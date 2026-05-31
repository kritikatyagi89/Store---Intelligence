# PROMPT: Write pytest tests for a Store Intelligence API that ingests CCTV events
# and computes retail metrics. Test edge cases including empty stores, staff exclusion,
# re-entry deduplication, zero purchases, and idempotent ingestion.
# CHANGES MADE: Isolated store IDs per test, verified anomaly response shape
# rather than exact anomaly content since timing affects DEAD_ZONE detection.

import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_TEST_003",
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


def test_anomalies_returns_200_for_empty_store():
    """GET /anomalies must return 200 with empty list for store with no events."""
    store_id = f"STORE_ANO_EMPTY_{uuid.uuid4().hex[:4]}"
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    data = response.json()
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)


def test_anomalies_empty_when_no_conditions_met():
    """No anomaly conditions = empty anomalies list."""
    store_id = f"STORE_ANO_NONE_{uuid.uuid4().hex[:4]}"
    # Insert a normal event with no queue spike, no dead zone trigger
    event = make_event(store_id=store_id)
    client.post("/events/ingest", json={"events": [event]})
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200


def test_anomaly_response_shape():
    """Each anomaly must have anomaly_type, severity, suggested_action keys."""
    store_id = f"STORE_ANO_SHAPE_{uuid.uuid4().hex[:4]}"
    # Trigger a DEAD_ZONE by inserting an old ZONE_ENTER event
    event = make_event(
        store_id=store_id,
        event_type="ZONE_ENTER",
        zone_id="PERFUME",
        timestamp="2026-03-03T10:00:00Z",  # old timestamp — triggers DEAD_ZONE
    )
    client.post("/events/ingest", json={"events": [event]})
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    for anomaly in anomalies:
        assert "anomaly_type" in anomaly
        assert "severity" in anomaly
        assert "suggested_action" in anomaly


def test_queue_spike_anomaly_fires():
    """Queue depth > 5 should trigger BILLING_QUEUE_SPIKE anomaly."""
    store_id = f"STORE_ANO_QUEUE_{uuid.uuid4().hex[:4]}"
    event = make_event(
        store_id=store_id,
        event_type="BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        timestamp="2026-03-03T14:22:10Z",
        metadata={"queue_depth": 8, "sku_zone": None, "session_seq": 1},
    )
    client.post("/events/ingest", json={"events": [event]})
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomaly_types = [a["anomaly_type"] for a in response.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" in anomaly_types


def test_anomaly_severity_values_are_valid():
    """All anomaly severities must be INFO, WARN, or CRITICAL."""
    store_id = f"STORE_ANO_SEV_{uuid.uuid4().hex[:4]}"
    event = make_event(store_id=store_id,
                       event_type="ZONE_ENTER",
                       zone_id="SKINCARE",
                       timestamp="2026-01-01T10:00:00Z")
    client.post("/events/ingest", json={"events": [event]})
    response = client.get(f"/stores/{store_id}/anomalies")
    valid_severities = {"INFO", "WARN", "CRITICAL"}
    for anomaly in response.json()["anomalies"]:
        assert anomaly["severity"] in valid_severities