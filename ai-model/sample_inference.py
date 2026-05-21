#!/usr/bin/env python3
"""
Single-image YOLOv11 Segmentation Inference
============================================
Runs best.pt on one image, draws coloured segmentation masks + labels,
and saves the annotated result.

Usage:
    python sample_inference.py                          # uses sample.jpg if present
    python sample_inference.py --image path/to/img.jpg
    python sample_inference.py --image photo.jpg --output result.jpg
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

# ── constants ─────────────────────────────────────────────────────────────────
WORKSPACE  = Path(__file__).parent
MODEL_PATH = WORKSPACE / "best.pt"

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

# ── default inference params ──────────────────────────────────────────────────
DEFAULT_CONF       = 0.10
DEFAULT_IOU        = 0.45
DEFAULT_MASK_ALPHA = 0.40
DEFAULT_IMGSZ      = 640


def color_for_class(cls_idx: int):
    return PALETTE[int(cls_idx) % len(PALETTE)]


def draw_masks(img_bgr: np.ndarray, result, mask_alpha: float = DEFAULT_MASK_ALPHA) -> np.ndarray:
    annotated = img_bgr.copy()
    h, w = img_bgr.shape[:2]

    if result.masks is None:
        return annotated

    masks   = result.masks.data.cpu().numpy()
    boxes   = result.boxes
    classes = boxes.cls.cpu().numpy().astype(int) if boxes is not None else []
    confs   = boxes.conf.cpu().numpy()            if boxes is not None else []

    overlay = annotated.copy()

    for i, raw_mask in enumerate(masks):
        cls_idx   = int(classes[i]) if i < len(classes) else 0
        color_rgb = color_for_class(cls_idx)
        color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])

        mask_resized = cv2.resize(
            raw_mask, (w, h), interpolation=cv2.INTER_NEAREST
        ).astype(np.uint8)

        overlay[mask_resized == 1] = color_bgr
        annotated = cv2.addWeighted(overlay, mask_alpha, annotated, 1 - mask_alpha, 0)
        overlay   = annotated.copy()

        contours, _ = cv2.findContours(
            mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(annotated, contours, -1, color_bgr, 2)

        if boxes is not None and i < len(confs):
            cls_name = result.names.get(cls_idx, str(cls_idx))
            label    = f"{cls_name} {confs[i]:.2f}"
            xyxy     = boxes.xyxy[i].cpu().numpy().astype(int)
            tx, ty   = max(xyxy[0], 0), max(xyxy[1] - 6, 12)
            cv2.putText(
                annotated, label, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_bgr, 1, cv2.LINE_AA,
            )

    return annotated


def _has_mps() -> bool:
    try:
        import torch
        return torch.backends.mps.is_available()
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Single-image YOLOv11-seg inference",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--image",      default="sample.jpg",               help="Input image path")
    parser.add_argument("--model",      default=str(MODEL_PATH),             help="Path to best.pt")
    parser.add_argument("--output",     default="sample_output.jpg",         help="Annotated output path")
    parser.add_argument("--conf",       type=float, default=DEFAULT_CONF,    help="Confidence threshold")
    parser.add_argument("--iou",        type=float, default=DEFAULT_IOU,     help="NMS IoU threshold")
    parser.add_argument("--mask-alpha", type=float, default=DEFAULT_MASK_ALPHA, help="Mask opacity (0–1)")
    parser.add_argument("--imgsz",      type=int,   default=DEFAULT_IMGSZ,   help="Inference image size")
    parser.add_argument("--device",     default="",                          help="Device: cpu / 0 / mps")
    args = parser.parse_args()

    image_path = Path(args.image)
    model_path = Path(args.model)
    output     = Path(args.output)

    if not model_path.exists():
        sys.exit(f"[error] Model not found: {model_path}")
    if not image_path.exists():
        sys.exit(f"[error] Image not found: {image_path}")

    device = args.device if args.device else ("mps" if _has_mps() else "cpu")

    print(f"[info] Loading model : {model_path}")
    model = YOLO(str(model_path))
    print(f"[info] Device        : {device}")
    print(f"[info] Image         : {image_path}")

    img_bgr = cv2.imread(str(image_path))
    if img_bgr is None:
        sys.exit(f"[error] Could not read image: {image_path}")

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
    print(f"[info] Detections    : {n_det}")

    annotated = draw_masks(img_bgr, result, mask_alpha=args.mask_alpha)

    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), annotated)
    print(f"[done] Saved         : {output}")


if __name__ == "__main__":
    main()
