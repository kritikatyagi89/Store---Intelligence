"""Post pipeline events to the Store Intelligence API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

BATCH_SIZE = 100


def post_events(jsonl_path: str | Path, api_url: str) -> dict[str, int]:
    """Read a JSONL file and POST events in batches to /events/ingest."""
    jsonl_path = Path(jsonl_path)
    events: list[dict[str, Any]] = []

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    base = api_url.rstrip("/")
    ingest_url = f"{base}/events/ingest"

    total_accepted = 0
    total_rejected = 0

    for start in range(0, len(events), BATCH_SIZE):
        batch = events[start : start + BATCH_SIZE]
        response = requests.post(ingest_url, json={"events": batch}, timeout=30)
        response.raise_for_status()
        data = response.json()
        accepted = data.get("accepted_count", 0)
        rejected = data.get("rejected_count", 0)
        total_accepted += accepted
        total_rejected += rejected
        print(f"Batch {start // BATCH_SIZE + 1}: accepted={accepted}, rejected={rejected}")

    print(f"Total: accepted={total_accepted}, rejected={total_rejected}")
    return {"accepted": total_accepted, "rejected": total_rejected}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Post JSONL events to the API.")
    parser.add_argument(
        "--input",
        required=True,
        dest="jsonl_path",
        help="Path to events JSONL file",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Base API URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()
    post_events(args.jsonl_path, args.api_url)
