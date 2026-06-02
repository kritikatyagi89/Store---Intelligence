"""Funnel analysis engine."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.database import EventRecord


def _visitor_ids_with_event(
    store_id: str,
    db: Session,
    event_type: str,
    *,
    restrict_to: set[str] | None = None,
) -> set[str]:
    """Distinct non-staff visitor_ids that have at least one event of the given type."""
    query = (
        db.query(EventRecord.visitor_id)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == event_type,
            EventRecord.is_staff == False,
        )
        .distinct()
    )
    if restrict_to is not None:
        if not restrict_to:
            return set()
        query = query.filter(EventRecord.visitor_id.in_(restrict_to))

    return {row[0] for row in query.all()}


def _get_converted_visitor_ids(store_id: str, db: Session) -> set[str]:
    """Visitors with billing-zone activity within 5 minutes before a POS transaction."""
    pos_file = Path(__file__).resolve().parent.parent / "data" / "pos_transactions.csv"

    if not pos_file.exists():
        return set()

    converted: set[str] = set()

    try:
        with open(pos_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["store_id"].strip() != store_id:
                    continue

                transaction_time = datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
                time_window_start = transaction_time - timedelta(minutes=5)

                billing_visitors = (
                    db.query(EventRecord.visitor_id)
                    .filter(
                        EventRecord.store_id == store_id,
                        EventRecord.is_staff == False,
                        EventRecord.visitor_id.isnot(None),
                        EventRecord.timestamp >= time_window_start,
                        EventRecord.timestamp <= transaction_time,
                    )
                    .filter(
                        or_(
                            EventRecord.event_type == "BILLING_QUEUE_JOIN",
                            and_(
                                EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
                                or_(
                                    EventRecord.zone_id.ilike("%BILLING%"),
                                    EventRecord.zone_id == "CASH_COUNTER",
                                ),
                            ),
                        )
                    )
                    .distinct()
                    .all()
                )

                for (visitor_id,) in billing_visitors:
                    converted.add(visitor_id)

    except Exception:
        pass

    return converted


def _calc_dropoff(current: int, previous: int) -> float:
    """Drop-off percentage from previous stage to current (0 if previous is 0)."""
    if previous == 0:
        return 0.0
    return round((1 - current / previous) * 100, 2)


def get_store_funnel(store_id: str, db: Session) -> dict[str, Any]:
    """Session funnel: each stage is a subset of the previous by distinct visitor_id."""

    # Stage 1: distinct visitors with ENTRY (non-staff)
    entry_ids = _visitor_ids_with_event(store_id, db, "ENTRY")

    # Stage 2: entry visitors who also have any ZONE_ENTER
    zone_ids = entry_ids & _visitor_ids_with_event(store_id, db, "ZONE_ENTER")

    # Stage 3: zone visitors who also have BILLING_QUEUE_JOIN (distinct visitors, not events)
    queue_ids = zone_ids & _visitor_ids_with_event(store_id, db, "BILLING_QUEUE_JOIN")

    # Stage 4: queue visitors who converted via POS correlation
    purchase_ids = queue_ids & _get_converted_visitor_ids(store_id, db)

    entry_count = len(entry_ids)
    zone_count = len(zone_ids)
    queue_count = len(queue_ids)
    purchase_count = len(purchase_ids)

    return {
        "stages": [
            {"stage": "entry", "count": entry_count, "drop_off_pct": 0.0},
            {
                "stage": "zone_visit",
                "count": zone_count,
                "drop_off_pct": _calc_dropoff(zone_count, entry_count),
            },
            {
                "stage": "billing_queue",
                "count": queue_count,
                "drop_off_pct": _calc_dropoff(queue_count, zone_count),
            },
            {
                "stage": "purchase",
                "count": purchase_count,
                "drop_off_pct": _calc_dropoff(purchase_count, queue_count),
            },
        ]
    }
