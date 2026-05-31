"""Funnel analysis engine."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.database import EventRecord


def get_store_funnel(store_id: str, db: Session) -> dict[str, Any]:
    """Calculate conversion funnel based on sessions (visitor journeys)."""

    # Stage 1: ENTRY (distinct visitor_ids with ENTRY event)
    entry_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "ENTRY",
            EventRecord.is_staff == False,
        )
        .scalar()
        or 0
    )

    # Stage 2: ZONE_VISIT (entry visitors who also have ZONE_ENTER)
    zone_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "ZONE_ENTER",
            EventRecord.is_staff == False,
            EventRecord.visitor_id.in_(
                db.query(func.distinct(EventRecord.visitor_id)).filter(
                    EventRecord.store_id == store_id,
                    EventRecord.event_type == "ENTRY",
                    EventRecord.is_staff == False,
                )
            ),
        )
        .scalar()
        or 0
    )

    # Stage 3: BILLING_QUEUE (zone visitors who have BILLING_QUEUE_JOIN)
    queue_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.is_staff == False,
            EventRecord.visitor_id.in_(
                db.query(func.distinct(EventRecord.visitor_id)).filter(
                    EventRecord.store_id == store_id,
                    EventRecord.event_type == "ZONE_ENTER",
                    EventRecord.is_staff == False,
                )
            ),
        )
        .scalar()
        or 0
    )

    # Stage 4: PURCHASE (queue visitors who were converted)
    converted_visitors = _get_converted_visitors(store_id, db)

    # Calculate drop-off percentages
    entry_dropoff = 0.0
    zone_dropoff = _calc_dropoff(entry_visitors, zone_visitors)
    queue_dropoff = _calc_dropoff(zone_visitors, queue_visitors)
    purchase_dropoff = _calc_dropoff(queue_visitors, converted_visitors)

    return {
        "stages": [
            {"stage": "entry", "count": entry_visitors, "drop_off_pct": entry_dropoff},
            {"stage": "zone_visit", "count": zone_visitors, "drop_off_pct": zone_dropoff},
            {
                "stage": "billing_queue",
                "count": queue_visitors,
                "drop_off_pct": queue_dropoff,
            },
            {"stage": "purchase", "count": converted_visitors, "drop_off_pct": purchase_dropoff},
        ]
    }


def _calc_dropoff(current: int, previous: int) -> float:
    """Calculate drop-off percentage from current to previous stage."""
    if previous == 0:
        return 0.0
    return round((1 - current / previous) * 100, 2)


def _get_converted_visitors(store_id: str, db: Session) -> int:
    """Find visitors who converted (visited BILLING zone before POS transaction)."""
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
