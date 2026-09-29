"""Fan override router tests.

Signed semantics: positive minutes force the fan ON, negative force it OFF.
The sign is the desired state; the magnitude is the duration. Same sign extends
the window; the opposite sign replaces it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Event, FanOverride, Node
from app.routers.fan import MAX_OVERRIDE_MINUTES, _as_utc


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    # SQLite only autoincrements INTEGER PRIMARY KEY, not BIGINT.
    return "INTEGER"


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
        db.add(Node(node_id="disabled", name="Disabled", enabled=False))
        db.commit()

    with TestClient(app) as c:
        c.session_factory = TestingSession
        yield c

    app.dependency_overrides.clear()
    engine.dispose()


def _post(client, minutes, node_id="basement"):
    return client.post("/api/fan/override", json={"node_id": node_id, "minutes": minutes})


def _row(client, node_id="basement"):
    with client.session_factory() as db:
        row = db.get(FanOverride, node_id)
        if row is None:
            return None
        # Detach so callers get a snapshot, not a live identity-mapped object.
        db.expunge(row)
        return row


def _events(client, node_id="basement"):
    with client.session_factory() as db:
        return db.query(Event).filter(Event.node_id == node_id).all()


# --- positive = force ON ---

def test_plus_from_empty_forces_on(client):
    body = _post(client, 10).json()
    assert body["active"] is True
    assert body["desired_state"] is True
    assert 590 <= body["remaining_seconds"] <= 600
    assert _row(client).desired_state is True


def test_plus_extends_from_current_expiry(client):
    _post(client, 10)
    first = _row(client).expires_at
    assert _as_utc(first) > datetime.now(timezone.utc)
    _post(client, 10)
    second = _row(client).expires_at
    assert second > first
    assert 590 <= (second - first).total_seconds() <= 610


# --- negative = force OFF ---

def test_minus_from_empty_forces_off(client):
    body = _post(client, -10).json()
    assert body["active"] is True
    assert body["desired_state"] is False
    assert 590 <= body["remaining_seconds"] <= 600
    assert _row(client).desired_state is False


def test_minus_extends_off_window(client):
    _post(client, -10)
    first = _row(client).expires_at
    _post(client, -10)
    second = _row(client).expires_at
    assert second > first
    assert 590 <= (second - first).total_seconds() <= 610


# --- sign flip replaces the window ---

def test_flip_on_to_off_starts_fresh(client):
    _post(client, 30)
    body = _post(client, -10).json()
    assert body["desired_state"] is False
    # Fresh window from now, not 30m - 10m.
    assert 590 <= body["remaining_seconds"] <= 600


def test_flip_off_to_on_starts_fresh(client):
    _post(client, -30)
    body = _post(client, 10).json()
    assert body["desired_state"] is True
    assert 590 <= body["remaining_seconds"] <= 600


# --- cap ---

def test_plus_caps_at_24h(client):
    assert _post(client, 1440).json()["remaining_seconds"] <= MAX_OVERRIDE_MINUTES * 60
    assert _post(client, 1440).json()["remaining_seconds"] <= MAX_OVERRIDE_MINUTES * 60


def test_minus_caps_at_24h(client):
    assert _post(client, -1440).json()["remaining_seconds"] <= MAX_OVERRIDE_MINUTES * 60
    assert _post(client, -1440).json()["remaining_seconds"] <= MAX_OVERRIDE_MINUTES * 60


# --- GET / DELETE ---

def test_get_reflects_state(client):
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["active"] is False
    assert body["desired_state"] is None

    _post(client, 10)
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["active"] is True
    assert body["desired_state"] is True
    assert body["remaining_seconds"] > 0

    _post(client, -10)
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["desired_state"] is False


def test_get_expired_row_reports_inactive(client):
    with client.session_factory() as db:
        db.add(
            FanOverride(
                node_id="basement",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                desired_state=True,
            )
        )
        db.commit()
    body = client.get("/api/fan/override?node_id=basement").json()
    assert body["active"] is False
    assert body["desired_state"] is None
    assert body["remaining_seconds"] == 0


def test_delete_clears_override(client):
    _post(client, 10)
    r = client.delete("/api/fan/override?node_id=basement")
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["desired_state"] is None
    assert _row(client) is None


# --- validation ---

def test_unknown_node_rejected(client):
    assert _post(client, 10, node_id="nope").status_code == 400


def test_disabled_node_rejected(client):
    assert _post(client, 10, node_id="disabled").status_code == 403


def test_events_logged(client):
    _post(client, 10)
    _post(client, -10)
    messages = [e.message for e in _events(client)]
    assert "Fan override ON 10m" in messages
    assert "Fan override OFF 10m" in messages
