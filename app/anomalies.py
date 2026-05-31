"""Anomaly detection engine."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import EventRecord


def get_store_anomalies(store_id: str, db: Session) -> list[dict[str, Any]]:
    """Detect operational anomalies in store data."""
    anomalies = []

    # 1. BILLING_QUEUE_SPIKE
    queue_spike = _detect_queue_spike(store_id, db)
    if queue_spike:
        anomalies.append(queue_spike)

    # 2. CONVERSION_DROP
    conversion_drop = _detect_conversion_drop(store_id, db)
    if conversion_drop:
        anomalies.append(conversion_drop)

    # 3. DEAD_ZONE
    dead_zones = _detect_dead_zones(store_id, db)
    anomalies.extend(dead_zones)

    return anomalies


def _detect_queue_spike(store_id: str, db: Session) -> dict[str, Any] | None:
    """Detect if queue depth exceeds normal thresholds."""
    event = (
        db.query(EventRecord)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.is_staff == False,
        )
        .order_by(EventRecord.timestamp.desc())
        .first()
    )

    if not event or not event.metadata_:
        return None

    try:
        metadata = json.loads(event.metadata_)
        queue_depth = metadata.get("queue_depth", 0)
    except Exception:
        return None

    if queue_depth <= 5:
        return None

    severity = "CRITICAL" if queue_depth > 10 else "WARN"

    return {
        "anomaly_type": "BILLING_QUEUE_SPIKE",
        "severity": severity,
        "description": f"Queue depth is {queue_depth} (threshold: 5)",
        "suggested_action": "Deploy additional billing staff immediately",
    }


def _detect_conversion_drop(store_id: str, db: Session) -> dict[str, Any] | None:
    """Detect if conversion rate drops below baseline."""
    baseline = 0.15  # 15%

    # Count today's unique visitors
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    unique_visitors_today = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type != "REENTRY",
            EventRecord.timestamp >= today_start,
        )
        .scalar()
        or 0
    )

    if unique_visitors_today == 0:
        return None

    # Count converted visitors today
    converted_today = _get_converted_visitors_by_date(store_id, db, today_start)

    today_conversion_rate = converted_today / unique_visitors_today
    threshold = baseline * 0.8

    if today_conversion_rate >= threshold:
        return None

    return {
        "anomaly_type": "CONVERSION_DROP",
        "severity": "CRITICAL",
        "description": f"Conversion rate today is {today_conversion_rate:.2%} (baseline: {baseline:.0%})",
        "suggested_action": "Review floor staff positioning and promotion visibility",
    }


def _detect_dead_zones(store_id: str, db: Session) -> list[dict[str, Any]]:
    """Detect zones with no activity in the last 30 minutes."""
    anomalies = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = now - timedelta(minutes=30)

    # Get all zones
    all_zones = (
        db.query(func.distinct(EventRecord.zone_id))
        .filter(EventRecord.store_id == store_id, EventRecord.is_staff == False)
        .all()
    )

    for (zone_id,) in all_zones:
        if zone_id is None:
            continue

        # Get last ZONE_ENTER event for this zone
        last_event = (
            db.query(EventRecord)
            .filter(
                EventRecord.store_id == store_id,
                EventRecord.zone_id == zone_id,
                EventRecord.event_type == "ZONE_ENTER",
                EventRecord.is_staff == False,
            )
            .order_by(EventRecord.timestamp.desc())
            .first()
        )

        if not last_event or last_event.timestamp < window_start:
            anomalies.append(
                {
                    "anomaly_type": "DEAD_ZONE",
                    "severity": "INFO",
                    "description": f"Zone '{zone_id}' has no activity in last 30 minutes",
                    "suggested_action": "Check camera feed and consider zone repositioning",
                }
            )

    return anomalies


def _get_converted_visitors_by_date(store_id: str, db: Session, date_start: datetime) -> int:
    """Get count of converted visitors after a given date."""
    pos_file = Path(__file__).resolve().parent.parent / "data" / "pos_transactions.csv"

    if not pos_file.exists():
        return 0

    converted_visitor_ids = set()

    try:
        with open(pos_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["store_id"] != store_id:
                    continue

                transaction_time = datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")
                )

                if transaction_time < date_start:
                    continue

                time_window_start = transaction_time - timedelta(minutes=5)

                billing_visitors = (
                    db.query(func.distinct(EventRecord.visitor_id))
                    .filter(
                        EventRecord.store_id == store_id,
                        EventRecord.is_staff == False,
                        EventRecord.zone_id.contains("BILLING"),
                        EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
                        EventRecord.timestamp >= time_window_start,
                        EventRecord.timestamp < transaction_time,
                    )
                    .all()
                )

                for visitor_row in billing_visitors:
                    converted_visitor_ids.add(visitor_row.visitor_id)

    except Exception:
        pass

    return len(converted_visitor_ids)
