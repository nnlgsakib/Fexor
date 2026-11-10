"""Shift windows, shared by the server (which decides) and the kiosk (which tells the person).

A "shift day" is the calendar date a shift starts on. An event belongs to the shift day
whose window [start - early_window, end + late_window] contains it, so overnight shifts
(22:00-06:00) attribute the 05:55 check-out to the previous date.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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
    # Enforced windows (None = any time). Check-in: from start - early_window until
    # start + checkin_close. Check-out: from end - checkout_open. Outside them a scan
    # needs an admin's approval.
    checkin_close_minutes: int | None = None
    checkout_open_minutes: int | None = None

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


def shift_id_on(assignments: list[tuple[date, object]], default: object, d: date) -> object:
    """The shift in effect on day d: the latest assignment from before d, else the default.
    assignments: (valid_from, shift_id), sorted ascending."""
    shift_id = default
    for valid_from, sid in assignments:
        if valid_from <= d:
            shift_id = sid
    return shift_id


def clock_text(t: datetime | time, time_format: str = "24h") -> str:
    """16:05 as "16:05" (24h) or "4:05 PM" (12h)."""
    if time_format != "12h":
        return f"{t.hour:02d}:{t.minute:02d}"
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def window_issue(ts: datetime, tz: ZoneInfo, shift_for: ShiftLookup, counted: list[datetime],
                 *, single: bool, min_gap_minutes: int) -> tuple[str, datetime] | None:
    """Why a verified scan falls outside its shift's check-in/out window, with the boundary
    it missed; None when it counts. counted: this person's scans that already count."""
    d = shift_day_for(ts, tz, shift_for)
    s = shift_for(d)
    if s is None or not s.works_on(d):
        return None
    t = ts.astimezone(UTC)
    earlier = [c.astimezone(UTC) for c in counted
               if c.astimezone(UTC) < t and shift_day_for(c, tz, shift_for) == d]
    if earlier and t - max(earlier) < timedelta(minutes=min_gap_minutes):
        return None  # a double tap: dropped as a re-scan anyway
    w = shift_window(d, s, tz)
    if not earlier:  # this is the check-in
        if s.checkin_close_minutes is None:
            return None
        if t < w.open:
            return "checkin_not_open", w.open
        closes = w.start + timedelta(minutes=s.checkin_close_minutes)
        return ("checkin_closed", closes) if t > closes else None
    if single or s.checkout_open_minutes is None:
        return None
    opens = w.end - timedelta(minutes=s.checkout_open_minutes)
    return ("checkout_not_open", opens) if t < opens else None
