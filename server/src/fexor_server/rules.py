"""Attendance rules engine. Pure functions: no DB, no clock, fully unit-tested.

Shift windows and shift days live in fexor_core.schedule (the kiosk uses them too).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fexor_core.schedule import UTC, ShiftSpec, Window, shift_day_for, shift_window

from .models import DayStatus, Mode

__all__ = ["UTC", "ShiftSpec", "Window", "compute_day", "dedupe", "shift_day_for", "shift_window"]


def dedupe(events: list[datetime], min_gap_minutes: int) -> list[datetime]:
    """Drop re-scans closer than min_gap to the previous kept scan."""
    out: list[datetime] = []
    for ts in sorted(e.astimezone(UTC) for e in events):
        if not out or ts - out[-1] >= timedelta(minutes=min_gap_minutes):
            out.append(ts)
    return out


def _minutes(td: timedelta) -> int:
    return max(0, int(td.total_seconds() // 60))


@dataclass
class DayResult:
    status: DayStatus
    first_in: datetime | None = None
    last_out: datetime | None = None
    worked_minutes: int = 0
    late_minutes: int = 0
    early_leave_minutes: int = 0
    overtime_minutes: int = 0
    flags: list[str] = field(default_factory=list)


def compute_day(
    *,
    day: date,
    tz: ZoneInfo,
    mode: Mode | str,
    shift: ShiftSpec | None,
    events: list[datetime],
    is_holiday: bool,
    on_leave: bool,
    now: datetime,
    min_rescan_minutes: int = 2,
) -> DayResult | None:
    """Result for one person-day, or None when there is nothing to record yet."""
    ev = dedupe(events, min_rescan_minutes)
    r = DayResult(status=DayStatus.PRESENT)
    if ev:
        r.first_in = ev[0]
        if mode == Mode.IN_OUT and len(ev) >= 2:
            r.last_out = ev[-1]
            r.worked_minutes = _minutes(r.last_out - r.first_in)

    working = shift is not None and shift.works_on(day)
    if shift is not None and not working and not ev:
        # A holiday or leave on someone's day off is just a day off: counting it would
        # inflate leave/holiday totals (a 7-day leave is 5 working days for Mon-Fri staff).
        return None

    if is_holiday or on_leave or (shift is not None and not working):
        if not ev:
            if is_holiday:
                return DayResult(DayStatus.HOLIDAY)
            if on_leave:
                return DayResult(DayStatus.LEAVE)
            return None  # ordinary day off, nothing to store
        r.status = DayStatus.HOLIDAY if is_holiday else DayStatus.LEAVE if on_leave else DayStatus.OFF
        r.flags.append("worked_unscheduled")
        return r

    if shift is None:  # no schedule configured: presence only
        return r if ev else None

    w = shift_window(day, shift, tz)
    complete = now.astimezone(UTC) >= w.close
    if not ev:
        # With a check-in window, nobody can still check in once it closes.
        closed = complete or (shift.checkin_close_minutes is not None and now.astimezone(UTC)
                              > w.start + timedelta(minutes=shift.checkin_close_minutes))
        return DayResult(DayStatus.ABSENT) if closed else None

    if r.first_in > w.start + timedelta(minutes=shift.grace_minutes):
        r.late_minutes = _minutes(r.first_in - w.start)
        r.status = DayStatus.LATE
        r.flags.append("late")

    if mode == Mode.IN_OUT:
        if r.last_out is None:
            if complete:
                r.flags.append("missing_out")
        else:
            r.early_leave_minutes = _minutes(w.end - r.last_out)
            r.overtime_minutes = _minutes(r.last_out - w.end)
            if r.early_leave_minutes:
                r.flags.append("early_leave")
            if r.overtime_minutes:
                r.flags.append("overtime")
            if shift.half_day_minutes and r.worked_minutes < shift.half_day_minutes:
                r.status = DayStatus.HALF_DAY
    return r
