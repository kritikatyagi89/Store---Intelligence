"""Domain models and pydantic schemas."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class EventType(str, enum.Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int


class StoreEvent(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float
    metadata: EventMetadata

    @field_validator("event_id")
    @classmethod
    def validate_uuid_v4(cls, value: str) -> str:
        parsed = uuid.UUID(value)
        if parsed.version != 4:
            raise ValueError("event_id must be a UUID v4")
        return value


class IngestRequest(BaseModel):
    events: List[StoreEvent] = Field(..., max_length=500)


class IngestResponse(BaseModel):
    accepted_count: int
    rejected_count: int
    errors: List[dict[str, str]] = Field(default_factory=list)
