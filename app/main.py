from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import database
from app.anomalies import get_store_anomalies
from app.database import EventRecord, get_db
from app.funnel import get_store_funnel
from app.ingestion import ingest_events
from app.metrics import get_store_heatmap, get_store_metrics
from app.models import IngestRequest, IngestResponse

logger = logging.getLogger("store_intelligence")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    database.create_tables()
    yield


app = FastAPI(title="Store Intelligence API", lifespan=lifespan)


@app.post("/events/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    store_id = request.events[0].store_id if request.events else None
    result = ingest_events(request.events, db)

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    status_code = 200
    logger.info(
        json.dumps(
            {
                "trace_id": trace_id,
                "store_id": store_id,
                "endpoint": "/events/ingest",
                "event_count": len(request.events),
                "latency_ms": latency_ms,
                "status_code": status_code,
            }
        )
    )

    return IngestResponse(
        accepted_count=result["accepted"],
        rejected_count=result["rejected"],
        errors=result["errors"],
    )


@app.get("/stores/{store_id}/metrics")
async def get_metrics(store_id: str, db: Session = Depends(get_db)):
    metrics = get_store_metrics(store_id, db)
    return {"store_id": store_id, "metrics": metrics}


@app.get("/stores/{store_id}/funnel")
async def get_funnel(store_id: str, db: Session = Depends(get_db)):
    funnel = get_store_funnel(store_id, db)
    return {"store_id": store_id, "funnel": funnel}


@app.get("/stores/{store_id}/heatmap")
async def get_heatmap(store_id: str, db: Session = Depends(get_db)):
    heatmap = get_store_heatmap(store_id, db)
    return {"store_id": store_id, "heatmap": heatmap}


@app.get("/stores/{store_id}/anomalies")
async def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    anomalies = get_store_anomalies(store_id, db)
    return {"store_id": store_id, "anomalies": anomalies}


@app.get("/health")
async def health(db: Session = Depends(get_db)):
    rows = (
        db.query(EventRecord.store_id, func.max(EventRecord.timestamp).label("last_timestamp"))
        .group_by(EventRecord.store_id)
        .all()
    )
    now = datetime.utcnow()
    last_event_per_store = {
        row.store_id: row.last_timestamp.isoformat() + "Z" for row in rows
    }
    warnings = []

    for row in rows:
        if row.last_timestamp is None:
            continue
        if now - row.last_timestamp > timedelta(minutes=10):
            warnings.append(
                f"STALE_FEED: store_id={row.store_id} last event at {row.last_timestamp.isoformat()}Z"
            )

    return {
        "status": "ok",
        "last_event_per_store": last_event_per_store,
        "stale_feed_warnings": warnings,
    }
