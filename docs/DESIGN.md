# Store Intelligence System — DESIGN.md

## Architecture Overview

The Store Intelligence system is a five-stage pipeline that transforms raw CCTV footage into actionable retail business metrics. Each stage has a clearly defined input, output, and responsibility.

### Stage 1 — Input Layer
Raw inputs are: CCTV video clips (1080p, 15fps, 3 camera angles per store), store_layout.json (zone polygon definitions), and pos_transactions.csv (timestamped purchase records). No preprocessing is assumed — the pipeline handles raw footage directly.

### Stage 2 — Detection Pipeline (pipeline/)
The detection pipeline processes each video clip frame by frame using YOLOv8n for person detection (class 0 only). Detected bounding boxes are passed to a centroid-based tracker that assigns persistent track IDs across frames. Each track is assigned a visitor_id token derived from an MD5 hash of the track ID and store ID, ensuring uniqueness per session.

Entry and exit direction are determined by tracking centroid movement across a horizontal threshold line at 40% of frame height. Staff are identified by HSV colour histogram analysis of the bounding box region — high saturation uniform colours indicate staff uniforms.

Re-entry detection works by storing a fingerprint of each exited visitor. If a new track's appearance embedding is within a cosine distance threshold of an exited visitor within a 10-minute window, a REENTRY event is emitted instead of a new ENTRY.

Cross-camera deduplication suppresses duplicate ENTRY events when the same track appears on both the entry camera and the floor camera within a 5-second window.

### Stage 3 — Session Manager
The Session Manager maintains an in-memory mapping of visitor_id to session state. It tracks: session start time, zones visited, current zone, session sequence counter, and whether the session is open or closed. This component is the source of truth for funnel calculation and re-entry detection. Sessions expire after 30 minutes of inactivity.

### Stage 4 — Event Stream
Every behavioural signal is serialised into a structured JSON event matching the required schema. Events are written to a .jsonl file during batch processing, or posted directly to the API during real-time operation. The schema uses UUID v4 for event_id (globally unique), ISO-8601 UTC timestamps derived from clip start time plus frame offset divided by fps, and a metadata object for queue depth and zone labels.

### Stage 5 — Intelligence API (app/)
The FastAPI application ingests events via POST /events/ingest, which validates, deduplicates by event_id, and persists to SQLite. Six endpoints expose computed metrics: /metrics (real-time store KPIs), /funnel (session-based conversion funnel), /heatmap (zone visit frequency normalised 0-100), /anomalies (active operational alerts), and /health (feed status with STALE_FEED detection).

POS correlation is handled by the metrics engine: for each POS transaction, any visitor present in a BILLING zone within the 5 minutes before the transaction timestamp is marked as converted. This window was chosen to balance false positives (too wide) against missed conversions (too narrow).

## Database Schema

Two tables in SQLite:

**events** — stores every ingested event. Columns: event_id (PK), store_id, camera_id, visitor_id, event_type, timestamp, zone_id, dwell_ms, is_staff, confidence, metadata_ (JSON string).

**sessions** — derived session records. Columns: visitor_id, store_id, session_start, session_end, is_converted, entry_count.

The metadata column uses JSON serialisation rather than a separate table to keep the schema flexible — event types have different metadata shapes and a single table avoids complex joins.

## AI-Assisted Decisions

**Decision 1 — Model selection:** I asked Claude to evaluate YOLOv8 vs RT-DETR for retail CCTV detection. The AI recommended RT-DETR citing better performance on partially occluded scenes (relevant for the billing queue clip). I disagreed because RT-DETR has significantly higher inference latency and the YOLOv8 ecosystem has better ByteTrack integration. I chose YOLOv8n and accepted the occlusion trade-off, compensating by lowering the confidence threshold to 0.3 to catch partial detections.

**Decision 2 — Database:** Claude suggested PostgreSQL with async SQLAlchemy for production readiness. I overrode this in favour of SQLite with synchronous SQLAlchemy. The reasoning: the challenge runs on a single machine, SQLite requires zero setup in Docker, and the synchronous driver is simpler to reason about for a 48-hour build. The CHOICES.md documents this trade-off explicitly.

**Decision 3 — POS Correlation Window:** I asked Claude to help design the conversion attribution logic. It suggested a 10-minute window. I tightened it to 5 minutes after reasoning that a 10-minute window would inflate conversion rates in busy stores by attributing unrelated visitors to transactions. The 5-minute window matches the problem statement specification exactly.
