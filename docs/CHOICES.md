# Architecture Decision Records — CHOICES.md

## Decision 1 — Detection Model Selection

**The Problem:** Choose a person detection model for processing 1080p retail CCTV footage at 15fps with known edge cases: partial occlusion, group entry, and staff movement.

**Options Considered:**
- YOLOv8n — fast, lightweight, excellent Python ecosystem, pretrained on COCO
- YOLOv8m — better accuracy than nano, 3x slower inference
- RT-DETR — transformer-based, state-of-the-art on occluded scenes, high memory usage
- MediaPipe — designed for mobile, poor performance on overhead camera angles

**What AI Suggested:** Claude recommended RT-DETR because the challenge explicitly mentions partial occlusion in the billing queue clip as a known hard case, and transformer attention mechanisms handle occlusion better than YOLO's anchor-based approach.

**What I Chose:** YOLOv8n with ByteTrack tracking.

**Why:** RT-DETR inference is 4-8x slower than YOLOv8n on CPU, which matters for processing 20-minute clips. The ByteTrack integration with Ultralytics is battle-tested and handles the tracking requirement out of the box. For partial occlusion I compensated by lowering the detection confidence threshold to 0.3 and flagging low-confidence events rather than dropping them — which the schema explicitly supports via the confidence field.

**Trade-off:** Worse performance on heavily occluded scenes compared to RT-DETR. Accepted this trade-off because detection accuracy is evaluated on entry/exit counts, not on occluded-frame accuracy specifically.

---

## Decision 2 — Event Schema Design

**The Problem:** Design the event schema that connects the detection pipeline to the API. Key tension: flexibility vs queryability.

**Options Considered:**
- Separate table per event type (ENTRY table, ZONE_DWELL table, etc.) — maximum query performance, rigid schema
- Single events table with a type column and JSON metadata — flexible, single insert path
- Flat events table with nullable columns for all possible fields — simple but wasteful

**What AI Suggested:** Separate tables per event type with foreign key relationships for referential integrity and query performance.

**What I Chose:** Single events table with a metadata_ JSON column for event-specific fields like queue_depth and sku_zone.

**Why:** The detection pipeline emits 8 event types with overlapping but different metadata shapes. A single insert path in ingestion.py is simpler to maintain and test. The metadata fields are rarely queried directly — most queries filter by event_type and timestamp, which are indexed columns. JSON parsing overhead is acceptable at this data volume.

**Trade-off:** Metadata fields cannot be indexed. Queries that filter by queue_depth (e.g. anomaly detection) must parse JSON in Python rather than in SQL. For the current scale this is acceptable — at 40 live stores with continuous feeds this would need to be revisited.

---

## Decision 3 — API Storage and Concurrency Model

**The Problem:** Choose the storage engine and concurrency model for the Intelligence API.

**Options Considered:**
- PostgreSQL + async SQLAlchemy — production-grade, supports concurrent writes, complex Docker setup
- SQLite + sync SQLAlchemy — zero setup, single-writer, simple reasoning
- Redis for event buffer + PostgreSQL for persistence — high throughput, two services to manage

**What AI Suggested:** PostgreSQL with async SQLAlchemy and connection pooling, citing that real-time event ingestion from 40 stores would saturate SQLite's single-writer lock.

**What I Chose:** SQLite with synchronous SQLAlchemy.

**Why:** The challenge runs on a single machine with batch or simulated real-time event ingestion. SQLite's single-writer limitation is not a bottleneck at this scale. The Docker setup is one service instead of two, which means docker compose up works with zero configuration. The synchronous driver is easier to reason about, test, and debug in a 48-hour window.

**Trade-off:** Will not scale beyond a single API instance. Concurrent write-heavy workloads from 40 live stores would cause lock contention. The correct production architecture would swap SQLite for PostgreSQL — the SQLAlchemy ORM layer means this is a one-line change in database.py plus a Docker service addition.
