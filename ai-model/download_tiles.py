#!/usr/bin/env python3
"""
Fergana, Uzbekistan – Satellite Tile Downloader
================================================
Downloads high-resolution satellite tiles (zoom 17, 1.2 m/px) covering the
full urban area of Fergana (~12×12 km) and stitches them into a single
georeferenced JPEG.

Providers (tried in order, per tile):
  1. ESRI World Imagery  – free, no token required
  2. Google Maps Satellite (parsed) – fallback if ESRI fails

Usage:
    python download_tiles.py              # zoom 17, full urban area
    python download_tiles.py --zoom 18    # higher detail (4× tiles, ~20 min)
    python download_tiles.py --zoom 19    # maximum detail (very slow)
    python download_tiles.py --list-providers

Dependencies (auto-installed if missing):
    requests, Pillow, tqdm
"""

import argparse
import math
import os
import random
import struct
import sys
from typing import Dict, List, Optional, Tuple
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

# ── auto-install missing dependencies ────────────────────────────────────────
def _ensure(pkg, import_as=None):
    import importlib
    name = import_as or pkg
    try:
        return importlib.import_module(name)
    except ImportError:
        import subprocess
        print(f"[setup] Installing {pkg} …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])
        return importlib.import_module(name)

requests = _ensure("requests")
_ensure("Pillow", "PIL")
from PIL import Image
tqdm_mod = _ensure("tqdm")
tqdm     = tqdm_mod.tqdm

# ── constants ────────────────────────────────────────────────────────────────
TILE_SIZE = 256  # pixels

# Fergana, Uzbekistan – city centre (lat, lon)
FERGANA_CENTER = (40.3839, 71.7864)

# ── default configuration ────────────────────────────────────────────────────
# Edit these values to change the default behaviour when no CLI arguments are given.
CONFIG = {
    "zoom":     17,        # 17=1.2m/px  18=0.6m/px  19=0.3m/px  20=0.15m/px
    "provider": "google",  # "esri", "google", or "all"
    "km":       5,         # side length of the square area to download (km)
    "workers":  8,
    "output_dir": ".",
}

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

# ── tile math ────────────────────────────────────────────────────────────────

def bbox_from_center_km(lat: float, lon: float, km: float) -> dict:
    """Return a bounding box dict centered on (lat, lon) with a side of km × km."""
    half_lat = (km / 2.0) / 111.32
    half_lon = (km / 2.0) / (111.32 * math.cos(math.radians(lat)))
    return {
        "south": lat - half_lat,
        "north": lat + half_lat,
        "west":  lon - half_lon,
        "east":  lon + half_lon,
    }


def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Convert latitude/longitude to tile XY at given zoom level (Web Mercator)."""
    n = 2 ** zoom
    x = int(n * (lon + 180.0) / 360.0)
    lat_r = math.radians(lat)
    y = int(n * (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0)
    return x, y


def tile_to_lat_lon(x: int, y: int, zoom: int) -> Tuple[float, float]:
    """Return the (lat, lon) of the NW corner of tile (x, y) at zoom."""
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def get_tile_grid(bbox: dict, zoom: int) -> Tuple[range, range]:
    """Return (x_range, y_range) tile index ranges for a bounding box."""
    x_min, y_max = lat_lon_to_tile(bbox["south"], bbox["west"], zoom)
    x_max, y_min = lat_lon_to_tile(bbox["north"], bbox["east"], zoom)
    # clamp to valid tile range
    limit = 2 ** zoom - 1
    x_range = range(max(0, x_min), min(limit, x_max) + 1)
    y_range = range(max(0, y_min), min(limit, y_max) + 1)
    return x_range, y_range


# ── download engine ──────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({"Connection": "keep-alive"})


def _build_url(provider: dict, x: int, y: int, z: int) -> str:
    s = random.randint(0, 3)
    return provider["url"].format(x=x, y=y, z=z, s=s)


def _is_blank_tile(img_bytes: bytes) -> bool:
    """Return True if the tile is a solid-colour or near-blank image."""
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        # sample 9 evenly-spaced pixels; blank tiles are uniform
        w, h = img.size
        pixels = [
            img.getpixel((w * i // 4, h * j // 4))
            for i in range(1, 4) for j in range(1, 4)
        ]
        first = pixels[0]
        return all(
            abs(p[c] - first[c]) < 8
            for p in pixels[1:]
            for c in range(3)
        )
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
    Download a single tile, trying providers in order.
    Returns raw image bytes or None on failure.
    Tiles are cached to disk so re-runs are instant.
    """
    for provider in providers:
        if z > provider["max_zoom"]:
            continue

        slug = provider["slug"]
        cache_path = cache_dir / slug / str(z) / str(x) / f"{y}.{provider['ext']}"

        # serve from cache
        if cache_path.exists() and cache_path.stat().st_size > 500:
            return cache_path.read_bytes()

        url = _build_url(provider, x, y, z)
        delay = random.uniform(0.05, 0.15)
        time.sleep(delay)

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
                # non-200 or blank → try next provider
                break
            except requests.RequestException:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

    return None  # all providers failed for this tile


# ── stitching ────────────────────────────────────────────────────────────────

def stitch_tiles(
    tile_data: Dict[Tuple[int, int], bytes],
    x_range: range,
    y_range: range,
) -> Image.Image:
    """Combine downloaded tile bytes into a single PIL Image mosaic."""
    width  = len(x_range) * TILE_SIZE
    height = len(y_range) * TILE_SIZE
    mosaic = Image.new("RGB", (width, height), color=(30, 30, 30))

    for yi, y in enumerate(y_range):
        for xi, x in enumerate(x_range):
            raw = tile_data.get((x, y))
            if raw is None:
                continue
            try:
                tile_img = Image.open(BytesIO(raw)).convert("RGB")
                if tile_img.size != (TILE_SIZE, TILE_SIZE):
                    tile_img = tile_img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)
                mosaic.paste(tile_img, (xi * TILE_SIZE, yi * TILE_SIZE))
            except Exception:
                pass  # leave dark patch for failed tile

    return mosaic


# ── world file (.jgw) ────────────────────────────────────────────────────────

def write_world_file(path: Path, x0: int, y0: int, zoom: int) -> None:
    """
    Write a JPEG world file (.jgw) for GIS software (QGIS, ArcGIS, etc.).
    The world file defines pixel size and top-left geo-coordinate.
    """
    n = 2 ** zoom
    # pixel size in degrees (approximate; valid at the equator, fine for local use)
    pixel_deg_x = 360.0 / (n * TILE_SIZE)
    pixel_deg_y = -360.0 / (n * TILE_SIZE)  # negative: y increases downward

    # top-left pixel centre of tile (x0, y0)
    nw_lat, nw_lon = tile_to_lat_lon(x0, y0, zoom)
    half = pixel_deg_x / 2.0

    lines = [
        f"{pixel_deg_x:.10f}",   # pixel size in x direction
        "0.0",                    # rotation (x)
        "0.0",                    # rotation (y)
        f"{pixel_deg_y:.10f}",   # pixel size in y direction (negative)
        f"{nw_lon + half:.10f}", # x coordinate of top-left pixel centre
        f"{nw_lat + half:.10f}", # y coordinate of top-left pixel centre
    ]
    path.write_text("\n".join(lines) + "\n")


# ── main ──────────────────────────────────────────────────────────────────────

def list_providers() -> None:
    print("\nAvailable satellite tile providers:\n")
    for i, p in enumerate(PROVIDERS, 1):
        print(f"  {i}. {p['name']}  (max zoom: {p['max_zoom']}, slug: {p['slug']})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download satellite tiles for Fergana, Uzbekistan.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--zoom", type=int, default=CONFIG["zoom"],
        help="Tile zoom level (17=1.2m/px, 18=0.6m/px, 19=0.3m/px, 20=0.15m/px)",
    )
    parser.add_argument(
        "--km", type=float, default=CONFIG["km"],
        help="Side length of the square download area in km (centred on Fergana)",
    )
    parser.add_argument(
        "--provider", type=str, choices=["esri", "google", "all"],
        default=CONFIG["provider"],
        help="Tile provider: esri, google, or all (ESRI first, Google fallback)",
    )
    parser.add_argument(
        "--workers", type=int, default=CONFIG["workers"],
        help="Parallel download threads",
    )
    parser.add_argument(
        "--output-dir", type=str, default=CONFIG["output_dir"],
        help="Directory to save output files",
    )
    parser.add_argument(
        "--lat", type=float, default=FERGANA_CENTER[0],
        help="Latitude of the area centre (default: Fergana)",
    )
    parser.add_argument(
        "--lon", type=float, default=FERGANA_CENTER[1],
        help="Longitude of the area centre (default: Fergana)",
    )
    parser.add_argument(
        "--list-providers", action="store_true",
        help="Print available tile providers and exit.",
    )
    args = parser.parse_args()

    if args.list_providers:
        list_providers()
        return

    zoom = args.zoom
    if zoom < 1 or zoom > 23:
        print("Error: --zoom must be between 1 and 23.")
        sys.exit(1)

    if args.km <= 0:
        print("Error: --km must be a positive number.")
        sys.exit(1)

    # filter providers
    if args.provider == "esri":
        active_providers = [p for p in PROVIDERS if p["slug"] == "esri"]
    elif args.provider == "google":
        active_providers = [p for p in PROVIDERS if p["slug"] == "google"]
    else:
        active_providers = PROVIDERS  # all, ESRI first

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "tiles_cache"

    center = (args.lat, args.lon)

    # ── compute tile grid ──
    bbox = bbox_from_center_km(center[0], center[1], args.km)
    x_range, y_range = get_tile_grid(bbox, zoom)
    total_tiles = len(x_range) * len(y_range)
    img_w = len(x_range) * TILE_SIZE
    img_h = len(y_range) * TILE_SIZE

    date_tag = datetime.now().strftime("%Y%m%d")
    base_name = f"fergana_satellite_z{zoom}_{date_tag}"
    jpg_path = output_dir / f"{base_name}.jpg"
    jgw_path = output_dir / f"{base_name}.jgw"

    print()
    print("=" * 60)
    print("  Fergana Satellite Tile Downloader")
    print("=" * 60)
    print(f"  Zoom level  : {zoom}  (~{360 / (2**zoom * TILE_SIZE) * 111320:.1f} m/pixel at equator)")
    print(f"  Area        : {args.km:.1f} × {args.km:.1f} km  (centred on {center[0]:.4f}°N, {center[1]:.4f}°E)")
    print(f"  Tile grid   : {len(x_range)} × {len(y_range)} = {total_tiles:,} tiles")
    print(f"  Output size : {img_w:,} × {img_h:,} px")
    print(f"  Providers   : {', '.join(p['name'] for p in active_providers)}")
    print(f"  Output file : {jpg_path}")
    print(f"  Cache dir   : {cache_dir}")
    print("=" * 60)
    print()

    # count already-cached tiles
    cached = sum(
        1 for x in x_range for y in y_range
        for p in active_providers
        if (cache_dir / p["slug"] / str(zoom) / str(x) / f"{y}.{p['ext']}").exists()
    )
    if cached:
        print(f"  [cache] {cached:,} / {total_tiles:,} tiles already cached – skipping downloads.")
    print()

    # ── download ──
    tile_data: Dict[Tuple[int, int], bytes] = {}
    tasks = [(x, y) for y in y_range for x in x_range]

    with tqdm(total=total_tiles, unit="tile", desc="Downloading", ncols=70) as bar:
        def _fetch(xy):
            x, y = xy
            data = download_tile(x, y, zoom, cache_dir, active_providers)
            return (x, y), data

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_fetch, xy): xy for xy in tasks}
            for future in as_completed(futures):
                (x, y), data = future.result()
                if data:
                    tile_data[(x, y)] = data
                bar.update(1)

    ok  = len(tile_data)
    bad = total_tiles - ok
    print(f"\n  Downloaded : {ok:,}  |  Failed/blank : {bad:,}")

    if ok == 0:
        print("\n  ERROR: No tiles downloaded. Check internet connection.")
        sys.exit(1)

    # ── stitch ──
    print("\n  Stitching tiles …")
    mosaic = stitch_tiles(tile_data, x_range, y_range)

    # ── save ──
    print(f"  Saving {jpg_path.name} …")
    mosaic.save(str(jpg_path), "JPEG", quality=90, optimize=True, progressive=True)

    print(f"  Saving {jgw_path.name} (GIS world file) …")
    write_world_file(jgw_path, x_range.start, y_range.start, zoom)

    size_mb = jpg_path.stat().st_size / 1_048_576
    print()
    print("=" * 60)
    print("  Done!")
    print(f"  Image : {jpg_path}")
    print(f"  Size  : {size_mb:.1f} MB  ({img_w:,} × {img_h:,} px)")
    print(f"  World : {jgw_path}  (open with QGIS/ArcGIS)")
    print()
    print("  Bounding box (WGS-84):")
    nw_lat, nw_lon = tile_to_lat_lon(x_range.start, y_range.start, zoom)
    se_lat, se_lon = tile_to_lat_lon(x_range.stop,  y_range.stop,  zoom)
    print(f"    North: {nw_lat:.6f}°  South: {se_lat:.6f}°")
    print(f"    West:  {nw_lon:.6f}°  East:  {se_lon:.6f}°")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
