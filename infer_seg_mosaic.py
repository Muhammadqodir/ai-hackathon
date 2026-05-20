#!/usr/bin/env python3
"""
YOLOv11 Segmentation Inference + Mosaic Stitcher
=================================================
1. Runs best.pt (YOLOv11-seg) on every tile in tiles_cache/google/18/{x}/{y}.jpg
2. Draws coloured segmentation masks + outlines on each tile (in-memory).
3. Stitches all annotated tiles into ONE lossless PNG mosaic, preserving the
   spatial grid so the result is pixel-accurate.

Usage:
    python infer_seg_mosaic.py
    python infer_seg_mosaic.py --conf 0.25 --output my_mosaic.png
    python infer_seg_mosaic.py --iou 0.45 --mask-alpha 0.45

Output:
    mosaic_segmented.png  (lossless, full resolution, one pixel per tile pixel)
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Tuple

# ── auto-install helpers ──────────────────────────────────────────────────────
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

_ensure("ultralytics")
_ensure("Pillow", "PIL")
_ensure("numpy")
_ensure("tqdm")
_ensure("opencv-python-headless", "cv2")

import cv2
import numpy as np
from PIL import Image
from tqdm import tqdm
from ultralytics import YOLO

# ── constants ─────────────────────────────────────────────────────────────────
WORKSPACE  = Path(__file__).parent
TILES_ROOT = WORKSPACE / "margilon" / "tiles_cache" / "google" / "18"
MODEL_PATH = WORKSPACE / "best.pt"
TILE_SIZE  = 256  # pixels per tile (standard slippy-map tile)

# Colour palette for segmentation classes (BGR for OpenCV, converted to RGB later)
PALETTE = [
    (255,  56,  56),   # class 0 – construction (red)
    ( 56, 255,  56),   # class 1
    ( 56,  56, 255),   # class 2
    (255, 255,  56),   # class 3
    (255,  56, 255),   # class 4
    ( 56, 255, 255),   # class 5
    (255, 165,   0),   # class 6
    (128,   0, 255),   # class 7
]


# ── helpers ───────────────────────────────────────────────────────────────────

def collect_tiles(root: Path) -> Dict[Tuple[int, int], Path]:
    """
    Walk  root/{x}/{y}.jpg  and return {(x, y): path} for every tile found.
    """
    tiles: Dict[Tuple[int, int], Path] = {}
    for x_dir in sorted(root.iterdir()):
        if not x_dir.is_dir():
            continue
        try:
            x = int(x_dir.name)
        except ValueError:
            continue
        for tile_file in sorted(x_dir.iterdir()):
            if tile_file.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            try:
                y = int(tile_file.stem)
            except ValueError:
                continue
            tiles[(x, y)] = tile_file
    return tiles


def color_for_class(cls_idx: int) -> Tuple[int, int, int]:
    return PALETTE[int(cls_idx) % len(PALETTE)]


def draw_masks_on_tile(
    img_bgr: np.ndarray,
    result,
    mask_alpha: float = 0.40,
) -> np.ndarray:
    """
    Overlay segmentation masks from a single YOLO result onto img_bgr.
    Returns a new annotated BGR array (same size as input).
    """
    annotated = img_bgr.copy()
    h, w = img_bgr.shape[:2]

    if result.masks is None:
        return annotated

    masks  = result.masks.data.cpu().numpy()   # (N, H', W')
    boxes  = result.boxes
    classes = boxes.cls.cpu().numpy().astype(int) if boxes is not None else []
    confs   = boxes.conf.cpu().numpy()          if boxes is not None else []

    overlay = annotated.copy()

    for i, raw_mask in enumerate(masks):
        cls_idx = int(classes[i]) if i < len(classes) else 0
        color   = color_for_class(cls_idx)  # RGB tuple

        # Resize mask to tile resolution if needed
        mask_resized = cv2.resize(
            raw_mask, (w, h), interpolation=cv2.INTER_NEAREST
        ).astype(np.uint8)

        # Fill semi-transparent colour
        color_bgr = (color[2], color[1], color[0])  # convert RGB -> BGR
        overlay[mask_resized == 1] = color_bgr
        annotated = cv2.addWeighted(overlay, mask_alpha, annotated, 1 - mask_alpha, 0)
        overlay = annotated.copy()  # accumulate

        # Draw contour outline
        contours, _ = cv2.findContours(
            mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(annotated, contours, -1, color_bgr, 1)

        # Label: class name + confidence
        if boxes is not None and i < len(confs):
            cls_name = result.names.get(cls_idx, str(cls_idx))
            label    = f"{cls_name} {confs[i]:.2f}"
            xyxy     = boxes.xyxy[i].cpu().numpy().astype(int)
            tx, ty   = max(xyxy[0], 0), max(xyxy[1] - 4, 10)
            cv2.putText(
                annotated, label, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.30, color_bgr, 1, cv2.LINE_AA,
            )

    return annotated


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YOLOv11-seg inference on cached tiles → lossless mosaic PNG",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model",      default=str(MODEL_PATH), help="Path to best.pt")
    parser.add_argument("--tiles-dir",  default=str(TILES_ROOT), help="Tiles root directory")
    parser.add_argument("--output",     default="mosaic_segmented.png", help="Output PNG path")
    parser.add_argument("--conf",       type=float, default=0.10, help="Confidence threshold")
    parser.add_argument("--iou",        type=float, default=0.45, help="NMS IoU threshold")
    parser.add_argument("--mask-alpha", type=float, default=0.40, help="Mask fill opacity (0–1)")
    parser.add_argument("--imgsz",      type=int,   default=256,  help="Inference image size")
    parser.add_argument("--device",     default="",  help="Inference device: cpu / 0 / mps / …")
    args = parser.parse_args()

    tiles_root = Path(args.tiles_dir)
    model_path = Path(args.model)
    output     = Path(args.output)

    # ── sanity checks ──────────────────────────────────────────────────────────
    if not model_path.exists():
        sys.exit(f"[error] Model not found: {model_path}")
    if not tiles_root.exists():
        sys.exit(f"[error] Tiles directory not found: {tiles_root}")

    # ── discover tiles ─────────────────────────────────────────────────────────
    print(f"[info] Scanning tiles in: {tiles_root}")
    tile_map = collect_tiles(tiles_root)
    if not tile_map:
        sys.exit("[error] No tile images found. Download tiles first (python download_tiles.py).")
    print(f"[info] Found {len(tile_map):,} tiles.")

    # ── load model ─────────────────────────────────────────────────────────────
    print(f"[info] Loading model: {model_path}")
    device = args.device if args.device else ("mps" if _has_mps() else "cpu")
    model  = YOLO(str(model_path))
    print(f"[info] Using device: {device}")

    # ── compute grid extents ───────────────────────────────────────────────────
    xs = [coord[0] for coord in tile_map]
    ys = [coord[1] for coord in tile_map]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    cols = x_max - x_min + 1
    rows = y_max - y_min + 1
    mosaic_w = cols * TILE_SIZE
    mosaic_h = rows * TILE_SIZE
    print(f"[info] Grid: x=[{x_min}..{x_max}] ({cols} cols)  "
          f"y=[{y_min}..{y_max}] ({rows} rows)")
    print(f"[info] Mosaic size: {mosaic_w} × {mosaic_h} px  "
          f"({mosaic_w * mosaic_h / 1e6:.1f} Mpx)")

    # Warn if mosaic is very large
    mpx = mosaic_w * mosaic_h / 1e6
    if mpx > 2000:
        print(f"[warn] Mosaic is {mpx:.0f} Mpx — this may require significant RAM. "
              "Consider filtering tiles to a smaller area.")

    # ── allocate mosaic canvas (RGB) ───────────────────────────────────────────
    print("[info] Allocating mosaic canvas …")
    mosaic = np.zeros((mosaic_h, mosaic_w, 3), dtype=np.uint8)

    # ── run inference tile by tile ─────────────────────────────────────────────
    detections_total = 0
    failed           = 0

    sorted_tiles = sorted(tile_map.items(), key=lambda kv: (kv[0][0], kv[0][1]))

    for (x, y), tile_path in tqdm(sorted_tiles, desc="Inferring", unit="tile"):
        try:
            img_bgr = cv2.imread(str(tile_path))
            if img_bgr is None:
                raise ValueError("cv2.imread returned None")

            # Run inference
            results = model.predict(
                source=img_bgr,
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                device=device,
                verbose=False,
            )
            result = results[0]

            n_det = len(result.boxes) if result.boxes is not None else 0
            detections_total += n_det

            # Annotate tile (draw masks even if 0 detections — tile still goes to mosaic)
            annotated_bgr = draw_masks_on_tile(img_bgr, result, mask_alpha=args.mask_alpha)

            # Convert BGR → RGB and paste into mosaic
            annotated_rgb = cv2.cvtColor(annotated_bgr, cv2.COLOR_BGR2RGB)

        except Exception as exc:
            tqdm.write(f"[warn] tile ({x},{y}) – {exc}")
            failed += 1
            # Fill with original tile if readable, else leave black
            try:
                img_bgr = cv2.imread(str(tile_path))
                if img_bgr is not None:
                    annotated_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                else:
                    annotated_rgb = np.zeros((TILE_SIZE, TILE_SIZE, 3), dtype=np.uint8)
            except Exception:
                annotated_rgb = np.zeros((TILE_SIZE, TILE_SIZE, 3), dtype=np.uint8)

        # Place tile in mosaic
        col = x - x_min
        row = y - y_min
        py  = row * TILE_SIZE
        px  = col * TILE_SIZE
        mosaic[py : py + TILE_SIZE, px : px + TILE_SIZE] = annotated_rgb

    # ── save mosaic as lossless PNG ────────────────────────────────────────────
    print(f"\n[info] Saving lossless PNG → {output} …")
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mosaic).save(str(output), format="PNG", compress_level=1)

    size_mb = output.stat().st_size / 1024 / 1024
    print(f"[done] Mosaic saved: {output}  ({size_mb:.1f} MB)")
    print(f"[done] Total detections: {detections_total:,}  |  failed tiles: {failed}")


def _has_mps() -> bool:
    """Return True if Apple Silicon MPS is available."""
    try:
        import torch
        return torch.backends.mps.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    main()
