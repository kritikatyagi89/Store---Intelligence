# Store Intelligence API

## Setup (5 commands)
git clone <repo-url>
cd store-intelligence
cp .env.example .env
docker compose up --build
python data/seed_events.py

## Run Detection Pipeline
python pipeline/detect.py --video data/clips/store.mp4 --store-id STORE_BLR_002 --camera-id CAM_ENTRY_01 --output data/events.jsonl
python pipeline/emit.py --input data/events.jsonl --api-url http://localhost:8000

## Run Live Dashboard
python dashboard/live.py

## API Endpoints
- POST http://localhost:8000/events/ingest
- GET  http://localhost:8000/stores/{store_id}/metrics
- GET  http://localhost:8000/stores/{store_id}/funnel
- GET  http://localhost:8000/stores/{store_id}/heatmap
- GET  http://localhost:8000/stores/{store_id}/anomalies
- GET  http://localhost:8000/health

## Run Tests
pytest tests/ -v

## Architecture
Five stage pipeline: CCTV clips → Detection (YOLOv8n + ByteTrack) 
→ Event Stream (JSONL) → Intelligence API (FastAPI + SQLite) 
→ Live Dashboard (rich terminal)
