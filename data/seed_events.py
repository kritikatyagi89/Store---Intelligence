"""Seed realistic events into Store Intelligence API for STORE_BLR_002."""
import json
import uuid
from datetime import datetime

import requests


API_URL = "http://localhost:8000/events/ingest"
STORE_ID = "STORE_BLR_002"
CONFIDENCE = 0.91


def generate_uuid_v4():
    """Generate a UUID v4 string."""
    return str(uuid.uuid4())


def create_event(
    visitor_id: str,
    event_type: str,
    timestamp: str,
    camera_id: str = None,
    zone_id: str = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    queue_depth: int = None,
    sku_zone: str = None,
):
    """Create a StoreEvent payload."""
    return {
        "event_id": generate_uuid_v4(),
        "store_id": STORE_ID,
        "camera_id": camera_id or "",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": CONFIDENCE,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": 1,
        },
    }


def seed_events():
    """Seed realistic event sequence."""
    all_events = []

    # Visitor 1: VIS_aaa111 - Full journey (converted)
    all_events.extend(
        [
            create_event("VIS_aaa111", "ENTRY", "2026-03-03T14:20:00Z", camera_id="CAM_ENTRY_01"),
            create_event("VIS_aaa111", "ZONE_ENTER", "2026-03-03T14:21:00Z", zone_id="SKINCARE"),
            create_event("VIS_aaa111", "ZONE_DWELL", "2026-03-03T14:21:30Z", zone_id="SKINCARE", dwell_ms=35000),
            create_event("VIS_aaa111", "ZONE_ENTER", "2026-03-03T14:33:00Z", zone_id="BILLING"),
            create_event("VIS_aaa111", "BILLING_QUEUE_JOIN", "2026-03-03T14:33:30Z", zone_id="BILLING", queue_depth=2),
            create_event("VIS_aaa111", "EXIT", "2026-03-03T14:39:00Z"),
        ]
    )

    # Visitor 2: VIS_bbb222 - Abandoned billing queue
    all_events.extend(
        [
            create_event("VIS_bbb222", "ENTRY", "2026-03-03T14:25:00Z", camera_id="CAM_ENTRY_01"),
            create_event("VIS_bbb222", "ZONE_ENTER", "2026-03-03T14:26:00Z", zone_id="HAIRCARE"),
            create_event("VIS_bbb222", "ZONE_DWELL", "2026-03-03T14:26:30Z", zone_id="HAIRCARE", dwell_ms=45000),
            create_event("VIS_bbb222", "ZONE_ENTER", "2026-03-03T14:35:00Z", zone_id="BILLING"),
            create_event("VIS_bbb222", "BILLING_QUEUE_JOIN", "2026-03-03T14:35:30Z", zone_id="BILLING", queue_depth=4),
            create_event("VIS_bbb222", "BILLING_QUEUE_ABANDON", "2026-03-03T14:37:00Z", zone_id="BILLING"),
            create_event("VIS_bbb222", "EXIT", "2026-03-03T14:37:30Z"),
        ]
    )

    # Visitor 3: VIS_ccc333 - Staff member (should be excluded)
    all_events.extend(
        [
            create_event("VIS_ccc333", "ENTRY", "2026-03-03T14:22:00Z", is_staff=True),
            create_event("VIS_ccc333", "ZONE_ENTER", "2026-03-03T14:23:00Z", zone_id="BILLING", is_staff=True),
            create_event("VIS_ccc333", "EXIT", "2026-03-03T14:50:00Z", is_staff=True),
        ]
    )

    # Visitor 4: VIS_ddd444 - Re-entry case
    all_events.extend(
        [
            create_event("VIS_ddd444", "ENTRY", "2026-03-03T14:28:00Z"),
            create_event("VIS_ddd444", "EXIT", "2026-03-03T14:30:00Z"),
            create_event("VIS_ddd444", "REENTRY", "2026-03-03T14:32:00Z"),
            create_event("VIS_ddd444", "ZONE_ENTER", "2026-03-03T14:33:00Z", zone_id="SKINCARE"),
            create_event("VIS_ddd444", "EXIT", "2026-03-03T14:45:00Z"),
        ]
    )

    # Send all events in a single batch
    payload = {"events": all_events}
    print(f"Seeding {len(all_events)} events to {API_URL}...")

    try:
        response = requests.post(API_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()

        print(f"\n✓ Seed completed!")
        print(f"  Accepted: {result.get('accepted_count', 0)}")
        print(f"  Rejected: {result.get('rejected_count', 0)}")

        if result.get("errors"):
            print(f"\n  Errors:")
            for error in result["errors"]:
                print(f"    - {error}")

    except requests.exceptions.ConnectionError:
        print("✗ Connection failed. Is the API running on http://localhost:8000?")
        print("  Start with: uvicorn app.main:app --reload --port 8000")
    except Exception as e:
        print(f"✗ Error: {e}")


if __name__ == "__main__":
    seed_events()
