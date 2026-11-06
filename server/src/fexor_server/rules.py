"""Attendance rules engine. Pure functions: no DB, no clock, fully unit-tested.

A "shift day" is the calendar date a shift starts on. An event belongs to the shift day
whose window [start - early_window, end + late_window] contains it, so overnight shifts
(22:00-06:00) attribute the 05:55 check-out to the previous date.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import DayStatus, Mode

UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class ShiftSpec:
    id: object
    start: time
    end: time
    weekdays: frozenset[int]  # 0 = Monday
    grace_minutes: int = 10
    early_window_minutes: int = 120
    late_window_minutes: int = 240
    half_day_minutes: int | None = None

    def works_on(self, d: date) -> bool:
        return d.weekday() in self.weekdays


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    open: datetime
    close: datetime


def shift_window(day: date, s: ShiftSpec, tz: ZoneInfo) -> Window:
    # Aware datetimes sharing a tzinfo subtract by wall clock in Python, which is wrong
    # across DST changes. All arithmetic below happens in UTC.
    start = datetime.combine(day, s.start, tz).astimezone(UTC)
    end_day = day + timedelta(days=1) if s.end <= s.start else day
    end = datetime.combine(end_day, s.end, tz).astimezone(UTC)
    return Window(
        start,
        end,
        start - timedelta(minutes=s.early_window_minutes),
        end + timedelta(minutes=s.late_window_minutes),
    )


ShiftLookup = Callable[[date], ShiftSpec | None]


def shift_day_for(ts: datetime, tz: ZoneInfo, shift_for: ShiftLookup) -> date:
    """Which shift day an event belongs to."""
    local = ts.astimezone(tz).date()
    # Today's window first, then yesterday's (overnight), then tomorrow's (very early check-in).
    for d in (local, local - timedelta(days=1), local + timedelta(days=1)):
        s = shift_for(d)
        if s and s.works_on(d):
            w = shift_window(d, s, tz)
            if w.open <= ts.astimezone(UTC) <= w.close:
                return d
    return local


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
        return DayResult(DayStatus.ABSENT) if complete else None

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
