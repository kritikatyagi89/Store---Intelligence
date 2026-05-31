# PROMPT: Write pytest tests for a Store Intelligence API that ingests CCTV events
# and computes retail metrics. Test edge cases including empty stores, staff exclusion,
# re-entry deduplication, zero purchases, and idempotent ingestion.
# CHANGES MADE: Added TestClient fixture, used unique UUIDs per test to avoid
# cross-test contamination, added explicit staff exclusion assertion.

import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def make_event(**overrides):
    """Helper to build a valid event dict with defaults."""
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_TEST_001",
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


def test_valid_event_ingest_accepted():
    """A valid event should be accepted with accepted_count=1."""
    event = make_event()
    response = client.post("/events/ingest", json={"events": [event]})
    assert response.status_code == 200
    data = response.json()
    assert data["accepted_count"] == 1
    assert data["rejected_count"] == 0
    assert data["errors"] == []


def test_duplicate_event_id_rejected_not_500():
    """Sending the same event twice should reject the duplicate, not crash."""
    event = make_event()
    client.post("/events/ingest", json={"events": [event]})
    response = client.post("/events/ingest", json={"events": [event]})
    assert response.status_code == 200
    data = response.json()
    assert data["rejected_count"] == 1
    assert data["errors"][0]["reason"] == "duplicate event_id"


def test_staff_event_stored_but_excluded_from_metrics():
    """is_staff=True events must not appear in unique_visitors count."""
    store_id = f"STORE_STAFF_{uuid.uuid4().hex[:4]}"
    staff_event = make_event(store_id=store_id, is_staff=True, visitor_id="VIS_STAFF_01")
    client.post("/events/ingest", json={"events": [staff_event]})
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    assert response.json()["metrics"]["unique_visitors"] == 0


def test_batch_of_events_all_accepted():
    """A batch of 10 unique events should all be accepted."""
    events = [make_event() for _ in range(10)]
    response = client.post("/events/ingest", json={"events": events})
    assert response.status_code == 200
    assert response.json()["accepted_count"] == 10


def test_invalid_confidence_rejected():
    """Event with confidence > 1 should be rejected."""
    event = make_event(confidence=1.5)
    response = client.post("/events/ingest", json={"events": [event]})
    assert response.status_code == 200
    data = response.json()
    assert data["rejected_count"] == 1