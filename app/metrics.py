"""Store metrics calculation engine."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import EventRecord


def get_store_metrics(store_id: str, db: Session) -> dict[str, Any]:
    """Calculate comprehensive metrics for a store."""

    # 1. Unique visitors (excluding staff and REENTRY events)
    unique_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type != "REENTRY",
        )
        .scalar()
        or 0
    )

    # 2. Conversion rate (visitors who visited BILLING zone before a POS transaction)
    converted_visitors = _get_converted_visitors(store_id, db)
    conversion_rate = (
        converted_visitors / unique_visitors if unique_visitors > 0 else 0.0
    )

    # 3. Avg dwell per zone
    dwell_rows = (
        db.query(
            EventRecord.zone_id,
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
        )
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "ZONE_DWELL",
            EventRecord.is_staff == False,
            EventRecord.zone_id != None,
        )
        .group_by(EventRecord.zone_id)
        .all()
    )
    avg_dwell_per_zone = {row.zone_id: int(row.avg_dwell) for row in dwell_rows}

    # 4. Queue depth (latest from BILLING_QUEUE_JOIN events)
    queue_depth = _get_latest_queue_depth(store_id, db)

    # 5. Abandonment rate
    abandonment_rate = _get_abandonment_rate(store_id, db)

    return {
        "unique_visitors": unique_visitors,
        "conversion_rate": round(conversion_rate, 4),
        "avg_dwell_per_zone": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": round(abandonment_rate, 4),
    }


def _get_converted_visitors(store_id: str, db: Session) -> int:
    """Find visitors who visited BILLING zones before POS transactions.
    
    Correlation rule: visitor must be in BILLING zone within 5 minutes
    BEFORE a POS transaction timestamp for the same store.
    """
    pos_file = Path(__file__).resolve().parent.parent / "data" / "pos_transactions.csv"

    if not pos_file.exists():
        return 0

    converted_visitor_ids: set[str] = set()

    try:
        with open(pos_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["store_id"].strip() != store_id:
                    continue

                # Strip timezone info so comparison works with naive DB timestamps
                transaction_time = datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")
                ).replace(tzinfo=None)

                time_window_start = transaction_time - timedelta(minutes=5)

                # Find visitors in BILLING zone within the 5-minute window
                billing_visitors = (
                    db.query(EventRecord.visitor_id)
                    .filter(
                        EventRecord.store_id == store_id,
                        EventRecord.is_staff == False,
                        EventRecord.zone_id.ilike("%BILLING%"),
                        EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL", "BILLING_QUEUE_JOIN"]),
                        EventRecord.timestamp >= time_window_start,
                        EventRecord.timestamp <= transaction_time,
                    )
                    .distinct()
                    .all()
                )

                for (visitor_id,) in billing_visitors:
                    converted_visitor_ids.add(visitor_id)

    except Exception as e:
        print(f"POS correlation error: {e}")

    return len(converted_visitor_ids)


def _get_latest_queue_depth(store_id: str, db: Session) -> int:
    """Get the most recent queue depth from BILLING_QUEUE_JOIN events."""
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
        return 0

    try:
        metadata = json.loads(event.metadata_)
        return metadata.get("queue_depth") or 0
    except Exception:
        return 0


def _get_abandonment_rate(store_id: str, db: Session) -> float:
    """Calculate BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN rate."""
    join_count = (
        db.query(func.count(EventRecord.event_id))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.is_staff == False,
        )
        .scalar()
        or 0
    )

    if join_count == 0:
        return 0.0

    abandon_count = (
        db.query(func.count(EventRecord.event_id))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_ABANDON",
            EventRecord.is_staff == False,
        )
        .scalar()
        or 0
    )

    return round(abandon_count / join_count, 4)


def get_store_heatmap(store_id: str, db: Session) -> dict[str, Any]:
    """Generate zone heatmap with visit frequency and dwell time, normalised 0-100."""

    # Count unique sessions for data_confidence flag
    session_count = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
        )
        .scalar()
        or 0
    )
    data_confidence = session_count >= 20

    # Aggregate visit frequency and avg dwell per zone
    zone_stats = (
        db.query(
            EventRecord.zone_id,
            func.count(EventRecord.event_id)
            .filter(EventRecord.event_type == "ZONE_ENTER")
            .label("visit_count"),
            func.avg(EventRecord.dwell_ms)
            .filter(EventRecord.event_type == "ZONE_DWELL")
            .label("avg_dwell"),
        )
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.zone_id != None,
        )
        .group_by(EventRecord.zone_id)
        .all()
    )

    heatmap: dict[str, Any] = {}
    max_visits = 0

    for zone_id, visit_count, avg_dwell in zone_stats:
        if zone_id is None:
            continue
        visits = visit_count or 0
        max_visits = max(max_visits, visits)
        heatmap[zone_id] = {
            "visit_frequency": visits,
            "avg_dwell_ms": int(avg_dwell) if avg_dwell else 0,
            "data_confidence": data_confidence,
            "normalised_score": 0.0,
        }

    # Normalise visit_frequency to 0-100 scale
    if max_visits > 0:
        for zone_id in heatmap:
            normalised = (heatmap[zone_id]["visit_frequency"] / max_visits) * 100
            heatmap[zone_id]["normalised_score"] = round(normalised, 2)

    return heatmap