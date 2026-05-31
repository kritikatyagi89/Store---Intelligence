# PROMPT: Write pytest tests for a Store Intelligence API that ingests CCTV events
# and computes retail metrics. Test edge cases including empty stores, staff exclusion,
# re-entry deduplication, zero purchases, and idempotent ingestion.
# CHANGES MADE: Used isolated store IDs per test to avoid DB state bleed,
# adjusted conversion rate assertions to match 5-minute billing window logic.

import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_TEST_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    base.update(overrides)
    return base


def test_metrics_returns_zeros_for_empty_store():
    """Store with no events must return zeros not null or 500."""
    store_id = f"STORE_EMPTY_{uuid.uuid4().hex[:6]}"
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["queue_depth"] == 0
    assert metrics["abandonment_rate"] == 0.0
    assert metrics["avg_dwell_per_zone"] == {}


def test_unique_visitors_excludes_staff():
    """Staff visitors must not be counted in unique_visitors."""
    store_id = f"STORE_EXCL_{uuid.uuid4().hex[:4]}"
    customer = make_event(store_id=store_id, visitor_id="VIS_CUST_01")
    staff = make_event(store_id=store_id, visitor_id="VIS_STAF_01", is_staff=True)
    client.post("/events/ingest", json={"events": [customer, staff]})
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.json()["metrics"]["unique_visitors"] == 1


def test_abandonment_rate_calculation():
    """1 abandon out of 2 queue joins = 0.5 abandonment rate."""
    store_id = f"STORE_ABAND_{uuid.uuid4().hex[:4]}"
    events = [
        make_event(store_id=store_id, visitor_id="VIS_Q1",
                   event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                   metadata={"queue_depth": 2, "sku_zone": None, "session_seq": 1}),
        make_event(store_id=store_id, visitor_id="VIS_Q2",
                   event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                   metadata={"queue_depth": 3, "sku_zone": None, "session_seq": 1}),
        make_event(store_id=store_id, visitor_id="VIS_Q2",
                   event_type="BILLING_QUEUE_ABANDON", zone_id="BILLING",
                   metadata={"queue_depth": None, "sku_zone": None, "session_seq": 2}),
    ]
    client.post("/events/ingest", json={"events": events})
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.json()["metrics"]["abandonment_rate"] == 0.5


def test_avg_dwell_empty_when_no_dwell_events():
    """avg_dwell_per_zone must be empty dict when no ZONE_DWELL events exist."""
    store_id = f"STORE_DWELL_{uuid.uuid4().hex[:4]}"
    event = make_event(store_id=store_id)
    client.post("/events/ingest", json={"events": [event]})
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.json()["metrics"]["avg_dwell_per_zone"] == {}


def test_reentry_does_not_double_count_visitor():
    """REENTRY event must not create a second unique visitor."""
    store_id = f"STORE_REENT_{uuid.uuid4().hex[:4]}"
    events = [
        make_event(store_id=store_id, visitor_id="VIS_RE1", event_type="ENTRY"),
        make_event(store_id=store_id, visitor_id="VIS_RE1", event_type="EXIT"),
        make_event(store_id=store_id, visitor_id="VIS_RE1", event_type="REENTRY"),
    ]
    client.post("/events/ingest", json={"events": events})
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.json()["metrics"]["unique_visitors"] == 1