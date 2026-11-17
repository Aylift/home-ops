"""Fan schedule window evaluation.

Pure functions (no DB) so the window math is unit-testable. Times are local
wall-clock in the configured tz; the anchor date (the day the window *started*)
is resolved before date bounds and the weekday mask are checked, so overnight
windows carry their start day's weekday past midnight.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _occurrence_bounds(now_local: datetime, s) -> datetime | None:
    """If `now_local` falls inside schedule `s`, return its local end datetime.

    start_time == end_time is a 24h all-day window. end_time < start_time wraps
    past midnight; the anchor date is then yesterday when we are in the
    post-midnight tail, so the weekday/date checks apply to the start day.
    """
    t = now_local.time()
    today = now_local.date()

    if s.start_time == s.end_time:
        # All-day (24h) window, e.g. 00:00 -> 00:00
        anchor_date = today
        end_date = today + timedelta(days=1)
        end_time = time(0, 0)
    elif s.start_time < s.end_time:
        # Same-day window, e.g. 08:00 -> 18:00. A window ending at 23:59 (the
        # finest HTML time input resolution) is promoted to midnight so there
        # is no 1-minute nightly gap; membership and end instant both use it.
        end_time = s.end_time
        end_date = today
        if end_time == time(23, 59):
            end_time = time(0, 0)
            end_date = today + timedelta(days=1)
        # Membership: [start, end) normally; when promoted to midnight the
        # window covers the rest of the day, so only the lower bound applies.
        if t < s.start_time or (end_time != time(0, 0) and t >= end_time):
            return None
        anchor_date = today
    else:
        # Overnight window (end_time < start_time), e.g. 22:00 -> 06:00
        if t >= s.start_time:
            anchor_date = today
            end_date = today + timedelta(days=1)
        elif t < s.end_time:
            anchor_date = today - timedelta(days=1)
            end_date = today
        else:
            return None
        end_time = s.end_time

    if s.start_date and anchor_date < s.start_date:
        return None
    if s.end_date and anchor_date > s.end_date:
        return None
    if s.days_of_week and not (s.days_of_week & (1 << anchor_date.weekday())):
        return None

    return datetime.combine(end_date, end_time, tzinfo=now_local.tzinfo)


def active_block(schedules, now_utc: datetime, tz_name: str):
    """Return (schedule, seconds_until_end) for the longest active block, or None.

    Longest-match wins so overlapping blocks (daily + seasonal) report the
    block that actually governs the fan, not an arbitrary first row.
    """
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    best = None

    for s in schedules:
        if not s.enabled:
            continue
        end_local = _occurrence_bounds(now_local, s)
        if end_local is None:
            continue
        # Convert to UTC before subtracting: same-ZoneInfo subtraction is
        # wall-clock arithmetic and is off by 3600s across a DST boundary.
        secs = max(
            1, int((end_local.astimezone(timezone.utc) - now_utc).total_seconds())
        )
        if best is None or secs > best[1]:
            best = (s, secs)

    return best
