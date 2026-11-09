from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fexor_server.models import DayStatus, Mode
from fexor_server.rules import ShiftSpec, compute_day, dedupe, shift_day_for

TZ = ZoneInfo("Asia/Kolkata")
DAY = ShiftSpec("day", time(9), time(17), frozenset(range(5)), grace_minutes=10, half_day_minutes=240)
NIGHT = ShiftSpec("night", time(22), time(6), frozenset(range(7)))
MON = date(2026, 11, 2)
LATER = datetime(2026, 12, 1, tzinfo=TZ)


def at(d: date, hh: int, mm: int = 0, tz=TZ) -> datetime:
    return datetime.combine(d, time(hh, mm), tz)


def day(events, *, shift=DAY, mode=Mode.IN_OUT, d=MON, holiday=False, leave=False, now=LATER, tz=TZ):
    return compute_day(day=d, tz=tz, mode=mode, shift=shift, events=events, is_holiday=holiday,
                       on_leave=leave, now=now)


def test_on_time_in_out():
    r = day([at(MON, 8, 55), at(MON, 17, 5)])
    assert r.status is DayStatus.PRESENT
    assert (r.worked_minutes, r.late_minutes, r.overtime_minutes) == (490, 0, 5)
    assert "overtime" in r.flags


def test_within_grace_is_not_late():
    assert day([at(MON, 9, 10), at(MON, 17)]).status is DayStatus.PRESENT


def test_late_counts_from_shift_start():
    r = day([at(MON, 9, 25), at(MON, 17)])
    assert r.status is DayStatus.LATE and r.late_minutes == 25


def test_half_day_and_early_leave():
    r = day([at(MON, 9), at(MON, 12)])
    assert r.status is DayStatus.HALF_DAY
    assert r.early_leave_minutes == 300 and "early_leave" in r.flags


def test_missing_out_only_after_window_closes():
    assert day([at(MON, 9)], now=at(MON, 18)).flags == []
    assert "missing_out" in day([at(MON, 9)]).flags


def test_single_mode_ignores_out():
    r = day([at(MON, 7, 50), at(MON, 15)], mode=Mode.SINGLE)
    assert r.status is DayStatus.PRESENT and r.last_out is None and r.worked_minutes == 0


def test_absent_only_when_day_complete():
    assert day([], now=at(MON, 12)) is None
    assert day([]).status is DayStatus.ABSENT


def test_holiday_leave_weekend():
    assert day([], holiday=True).status is DayStatus.HOLIDAY
    assert day([], leave=True).status is DayStatus.LEAVE
    sat = MON + timedelta(days=5)
    assert day([], d=sat) is None
    r = day([at(sat, 10), at(sat, 14)], d=sat)
    assert r.status is DayStatus.OFF and "worked_unscheduled" in r.flags


def test_no_shift_presence_only():
    assert day([at(MON, 3)], shift=None).status is DayStatus.PRESENT
    assert day([], shift=None) is None


def test_rescan_dedupe():
    evs = [at(MON, 9), at(MON, 9, 1), at(MON, 9, 3)]
    assert dedupe(evs, 2) == [at(MON, 9), at(MON, 9, 3)]
    r = day([at(MON, 9), at(MON, 9, 1)])
    assert r.last_out is None  # double tap is not a check-out


def test_night_shift_attribution_and_compute():
    lookup = lambda d: NIGHT  # noqa: E731
    tue = MON + timedelta(days=1)
    assert shift_day_for(at(MON, 21, 50), TZ, lookup) == MON
    assert shift_day_for(at(tue, 6, 5), TZ, lookup) == MON  # check-out next morning
    r = day([at(MON, 21, 58), at(tue, 6, 2)], shift=NIGHT)
    assert r.status is DayStatus.PRESENT and r.worked_minutes == 484


def test_attribution_outside_any_window_uses_local_date():
    lookup = lambda d: DAY  # noqa: E731
    assert shift_day_for(at(MON, 2), TZ, lookup) == MON


def test_dst_transition_day():
    ny = ZoneInfo("America/New_York")
    dst_day = date(2026, 3, 9)  # Monday after DST start (Mar 8 2026)
    r = day([at(dst_day, 9, tz=ny), at(dst_day, 17, tz=ny)], d=dst_day, tz=ny,
            now=datetime(2026, 4, 1, tzinfo=ny))
    assert r.worked_minutes == 480
    sun = date(2026, 3, 8)  # clocks jump at 02:00: 8h local shift = 7h real
    every_day = ShiftSpec("x", time(0), time(8), frozenset(range(7)), grace_minutes=0)
    r = day([at(sun, 0, tz=ny), at(sun, 8, tz=ny)], shift=every_day, d=sun, tz=ny,
            now=datetime(2026, 4, 1, tzinfo=ny))
    assert r.worked_minutes == 420


def test_leave_and_holiday_on_a_day_off_count_as_day_off():
    sat = MON + timedelta(days=5)
    assert day([], d=sat, leave=True) is None
    assert day([], d=sat, holiday=True) is None
    # Working anyway is still recorded.
    r = day([at(sat, 10)], d=sat, holiday=True)
    assert r.status is DayStatus.HOLIDAY and "worked_unscheduled" in r.flags


def test_autumn_dst_repeated_hour():
    ny = ZoneInfo("America/New_York")
    sun = date(2026, 11, 1)  # clocks go back 02:00 -> 01:00
    s = ShiftSpec("n", time(0), time(8), frozenset(range(7)), grace_minutes=0)
    r = compute_day(day=sun, tz=ny, mode=Mode.IN_OUT, shift=s,
                    events=[at(sun, 0, tz=ny), at(sun, 8, tz=ny)], is_holiday=False,
                    on_leave=False, now=datetime(2026, 12, 1, tzinfo=ny))
    assert r.worked_minutes == 9 * 60  # the repeated hour is real time worked


def test_very_early_check_in_belongs_to_next_days_shift():
    early = ShiftSpec("e", time(0, 30), time(8), frozenset(range(7)), early_window_minutes=120)
    ts = at(MON, 23, 0)  # 23:00 Monday, inside Tuesday 00:30's early window
    assert shift_day_for(ts, TZ, lambda d: early) == MON + timedelta(days=1)
