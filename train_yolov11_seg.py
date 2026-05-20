"""
YOLOv11 Instance Segmentation Training Script
Dataset: construction site detection from satellite tiles
Annotation: polygon (up to 4 vertices), YOLO segment format
"""

import os
import shutil
import random
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────
DATASET_ROOT   = Path("/Users/mqodir/Desktop/tiles/dataset/anotated_data")
OUTPUT_ROOT    = Path("/Users/mqodir/Desktop/tiles/dataset/split")
DATA_YAML      = Path("/Users/mqodir/Desktop/tiles/dataset/data.yaml")

TRAIN_RATIO    = 0.80
VAL_RATIO      = 0.15
TEST_RATIO     = 0.05   # remaining

MODEL_WEIGHTS  = "yolo11n-seg.pt"   # nano seg model; change to yolo11s-seg.pt / yolo11m-seg.pt etc.
EPOCHS         = 100
IMG_SIZE       = 256                # tile size (256×256 pixels)
BATCH_SIZE     = 16
DEVICE         = "0"                # "0" for first GPU, "cpu" for CPU-only
PROJECT        = "/Users/mqodir/Desktop/tiles/runs/segment"
RUN_NAME       = "construction_v1"
# ──────────────────────────────────────────────────────────────────────────────


def split_dataset(seed: int = 42) -> Path:
    """
    Split images/labels into train / val / test sets.
    Returns the path to the updated data YAML for the split dataset.
    """
    images_dir = DATASET_ROOT / "images"
    labels_dir = DATASET_ROOT / "labels"

    # Collect stems that have both an image and a label
    stems = sorted([
        p.stem for p in images_dir.glob("*.jpg")
        if (labels_dir / f"{p.stem}.txt").exists()
    ])

    if not stems:
        raise FileNotFoundError(f"No matched image/label pairs found in {DATASET_ROOT}")

    random.seed(seed)
    random.shuffle(stems)

    n       = len(stems)
    n_train = int(n * TRAIN_RATIO)
    n_val   = int(n * VAL_RATIO)

    splits = {
        "train": stems[:n_train],
        "val":   stems[n_train : n_train + n_val],
        "test":  stems[n_train + n_val :],
    }

    print(f"Dataset split  →  train: {len(splits['train'])}  "
          f"val: {len(splits['val'])}  test: {len(splits['test'])}")

    # Build directory tree and copy files
    for split, split_stems in splits.items():
        (OUTPUT_ROOT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUTPUT_ROOT / split / "labels").mkdir(parents=True, exist_ok=True)
        for stem in split_stems:
            shutil.copy2(images_dir / f"{stem}.jpg",
                         OUTPUT_ROOT / split / "images" / f"{stem}.jpg")
            shutil.copy2(labels_dir / f"{stem}.txt",
                         OUTPUT_ROOT / split / "labels" / f"{stem}.txt")

    # Write a data YAML that points to the split dataset
    split_yaml = OUTPUT_ROOT / "data.yaml"
    split_yaml.write_text(
        f"path: {OUTPUT_ROOT}\n"
        "train: train/images\n"
        "val:   val/images\n"
        "test:  test/images\n\n"
        "nc: 1\n\n"
        "names:\n"
        "  0: construction\n\n"
        "task: segment\n"
    )
    print(f"Split YAML written to {split_yaml}")
    return split_yaml


def train(data_yaml: Path) -> None:
    """Run YOLOv11 segmentation training."""
    try:
        from ultralytics import YOLO
    except ImportError:
        raise ImportError(
            "ultralytics is not installed. "
            "Run:  pip install ultralytics"
        )

    model = YOLO(MODEL_WEIGHTS)  # downloads weights on first run if not present

    results = model.train(
        data=str(data_yaml),
        task="segment",
        epochs=EPOCHS,
        imgsz=IMG_SIZE,
        batch=BATCH_SIZE,
        device=DEVICE,
        project=PROJECT,
        name=RUN_NAME,
        # Augmentation — conservative settings for satellite imagery
        hsv_h=0.01,     # small hue shift (satellite colours are stable)
        hsv_s=0.4,
        hsv_v=0.3,
        degrees=90.0,   # allow 90° rotations (top-down view is rotation-invariant)
        flipud=0.5,
        fliplr=0.5,
        mosaic=0.5,
        # Polygon-specific
        overlap_mask=True,
        mask_ratio=4,
        # Early stopping
        patience=30,
        # Reproducibility
        seed=42,
        # Logging
        save=True,
        save_period=10,
        val=True,
        plots=True,
    )
    print(f"\nTraining complete. Results saved to: {results.save_dir}")


if __name__ == "__main__":
    # Step 1 – split dataset (skips if output already exists)
    if OUTPUT_ROOT.exists():
        print(f"Split directory already exists at {OUTPUT_ROOT}, skipping split step.")
        split_yaml = OUTPUT_ROOT / "data.yaml"
    else:
        split_yaml = split_dataset()

    # Step 2 – train
    train(split_yaml)
