"""CCTV video detection pipeline — YOLOv8 person detection with event emission."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO

PERSON_CLASS_ID = 0
MATCH_DISTANCE_PX = 50
DWELL_INTERVAL_SEC = 30
REENTRY_WINDOW = timedelta(minutes=10)
MIN_REENTRY_GAP = timedelta(seconds=60)
COSINE_SIMILARITY_MIN = 0.85


@dataclass
class ExitedVisitor:
    visitor_id: str
    exit_time: datetime
    fingerprint: np.ndarray


@dataclass
class TrackState:
    track_id: int
    visitor_id: str
    cx: float
    cy: float
    is_staff: bool
    confidence: float
    inside_store: bool = False
    fingerprint: np.ndarray | None = None
    zone_id: str | None = None
    zone_enter_time: datetime | None = None
    last_dwell_emit: datetime | None = None
    missed_frames: int = 0


@dataclass
class CentroidTracker:
    max_distance: float = MATCH_DISTANCE_PX
    next_track_id: int = 0
    tracks: dict[int, TrackState] = field(default_factory=dict)

    def _distance(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def update(
        self,
        detections: list[
            tuple[float, float, float, tuple[int, int, int, int], bool, np.ndarray]
        ],
        store_id: str,
        exited_visitors: list[ExitedVisitor],
        now: datetime,
    ) -> dict[int, TrackState]:
        """Match detections to tracks by nearest centroid within max_distance."""
        if not detections:
            for track in self.tracks.values():
                track.missed_frames += 1
            self._drop_stale_tracks()
            return self.tracks

        track_ids = list(self.tracks.keys())
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()

        pairs: list[tuple[float, int, int]] = []
        for ti, tid in enumerate(track_ids):
            track = self.tracks[tid]
            for di, (dcx, dcy, _conf, _bbox, _staff, _fp) in enumerate(detections):
                dist = self._distance((track.cx, track.cy), (dcx, dcy))
                if dist <= self.max_distance:
                    pairs.append((dist, ti, di))

        pairs.sort(key=lambda p: p[0])
        track_match: dict[int, int] = {}
        for _, ti, di in pairs:
            if ti in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(ti)
            matched_dets.add(di)
            track_match[track_ids[ti]] = di

        for tid, di in track_match.items():
            cx, cy, conf, _bbox, is_staff, fp = detections[di]
            track = self.tracks[tid]
            track.cx = cx
            track.cy = cy
            track.confidence = max(track.confidence, conf)
            track.is_staff = track.is_staff or is_staff
            track.fingerprint = fp
            track.missed_frames = 0

        for di, det in enumerate(detections):
            if di in matched_dets:
                continue
            cx, cy, conf, _bbox, is_staff, fp = det
            tid = self.next_track_id
            self.next_track_id += 1
            visitor_id = _resolve_visitor_id(
                tid, store_id, fp, exited_visitors, now
            )
            self.tracks[tid] = TrackState(
                track_id=tid,
                visitor_id=visitor_id,
                cx=cx,
                cy=cy,
                is_staff=is_staff,
                confidence=conf,
                fingerprint=fp,
            )

        for tid in track_ids:
            if tid not in track_match:
                self.tracks[tid].missed_frames += 1

        self._drop_stale_tracks()
        return self.tracks

    def _drop_stale_tracks(self, max_missed: int = 30) -> None:
        stale = [tid for tid, t in self.tracks.items() if t.missed_frames > max_missed]
        for tid in stale:
            del self.tracks[tid]


def _visitor_id(track_id: int, store_id: str) -> str:
    digest = hashlib.md5(f"{track_id}{store_id}".encode()).hexdigest()
    return f"VIS_{digest[:6]}"


def _appearance_fingerprint(
    frame: np.ndarray, bbox: tuple[int, int, int, int]
) -> np.ndarray:
    """Normalized HSV histogram used for re-entry appearance matching."""
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return np.zeros(256, dtype=np.float32)

    roi = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().astype(np.float32)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _prune_exited_visitors(exited: list[ExitedVisitor], now: datetime) -> None:
    cutoff = now - REENTRY_WINDOW
    exited[:] = [ev for ev in exited if ev.exit_time >= cutoff]


def _find_matching_exited_visitor(
    fingerprint: np.ndarray,
    exited_visitors: list[ExitedVisitor],
    now: datetime,
) -> str | None:
    """Return visitor_id if fingerprint matches an exited visitor (sim >= 0.85)."""
    _prune_exited_visitors(exited_visitors, now)
    best_sim = 0.0
    best_visitor: str | None = None

    for ev in exited_visitors:
        sim = _cosine_similarity(fingerprint, ev.fingerprint)
        if sim >= COSINE_SIMILARITY_MIN and sim > best_sim:
            best_sim = sim
            best_visitor = ev.visitor_id

    return best_visitor


def _resolve_visitor_id(
    track_id: int,
    store_id: str,
    fingerprint: np.ndarray,
    exited_visitors: list[ExitedVisitor],
    now: datetime,
) -> str:
    matched = _find_matching_exited_visitor(fingerprint, exited_visitors, now)
    if matched is not None:
        return matched
    return _visitor_id(track_id, store_id)


def _classify_entry_event(
    visitor_id: str,
    frame_time: datetime,
    visitor_exit_times: dict[str, datetime],
) -> str:
    """ENTRY unless same visitor_id exited 60s–10min ago."""
    last_exit = visitor_exit_times.get(visitor_id)
    if last_exit is None:
        return "ENTRY"

    gap = frame_time - last_exit
    if gap < MIN_REENTRY_GAP:
        return "ENTRY"
    if gap <= REENTRY_WINDOW:
        return "REENTRY"
    return "ENTRY"


def _side_of_line(cy: float, threshold_y: float) -> str:
    return "below" if cy >= threshold_y else "above"


def _zone_from_y(cy: float, frame_height: int) -> str:
    ratio = cy / frame_height
    if ratio < 0.4:
        return "ENTRY_LOBBY"
    if ratio < 0.7:
        return "MAIN_FLOOR"
    return "BACK_ZONE"


def _is_staff_uniform(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> bool:
    """Staff wear uniforms: high saturation, low variance in HSV within the bbox."""
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return False

    roi = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)
    value = hsv[:, :, 2].astype(np.float32)

    mean_sat = float(np.mean(saturation))
    sat_var = float(np.var(saturation))
    val_var = float(np.var(value))

    return mean_sat > 80.0 and sat_var < 400.0 and val_var < 400.0


def _make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    *,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.91,
    session_seq: int = 1,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(confidence, 2),
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": session_seq,
        },
    }


def _count_event_types(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ENTRY": 0, "EXIT": 0, "REENTRY": 0}
    for event in events:
        et = event.get("event_type")
        if et in counts:
            counts[et] += 1
    return counts


def process_video(
    video_path: str | Path,
    store_id: str,
    camera_id: str,
    output_path: str | Path,
    *,
    model: YOLO | None = None,
) -> list[dict[str, Any]]:
    """Process a CCTV clip and write newline-delimited JSON events."""
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    yolo = model or YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    threshold_y = frame_height * 0.4

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    tracker = CentroidTracker()
    events: list[dict[str, Any]] = []
    session_seq: dict[str, int] = {}
    prev_side: dict[int, str] = {}
    exited_visitors: list[ExitedVisitor] = []
    visitor_exit_times: dict[str, datetime] = {}

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_time = base_time + timedelta(seconds=frame_idx / fps)
        _prune_exited_visitors(exited_visitors, frame_time)
        results = yolo(frame, verbose=False)[0]

        detections: list[
            tuple[float, float, float, tuple[int, int, int, int], bool, np.ndarray]
        ] = []
        if results.boxes is not None:
            for box in results.boxes:
                cls_id = int(box.cls[0])
                if cls_id != PERSON_CLASS_ID:
                    continue
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                bbox = (x1, y1, x2, y2)
                staff = _is_staff_uniform(frame, bbox)
                fp = _appearance_fingerprint(frame, bbox)
                detections.append((cx, cy, conf, bbox, staff, fp))

        tracks = tracker.update(detections, store_id, exited_visitors, frame_time)

        for tid, track in tracks.items():
            if track.missed_frames > 0:
                continue

            current_side = _side_of_line(track.cy, threshold_y)
            old_side = prev_side.get(tid, current_side)

            if old_side == "above" and current_side == "below" and not track.inside_store:
                track.inside_store = True
                seq = session_seq.get(track.visitor_id, 0) + 1
                session_seq[track.visitor_id] = seq
                event_type = _classify_entry_event(
                    track.visitor_id, frame_time, visitor_exit_times
                )
                if event_type == "REENTRY":
                    visitor_exit_times.pop(track.visitor_id, None)
                events.append(
                    _make_event(
                        store_id,
                        camera_id,
                        track.visitor_id,
                        event_type,
                        frame_time,
                        is_staff=track.is_staff,
                        confidence=track.confidence,
                        session_seq=seq,
                    )
                )
            elif old_side == "below" and current_side == "above" and track.inside_store:
                track.inside_store = False
                seq = session_seq.get(track.visitor_id, 1)
                events.append(
                    _make_event(
                        store_id,
                        camera_id,
                        track.visitor_id,
                        "EXIT",
                        frame_time,
                        is_staff=track.is_staff,
                        confidence=track.confidence,
                        session_seq=seq,
                    )
                )
                visitor_exit_times[track.visitor_id] = frame_time
                if track.fingerprint is not None:
                    exited_visitors.append(
                        ExitedVisitor(
                            visitor_id=track.visitor_id,
                            exit_time=frame_time,
                            fingerprint=track.fingerprint.copy(),
                        )
                    )

            prev_side[tid] = current_side

            zone = _zone_from_y(track.cy, frame_height)
            if track.zone_id != zone:
                track.zone_id = zone
                track.zone_enter_time = frame_time
                track.last_dwell_emit = frame_time
            elif track.zone_enter_time is not None and track.last_dwell_emit is not None:
                elapsed = (frame_time - track.last_dwell_emit).total_seconds()
                if elapsed >= DWELL_INTERVAL_SEC:
                    events.append(
                        _make_event(
                            store_id,
                            camera_id,
                            track.visitor_id,
                            "ZONE_DWELL",
                            frame_time,
                            zone_id=zone,
                            dwell_ms=DWELL_INTERVAL_SEC * 1000,
                            is_staff=track.is_staff,
                            confidence=track.confidence,
                            session_seq=session_seq.get(track.visitor_id, 1),
                        )
                    )
                    track.last_dwell_emit = frame_time

        frame_idx += 1

    cap.release()

    with output_path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")

    counts = _count_event_types(events)
    print(
        f"Event counts — ENTRY: {counts['ENTRY']}, "
        f"EXIT: {counts['EXIT']}, REENTRY: {counts['REENTRY']}"
    )

    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="Process CCTV video and emit store events.")
    parser.add_argument("--video", required=True, help="Path to input video clip")
    parser.add_argument("--store-id", required=True, help="Store identifier")
    parser.add_argument("--camera-id", required=True, help="Camera identifier")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    args = parser.parse_args()

    events = process_video(args.video, args.store_id, args.camera_id, args.output)
    print(f"Wrote {len(events)} events to {args.output}")


if __name__ == "__main__":
    main()
