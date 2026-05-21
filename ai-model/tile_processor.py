#!/usr/bin/env python3
"""
Satellite Tile Processor
========================
Downloads satellite tiles for a circular area and runs YOLOv11 segmentation
on each tile, emitting newline-delimited JSON to stdout for real-time progress.

Called by the Laravel backend via proc_open.

Output lines (one JSON per line, flushed immediately):
  {"type": "start",          "total_tiles": N, "zoom": Z, "bbox": {...}}
  {"type": "status",         "message": "..."}
  {"type": "tile_processed", "progress": N, "total": N, "tile": {...}}
  {"type": "complete",       "total_tiles": N, "processed": N}
  {"type": "error",          "message": "..."}
"""

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── auto-install missing deps ─────────────────────────────────────────────────
def _ensure(pkg: str, import_as: str = None):
    import importlib
    name = import_as or pkg
    try:
        return importlib.import_module(name)
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return importlib.import_module(name)


requests = _ensure("requests")
_ensure("Pillow", "PIL")
from PIL import Image

TILE_SIZE = 256
MAX_TILES = 6000  # covers zoom-18 + 5 km radius (~5 829 tiles); Laravel enforces this too

PROVIDERS = [
    {
        "name": "ESRI World Imagery",
        "slug": "esri",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 23,
        "ext": "jpg",
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Google Maps Satellite",
        "slug": "google",
        "url": "https://mt{s}.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
        "max_zoom": 20,
        "ext": "jpg",
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/maps",
        },
    },
]

SESSION = requests.Session()
SESSION.headers.update({"Connection": "keep-alive"})


# ── JSON output helpers ───────────────────────────────────────────────────────

def emit(event_type: str, **kwargs) -> None:
    """Write a single JSON progress line to stdout and flush immediately."""
    payload = {"type": event_type, **kwargs}
    print(json.dumps(payload, separators=(",", ":")), flush=True)


# ── tile math ─────────────────────────────────────────────────────────────────

def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    n = 2 ** zoom
    x = int(n * (lon + 180.0) / 360.0)
    lat_r = math.radians(lat)
    y = int(n * (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0)
    return x, y


def tile_to_lat_lon(x: int, y: int, zoom: int) -> Tuple[float, float]:
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def bbox_from_center_radius(lat: float, lon: float, radius_m: float) -> dict:
    """Compute a bounding box from a center point and radius in metres."""
    lat_deg = radius_m / 111_320.0
    lon_deg = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return {
        "south": lat - lat_deg,
        "north": lat + lat_deg,
        "west":  lon - lon_deg,
        "east":  lon + lon_deg,
    }


def get_tile_grid(bbox: dict, zoom: int) -> Tuple[range, range]:
    x_min, y_max = lat_lon_to_tile(bbox["south"], bbox["west"], zoom)
    x_max, y_min = lat_lon_to_tile(bbox["north"], bbox["east"], zoom)
    limit = 2 ** zoom - 1
    x_range = range(max(0, x_min), min(limit, x_max) + 1)
    y_range = range(max(0, y_min), min(limit, y_max) + 1)
    return x_range, y_range


def tile_intersects_circle(
    x: int,
    y: int,
    z: int,
    center_lat: float,
    center_lon: float,
    radius_m: float,
) -> bool:
    """
    Return True if the tile rectangle (x, y, z) overlaps the query circle.

    Algorithm: find the closest point on the tile's geographic bounding box
    to the circle centre, then check whether that distance is within the radius.
    This correctly handles tiles that are fully inside, partially inside, and
    completely outside the circle.
    """
    nw_lat, nw_lon = tile_to_lat_lon(x,     y,     z)
    se_lat, se_lon = tile_to_lat_lon(x + 1, y + 1, z)

    # Closest point on the tile bbox to the query centre
    closest_lat = max(se_lat, min(center_lat, nw_lat))
    closest_lon = max(nw_lon, min(center_lon, se_lon))

    # Metric distance (approximation valid for radii < 50 km)
    d_lat = (center_lat - closest_lat) * 111_320.0
    d_lon = (center_lon - closest_lon) * 111_320.0 * math.cos(math.radians(center_lat))

    return math.sqrt(d_lat * d_lat + d_lon * d_lon) <= radius_m


# ── tile download ─────────────────────────────────────────────────────────────

def _build_url(provider: dict, x: int, y: int, z: int) -> str:
    return provider["url"].format(x=x, y=y, z=z, s=random.randint(0, 3))


def _is_blank_tile(img_bytes: bytes) -> bool:
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        pixels = [
            img.getpixel((w * i // 4, h * j // 4))
            for i in range(1, 4)
            for j in range(1, 4)
        ]
        first = pixels[0]
        return all(abs(p[c] - first[c]) < 8 for p in pixels[1:] for c in range(3))
    except Exception:
        return True


def download_tile(
    x: int,
    y: int,
    z: int,
    cache_dir: Path,
    providers: List[dict],
    max_retries: int = 3,
) -> Optional[bytes]:
    """
    Download a tile, trying providers in order.
    Returns raw image bytes or None. Uses disk cache so repeated runs are instant.
    """
    for provider in providers:
        if z > provider["max_zoom"]:
            continue

        cache_path = (
            cache_dir / "tiles" / provider["slug"] / str(z) / str(x) / f"{y}.{provider['ext']}"
        )

        if cache_path.exists() and cache_path.stat().st_size > 500:
            return cache_path.read_bytes()

        url = _build_url(provider, x, y, z)
        time.sleep(random.uniform(0.02, 0.08))   # polite delay

        for attempt in range(max_retries):
            try:
                resp = SESSION.get(
                    url,
                    headers=provider["headers"],
                    timeout=15,
                    allow_redirects=True,
                )
                if resp.status_code == 200 and len(resp.content) > 500:
                    if not _is_blank_tile(resp.content):
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_bytes(resp.content)
                        return resp.content
                break   # non-200 or blank → try next provider
            except requests.RequestException:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

    return None


# ── inference ─────────────────────────────────────────────────────────────────

def run_inference(
    img_bytes: bytes,
    model,
    conf: float,
    iou: float,
    imgsz: int,
) -> List[dict]:
    """
    Run YOLOv11-seg inference on raw image bytes.
    Returns a list of detection dicts (no image output).
    """
    import numpy as np
    import cv2

    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        return []

    results = model.predict(
        source=img_bgr,
        conf=conf,
        iou=iou,
        imgsz=imgsz,
        verbose=False,
    )
    result = results[0]

    detections: List[dict] = []
    if result.boxes is None:
        return detections

    boxes   = result.boxes
    classes = boxes.cls.cpu().numpy().astype(int)
    confs   = boxes.conf.cpu().numpy()
    xyxys   = boxes.xyxy.cpu().numpy()

    img_h, img_w = img_bgr.shape[:2]

    for i in range(len(classes)):
        cls_idx = int(classes[i])
        det: dict = {
            "class_id":   cls_idx,
            "class_name": result.names.get(cls_idx, str(cls_idx)),
            "confidence": round(float(confs[i]), 4),
            "bbox": {
                "x1": round(float(xyxys[i][0]), 2),
                "y1": round(float(xyxys[i][1]), 2),
                "x2": round(float(xyxys[i][2]), 2),
                "y2": round(float(xyxys[i][3]), 2),
            },
        }

        # Encode mask as simplified polygon (compact for JSON transport)
        if result.masks is not None and i < len(result.masks.data):
            try:
                raw_mask = result.masks.data[i].cpu().numpy()
                mask_u8  = cv2.resize(
                    (raw_mask * 255).astype("uint8"),
                    (img_w, img_h),
                    interpolation=cv2.INTER_NEAREST,
                )
                contours, _ = cv2.findContours(
                    mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                if contours:
                    largest = max(contours, key=cv2.contourArea)
                    eps     = 0.01 * cv2.arcLength(largest, True)
                    approx  = cv2.approxPolyDP(largest, eps, True)
                    det["polygon"] = approx.reshape(-1, 2).tolist()
            except Exception:
                pass   # polygon is optional

        detections.append(det)

    return detections


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download satellite tiles and run YOLO inference, outputting JSON progress.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lat",       type=float, required=True,  help="Centre latitude")
    parser.add_argument("--lon",       type=float, required=True,  help="Centre longitude")
    parser.add_argument("--radius",    type=float, required=True,  help="Radius in metres")
    parser.add_argument("--zoom",      type=int,   default=18,     help="Tile zoom level (15-20)")
    parser.add_argument("--model",     type=str,   required=True,  help="Path to best.pt")
    parser.add_argument("--cache-dir", type=str,   required=True,  help="Cache directory for tiles and detections")
    parser.add_argument("--workers",   type=int,   default=8,      help="Parallel download threads")
    parser.add_argument("--provider",  type=str,   default="google",
                        choices=["esri", "google", "all"],          help="Tile provider")
    parser.add_argument("--conf",      type=float, default=0.25,   help="YOLO confidence threshold")
    parser.add_argument("--iou",       type=float, default=0.45,   help="YOLO NMS IoU threshold")
    parser.add_argument("--imgsz",     type=int,   default=256,    help="YOLO inference image size")
    args = parser.parse_args()

    # ── select providers ──
    if args.provider == "esri":
        active_providers = [p for p in PROVIDERS if p["slug"] == "esri"]
    elif args.provider == "google":
        active_providers = [p for p in PROVIDERS if p["slug"] == "google"]
    else:
        active_providers = PROVIDERS   # esri first, google fallback

    cache_dir = Path(args.cache_dir)
    det_cache_dir = cache_dir / "detections"
    det_cache_dir.mkdir(parents=True, exist_ok=True)

    # ── compute tile grid ──
    bbox     = bbox_from_center_radius(args.lat, args.lon, args.radius)
    x_range, y_range = get_tile_grid(bbox, args.zoom)

    # Filter to only tiles that actually intersect the query circle.
    # A square bounding box over-selects ~21 % extra tiles in the corners.
    tasks = [
        (x, y)
        for y in y_range
        for x in x_range
        if tile_intersects_circle(x, y, args.zoom, args.lat, args.lon, args.radius)
    ]
    total_tiles = len(tasks)

    if total_tiles == 0:
        emit("error", message="No tiles found in the specified area.")
        sys.exit(1)

    if total_tiles > MAX_TILES:
        emit(
            "error",
            message=(
                f"Too many tiles ({total_tiles}). "
                f"Reduce radius or zoom. Max allowed: {MAX_TILES}."
            ),
        )
        sys.exit(1)

    emit(
        "start",
        total_tiles=total_tiles,
        zoom=args.zoom,
        bbox={
            "south": round(bbox["south"], 7),
            "north": round(bbox["north"], 7),
            "west":  round(bbox["west"],  7),
            "east":  round(bbox["east"],  7),
        },
    )

    # ── load YOLO model (once) ──
    try:
        emit("status", message="Loading YOLO model…")
        from ultralytics import YOLO
        model = YOLO(args.model)
        emit("status", message="Model loaded.")
    except Exception as exc:
        emit("error", message=f"Failed to load YOLO model: {exc}")
        sys.exit(1)

    completed  = 0

    def download_task(xy: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[bytes]]:
        """Worker function: download a single tile (returns bytes or None)."""
        x, y = xy
        data = download_tile(x, y, args.zoom, cache_dir, active_providers)
        return xy, data

    # Pipeline: parallel downloads, sequential inference (thread-safe).
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_task, xy): xy for xy in tasks}

        for future in as_completed(futures):
            completed += 1
            x, y = futures[future]
            z    = args.zoom

            nw_lat, nw_lon = tile_to_lat_lon(x,     y,     z)
            se_lat, se_lon = tile_to_lat_lon(x + 1, y + 1, z)
            tile_bbox = {
                "north": round(nw_lat, 7),
                "west":  round(nw_lon, 7),
                "south": round(se_lat, 7),
                "east":  round(se_lon, 7),
            }

            # Check detection cache first
            cache_key     = f"{args.provider}_{z}_{x}_{y}"
            det_cache_file = det_cache_dir / f"{cache_key}.json"

            if det_cache_file.exists():
                try:
                    cached = json.loads(det_cache_file.read_text())
                    emit(
                        "tile_processed",
                        progress=completed,
                        total=total_tiles,
                        tile={
                            "x": x, "y": y, "z": z,
                            "cached": True,
                            "detections": cached.get("detections", []),
                            "bbox": tile_bbox,
                        },
                    )
                    continue
                except Exception:
                    pass   # corrupt cache – fall through to re-process

            # Get downloaded bytes
            try:
                _, img_bytes = future.result()
            except Exception as exc:
                emit(
                    "tile_processed",
                    progress=completed,
                    total=total_tiles,
                    tile={
                        "x": x, "y": y, "z": z,
                        "cached": False,
                        "error": str(exc),
                        "detections": [],
                        "bbox": tile_bbox,
                    },
                )
                continue

            if img_bytes is None:
                emit(
                    "tile_processed",
                    progress=completed,
                    total=total_tiles,
                    tile={
                        "x": x, "y": y, "z": z,
                        "cached": False,
                        "error": "Tile download failed",
                        "detections": [],
                        "bbox": tile_bbox,
                    },
                )
                continue

            # Run inference (in main thread – no CUDA race conditions)
            try:
                detections = run_inference(img_bytes, model, args.conf, args.iou, args.imgsz)
            except Exception as exc:
                detections = []
                # non-fatal: emit empty detections

            # Persist detection cache
            try:
                det_cache_file.write_text(
                    json.dumps(
                        {"detections": detections, "tile": {"x": x, "y": y, "z": z}},
                        separators=(",", ":"),
                    )
                )
            except Exception:
                pass   # cache write failure is non-fatal

            emit(
                "tile_processed",
                progress=completed,
                total=total_tiles,
                tile={
                    "x": x, "y": y, "z": z,
                    "cached": False,
                    "detections": detections,
                    "bbox": tile_bbox,
                },
            )

    emit("complete", total_tiles=total_tiles, processed=completed)


if __name__ == "__main__":
    main()
