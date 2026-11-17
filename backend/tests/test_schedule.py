"""Fan schedule window math + override projection tests."""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import FanSchedule, Node
from app.services.schedule import _occurrence_bounds, active_block

TZ = "Europe/Warsaw"
WARSAW = ZoneInfo(TZ)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


def _sched(**kw):
    base = dict(
        enabled=True,
        start_date=None,
        end_date=None,
        start_time=time(0, 0),
        end_time=time(0, 0),
        days_of_week=0,
        desired_state=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=WARSAW)


# --- _occurrence_bounds ---

def test_all_day_zero_to_zero_is_24h():
    s = _sched(start_time=time(0, 0), end_time=time(0, 0))
    end = _occurrence_bounds(_local(2026, 11, 17, 12), s)
    assert end == _local(2026, 11, 18, 0)


def test_zero_to_2359_has_no_nightly_gap():
    s = _sched(start_time=time(0, 0), end_time=time(23, 59))
    # 23:59:30 must still be inside the window.
    assert _occurrence_bounds(_local(2026, 11, 17, 23, 59), s) is not None


def test_same_day_window():
    s = _sched(start_time=time(8, 0), end_time=time(18, 0))
    assert _occurrence_bounds(_local(2026, 11, 17, 12), s) == _local(2026, 11, 17, 18)
    assert _occurrence_bounds(_local(2026, 11, 17, 19), s) is None


def test_overnight_carries_start_weekday_past_midnight():
    # Mon-Fri 22:00-06:00. Friday 23:00 -> active; Saturday 00:10 -> still active.
    s = _sched(
        start_time=time(22, 0),
        end_time=time(6, 0),
        days_of_week=0b0011111,  # Mon..Fri
    )
    fri = date(2026, 11, 20)  # Friday
    assert _occurrence_bounds(_local(fri.year, fri.month, fri.day, 23), s) is not None
    sat = fri + timedelta(days=1)
    end = _occurrence_bounds(_local(sat.year, sat.month, sat.day, 0, 10), s)
    assert end == _local(sat.year, sat.month, sat.day, 6)


def test_overnight_does_not_fire_on_start_date_morning():
    s = _sched(
        start_time=time(22, 0),
        end_time=time(6, 0),
        start_date=date(2026, 10, 1),
    )
    # 01:00 on 2026-10-01 is the tail of a window that would have started 09-30.
    assert _occurrence_bounds(_local(2026, 10, 1, 1), s) is None
    assert _occurrence_bounds(_local(2026, 10, 1, 23), s) is not None


def test_end_date_bounds_anchor_date():
    s = _sched(
        start_time=time(22, 0),
        end_time=time(6, 0),
        end_date=date(2026, 10, 1),
    )
    # Window starting 10-01 22:00 is allowed; its tail on 10-02 01:00 too.
    assert _occurrence_bounds(_local(2026, 10, 1, 23), s) is not None
    assert _occurrence_bounds(_local(2026, 10, 2, 1), s) is not None
    assert _occurrence_bounds(_local(2026, 10, 2, 23), s) is None


def test_disabled_never_matches():
    s = _sched(enabled=False)
    assert active_block([s], datetime.now(timezone.utc), TZ) is None


# --- active_block ---

def test_active_block_returns_seconds_to_end():
    s = _sched(start_time=time(8, 0), end_time=time(18, 0))
    now = _local(2026, 11, 17, 12).astimezone(timezone.utc)
    hit = active_block([s], now, TZ)
    assert hit is not None
    assert hit[1] == 6 * 3600


def test_active_block_longest_match_wins():
    daily = _sched(start_time=time(0, 0), end_time=time(0, 0))  # 24h
    short = _sched(start_time=time(8, 0), end_time=time(18, 0))
    now = _local(2026, 11, 17, 12).astimezone(timezone.utc)
    hit = active_block([short, daily], now, TZ)
    assert hit[0] is daily


def test_active_block_dst_boundary_seconds():
    # 2026-10-25 03:00 CEST -> 02:00 CET. Window 00:00-00:00 spans 25 hours.
    s = _sched(start_time=time(0, 0), end_time=time(0, 0))
    now = _local(2026, 10, 25, 0, 0).astimezone(timezone.utc)
    hit = active_block([s], now, TZ)
    assert hit is not None
    assert hit[1] == 25 * 3600


# --- API ---

@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestingSession() as db:
        db.add(Node(node_id="basement", name="Basement", enabled=True))
        db.commit()

    with TestClient(app) as c:
        c.session_factory = TestingSession
        yield c

    app.dependency_overrides.clear()
    engine.dispose()


def _always_on_block(client):
    return client.post(
        "/api/fan/schedule",
        json={
            "node_id": "basement",
            "name": "Heating season",
            "start_time": "00:00:00",
            "end_time": "00:00:00",
        },
    )


def test_schedule_crud(client):
    r = _always_on_block(client)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["desired_state"] is False

    assert len(client.get("/api/fan/schedule?node_id=basement").json()) == 1

    r = client.patch(f"/api/fan/schedule/{sid}", json={"enabled": False})
    assert r.json()["enabled"] is False

    assert client.delete(f"/api/fan/schedule/{sid}").status_code == 204
    assert client.get("/api/fan/schedule?node_id=basement").json() == []


def test_patch_can_clear_dates(client):
    r = client.post(
        "/api/fan/schedule",
        json={
            "node_id": "basement",
            "name": "Season",
            "start_date": "2026-10-01",
            "end_date": "2027-04-30",
            "start_time": "00:00:00",
            "end_time": "00:00:00",
        },
    )
    sid = r.json()["id"]
    r = client.patch(f"/api/fan/schedule/{sid}", json={"start_date": None})
    assert r.json()["start_date"] is None
    assert r.json()["end_date"] == "2027-04-30"


def test_invalid_date_range_rejected(client):
    r = client.post(
        "/api/fan/schedule",
        json={
            "node_id": "basement",
            "name": "Bad",
            "start_date": "2026-10-01",
            "end_date": "2026-04-30",
            "start_time": "00:00:00",
            "end_time": "00:00:00",
        },
    )
    assert r.status_code == 422


def test_override_reports_schedule_source(client):
    _always_on_block(client)
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["active"] is True
    assert body["desired_state"] is False
    assert body["source"] == "schedule"
    assert body["remaining_seconds"] > 0


def test_manual_override_beats_schedule(client):
    _always_on_block(client)
    client.post("/api/fan/override", json={"node_id": "basement", "minutes": 10})
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["source"] == "manual"
    assert body["desired_state"] is True


def test_no_schedule_no_override(client):
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["active"] is False
    assert body["source"] is None
