#!/usr/bin/env python3
"""
Sample single-image inference with YOLOv11 segmentation.

Usage:
    python sample_inference.py --image path/to/image.jpg
    python sample_inference.py --image path/to/image.jpg --output result.png
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

WORKSPACE  = Path(__file__).parent
MODEL_PATH = WORKSPACE / "best.pt"

PALETTE = [
    (255,  56,  56),
    ( 56, 255,  56),
    ( 56,  56, 255),
    (255, 255,  56),
    (255,  56, 255),
    ( 56, 255, 255),
    (255, 165,   0),
    (128,   0, 255),
]


def color_for_class(cls_idx: int):
    return PALETTE[int(cls_idx) % len(PALETTE)]


def draw_masks(img_bgr: np.ndarray, result, mask_alpha: float = 0.40) -> np.ndarray:
    annotated = img_bgr.copy()
    h, w = img_bgr.shape[:2]

    if result.masks is None:
        print("[info] No masks detected.")
        return annotated

    masks   = result.masks.data.cpu().numpy()
    boxes   = result.boxes
    classes = boxes.cls.cpu().numpy().astype(int) if boxes is not None else []
    confs   = boxes.conf.cpu().numpy()            if boxes is not None else []

    overlay = annotated.copy()

    for i, raw_mask in enumerate(masks):
        cls_idx  = int(classes[i]) if i < len(classes) else 0
        color    = color_for_class(cls_idx)
        color_bgr = (color[2], color[1], color[0])

        mask_resized = cv2.resize(
            raw_mask, (w, h), interpolation=cv2.INTER_NEAREST
        ).astype(np.uint8)

        overlay[mask_resized == 1] = color_bgr
        annotated = cv2.addWeighted(overlay, mask_alpha, annotated, 1 - mask_alpha, 0)
        overlay = annotated.copy()

        contours, _ = cv2.findContours(
            mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(annotated, contours, -1, color_bgr, 1)

        if boxes is not None and i < len(confs):
            cls_name = result.names.get(cls_idx, str(cls_idx))
            label    = f"{cls_name} {confs[i]:.2f}"
            xyxy     = boxes.xyxy[i].cpu().numpy().astype(int)
            tx, ty   = max(xyxy[0], 0), max(xyxy[1] - 4, 10)
            cv2.putText(
                annotated, label, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_bgr, 1, cv2.LINE_AA,
            )

    return annotated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YOLOv11-seg inference on a single image",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--image",      required=True,              help="Path to input image")
    parser.add_argument("--model",      default=str(MODEL_PATH),    help="Path to best.pt")
    parser.add_argument("--output",     default="result.png",       help="Output image path")
    parser.add_argument("--conf",       type=float, default=0.25,   help="Confidence threshold")
    parser.add_argument("--iou",        type=float, default=0.45,   help="NMS IoU threshold")
    parser.add_argument("--imgsz",      type=int,   default=640,    help="Inference image size")
    parser.add_argument("--mask-alpha", type=float, default=0.40,   help="Mask opacity (0–1)")
    args = parser.parse_args()

    image_path = Path(args.image)
    model_path = Path(args.model)
    output     = Path(args.output)

    if not image_path.exists():
        sys.exit(f"[error] Image not found: {image_path}")
    if not model_path.exists():
        sys.exit(f"[error] Model not found: {model_path}")

    print(f"[info] Loading model: {model_path}")
    model = YOLO(str(model_path))

    print(f"[info] Running inference on: {image_path}")
    img_bgr = cv2.imread(str(image_path))
    if img_bgr is None:
        sys.exit(f"[error] Could not read image: {image_path}")

    results = model.predict(
        source=img_bgr,
        conf=args.conf,
        iou=args.iou,
        imgsz=args.imgsz,
        verbose=False,
    )
    result = results[0]

    n_det = len(result.boxes) if result.boxes is not None else 0
    print(f"[info] Detections: {n_det}")

    annotated = draw_masks(img_bgr, result, mask_alpha=args.mask_alpha)

    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), annotated)
    print(f"[done] Saved → {output}")


if __name__ == "__main__":
    main()
