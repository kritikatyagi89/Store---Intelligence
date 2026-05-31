"""Event ingestion helpers."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.database import EventRecord
from app.models import StoreEvent


def ingest_events(events: list[StoreEvent], db: Session) -> dict[str, Any]:
    accepted = 0
    rejected = 0
    errors: list[dict[str, str]] = []

    for event in events:
        try:
            existing = db.get(EventRecord, event.event_id)
            if existing is not None:
                rejected += 1
                errors.append({"event_id": event.event_id, "reason": "duplicate event_id"})
                continue

            if not 0.0 <= event.confidence <= 1.0:
                rejected += 1
                errors.append({"event_id": event.event_id, "reason": "confidence must be between 0 and 1"})
                continue

            db_event = EventRecord(
                event_id=event.event_id,
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                event_type=event.event_type.value,
                timestamp=event.timestamp,
                zone_id=event.zone_id,
                dwell_ms=event.dwell_ms,
                is_staff=event.is_staff,
                confidence=event.confidence,
                metadata_=json.dumps(event.metadata.model_dump()),
            )
            db.add(db_event)
            accepted += 1
        except Exception as exc:
            rejected += 1
            errors.append({"event_id": event.event_id, "reason": str(exc)})

    return {"accepted": accepted, "rejected": rejected, "errors": errors}
