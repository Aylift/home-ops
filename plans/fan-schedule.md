# Fan Schedule (heating-season lockout)

> **Rev 2** — incorporates review: all-day windows, overnight anchor-date
> handling, DST-safe seconds, longest-match selection, `desired_state` column,
> PATCH `exclude_unset`, date-range validation.

## Problem

Basement furnace backdrafts smoke when the exhaust fan runs. Two mitigations:
reverse the fan (hardware) or **disable ventilation during heating season**.
This plan implements the software option: a calendar of **block windows** in
which the fan is forced OFF regardless of climate logic.

## Design decisions

1. **Backend is the schedule authority** (same pattern as weather + override).
   The device never stores a calendar; it polls one endpoint per cycle.
2. **Reuse the override channel.** The existing `GET /api/fan/override`
   already returns signed remaining seconds (positive = ON, negative = OFF).
   A schedule block is just a *synthetic negative override*: when "now" falls
   inside a block window, the endpoint returns `desired_state=false` with
   `remaining_seconds = seconds until block end`. **No device change needed**
   for the core feature — the device already forces OFF on negative values.
3. **Precedence:** manual override > schedule block > climate logic.
   A manual ON override must be able to defeat the schedule (user is present
   and explicitly wants air), but a manual OFF is redundant with a block.
   Implemented by checking the manual row first; only if inactive do we
   evaluate the schedule.
4. **Timezone:** blocks are stored as **local wall-clock** `start`/`end`
   (`HH:MM`) plus an ISO date range, evaluated in a configurable IANA tz
   (`Europe/Warsaw`). Storing UTC instants would break across DST and make
   "every day 18:00–08:00" awkward. Use `zoneinfo`.
5. **Recurrence:** keep it minimal — a block has `start_date`, `end_date`
   (inclusive, nullable = open-ended), `start_time`, `end_time`, and a
   `days_of_week` bitmask (0 = every day). Overnight windows (end < start)
   wrap past midnight. This covers "heating season = Oct 1 → Apr 30, all
   day" and "nights only" without a full RRULE engine.
6. **Fail-open:** if the schedule table is empty or the query errors, the
   endpoint behaves exactly as today (no block). The device's existing
   "override failure → 0" fallback already covers backend downtime.

## Data model

New table `fan_schedules` (one row per block, many per node):

| column        | type            | notes                                        |
|---------------|-----------------|----------------------------------------------|
| id            | int PK          |                                              |
| node_id       | str(64)         | FK-ish to nodes.node_id                      |
| name          | str(128)        | e.g. "Heating season"                        |
| enabled       | bool            | soft toggle without deleting                 |
| start_date    | date, nullable  | null = no lower bound                        |
| end_date      | date, nullable  | null = open-ended                            |
| start_time    | time            | local wall clock                             |
| end_time      | time            | local; < start_time means overnight wrap     |
| days_of_week  | int, default 0  | bitmask Mon=1 … Sun=64; 0 = every day        |
| desired_state | bool, default F | False = force OFF (block); True = force ON   |
| created_at    | timestamptz     | server default now()                         |

`desired_state` is added now (default False) even though the UI only exposes
OFF blocks, so a future summer-purge ON schedule needs no migration.

## API

- `GET /api/fan/schedule?node_id=basement` → `list[FanScheduleOut]`
- `POST /api/fan/schedule` → create, returns `FanScheduleOut`
- `PATCH /api/fan/schedule/{id}` → partial update (enable/disable, edit)
- `DELETE /api/fan/schedule/{id}` → remove
- `GET /api/fan/override` → **extended**: when no manual override is active,
  evaluate schedules; if inside a block, return
  `active=true, desired_state=<block.desired_state>,
  remaining_seconds=<secs to block end>, source="schedule"`. Add
  `source: "manual" | "schedule" | null` to `FanOverrideOut` so the dashboard
  can label it.

## Backend snippets

### `backend/app/models.py` (append)

```python
from datetime import date, time

class FanSchedule(Base):
    """Recurring window in which the fan is forced to `desired_state`.

    Times are local wall-clock in settings.schedule_tz. start_time == end_time
    is a 24h all-day window; end_time < start_time wraps past midnight.
    days_of_week is a bitmask (Mon=1 … Sun=64); 0 means every day. Null dates
    are open-ended. desired_state False = force OFF (heating-season lockout).
    """
    __tablename__ = "fan_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    days_of_week: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    desired_state: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Add `Date, Time` to the `sqlalchemy` import.

### `backend/app/schemas.py` (append)

```python
from datetime import date, time

from pydantic import model_validator

class FanScheduleIn(BaseModel):
    node_id: str = Field(default="basement", pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_time: time
    end_time: time
    days_of_week: int = Field(default=0, ge=0, le=127)
    desired_state: bool = False

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self

class FanScheduleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    days_of_week: Optional[int] = Field(default=None, ge=0, le=127)
    desired_state: Optional[bool] = None

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self

class FanScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    node_id: str
    name: str
    enabled: bool
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_time: time
    end_time: time
    days_of_week: int
    desired_state: bool
    created_at: datetime
```

Extend `FanOverrideOut` with `source: Optional[str] = None`.

PATCH endpoint must use `payload.model_dump(exclude_unset=True)` (NOT
`exclude_none=True`) so a client can clear `start_date`/`end_date` back to null.

### `backend/app/services/schedule.py` (new)

Pure function, unit-testable, no DB:

```python
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _occurrence_bounds(now_local: datetime, s) -> datetime | None:
    """If `now_local` falls inside schedule `s`, return its local end datetime.

    The anchor date (the day the window *started*) is resolved first, then
    date bounds and the weekday mask are checked against it — so an overnight
    window that began Friday 22:00 is still valid at Saturday 00:10, and a
    window starting 2026-10-01 22:00 does not fire on the morning of 10-01.
    start_time == end_time is treated as a 24h all-day window.
    """
    t = now_local.time()
    today = now_local.date()

    if s.start_time == s.end_time:
        # All-day (24h) window, e.g. 00:00 -> 00:00
        anchor_date = today
        end_date = today + timedelta(days=1)
        end_time = time(0, 0)
    elif s.start_time < s.end_time:
        # Same-day window, e.g. 08:00 -> 18:00
        if not (s.start_time <= t < s.end_time):
            return None
        anchor_date = today
        end_date = today
        end_time = s.end_time
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
        secs = max(1, int((end_local.astimezone(timezone.utc) - now_utc).total_seconds()))
        if best is None or secs > best[1]:
            best = (s, secs)

    return best
```

### `backend/app/routers/fan.py` — modify `get_override`

```python
from app.services.schedule import active_block
from app.models import FanSchedule

@router.get("/override", response_model=FanOverrideOut)
def get_override(db: DbDep, node_id=...):
    row = db.get(FanOverride, node_id)
    remaining = _remaining(row)
    if remaining > 0:
        return FanOverrideOut(
            node_id=node_id, active=True, desired_state=row.desired_state,
            expires_at=row.expires_at, remaining_seconds=remaining,
            source="manual",
        )
    # No manual override: fall through to the schedule (forced OFF).
    schedules = db.scalars(
        select(FanSchedule).where(
            FanSchedule.node_id == node_id, FanSchedule.enabled.is_(True)
        )
    ).all()
    hit = active_block(schedules, datetime.now(timezone.utc), settings.schedule_tz)
    if hit is not None:
        sched, secs = hit
        return FanOverrideOut(
            node_id=node_id, active=True, desired_state=sched.desired_state,
            expires_at=None, remaining_seconds=secs, source="schedule",
        )
    return FanOverrideOut(
        node_id=node_id, active=False, desired_state=None,
        expires_at=None, remaining_seconds=0, source=None,
    )
```

Add `schedule_tz: str = "Europe/Warsaw"` to `Settings`.

### `backend/alembic/versions/0004_fan_schedules.py` (new)

```python
revision = "0004_fan_schedules"
down_revision = "0003_fan_override_desired_state"

def upgrade():
    op.create_table(
        "fan_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("days_of_week", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("desired_state", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

def downgrade():
    op.drop_table("fan_schedules")
```

## Device

**No change required.** [`fetch_override()`](iot/basement/climate.py:68) already
returns negative seconds → `vent_decision = override_secs > 0` → fan OFF, and
the mode string renders `OVERRIDE OFF (Nm left)`. Optional cosmetic: the
device could distinguish `source="schedule"` and print `SCHEDULE (Nm left)`,
but that is a one-line `mode_reason` tweak and not required for correctness.

## Frontend

New card "Fan schedule" in [`App.vue`](frontend/src/App.vue:414) (below the
fan card): list blocks with name, date range, time, days, enabled toggle,
delete; a small form to add a block (name, start/end date, start/end time,
days checkboxes). Show `source` on the override badge so a schedule-driven
OFF reads "Schedule OFF 3h 12m" instead of "Manual".

i18n keys under `schedule.*` in [`en.js`](frontend/src/i18n/locales/en.js:13)
and [`pl.js`](frontend/src/i18n/locales/pl.js).

## Tests

- `backend/tests/test_schedule.py`:
  - `_occurrence_bounds`: same-day, overnight wrap (Fri 22:00 → Sat 00:10
    still active, weekday checked against Friday), all-day `00:00–00:00`,
    `00:00–23:59` has no nightly gap, `start_date` morning does not fire.
  - `active_block`: returns secs to end, None when outside, longest-match
    when two blocks overlap, DST-boundary seconds correct.
  - `GET /api/fan/override` returns `source="schedule"` with the block's
    `desired_state` when active and no manual override; manual override wins.
  - `PATCH` can clear `start_date`/`end_date` to null (`exclude_unset`).
  - `FanScheduleIn` rejects `end_date < start_date`.

## Rollout

1. `alembic upgrade head` (0004).
2. Restart backend.
3. Create the heating-season block via dashboard (or `POST /api/fan/schedule`).
4. Verify `GET /api/fan/override?node_id=basement` returns
   `source="schedule"` inside the window.
5. No device push/reset needed.

## Resolved decisions

- **Manual ON defeats a schedule block** (manual > schedule). A time-bounded
  manual ON (30–60 min) expires back into the lockout, so the user never has
  to remember to re-enable the schedule.
- **Single global `schedule_tz`** (`Europe/Warsaw`). Per-block tz is noise.
- **`desired_state` column added now** (default False) so a future summer
  purge ON schedule needs no migration.
