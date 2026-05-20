#!/usr/bin/env python3
"""
Construction Detection + Segmentation in Satellite Tiles
=========================================================
Two-stage pipeline per tile:

  Stage 1  YOLO-World (yolov8s-worldv2.pt)
           Zero-shot open-vocabulary detector -> bounding boxes + class labels.
           Text prompts describe construction stages as seen from above.

  Stage 2  SAM -- Segment Anything Model (sam_b.pt)
           Prompted by the Stage-1 bounding boxes -> pixel-accurate instance
           masks.  Contour lines are drawn over the annotated tile.
           Disabled with --no-segment.

Outputs (inside  detection_results/):
  annotated/      - tiles with coloured mask fills + contour lines + bbox labels
  report.csv      - z, x, y, class, conf, bbox, mask_area_px, lat/lon
  report.geojson  - GeoJSON FeatureCollection:
                      * MultiPolygon footprints when segmentation is ON
                      * Point centroids when segmentation is OFF
  summary.txt     - per-class & per-zoom counts

Usage:
    python detect_construction.py --zoom 18          # zoom 18 + segmentation
    python detect_construction.py --zoom 18 20       # multiple zooms
    python detect_construction.py --no-segment       # detection only (faster)
    python detect_construction.py --conf 0.12        # confidence threshold
    python detect_construction.py --limit 50         # quick test
    python detect_construction.py --resume           # skip already-done tiles

Dependencies (auto-installed if missing):
    ultralytics>=8.0, torch, Pillow, tqdm, opencv-python-headless, numpy
"""

import argparse
import csv
import json
import math
import sys
import numpy as np
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# -- auto-install helpers -----------------------------------------------------
def _ensure(pkg, import_as=None):
    import importlib
    name = import_as or pkg
    try:
        return importlib.import_module(name)
    except ImportError:
        import subprocess
        print(f"[setup] Installing {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])
        return importlib.import_module(name)

_ensure("ultralytics")
_ensure("tqdm")
_ensure("Pillow", "PIL")
_ensure("opencv-python-headless", "cv2")

import cv2
from ultralytics import YOLOWorld, SAM
from tqdm import tqdm

# -- constants ----------------------------------------------------------------
WORKSPACE = Path(__file__).parent
TILES_DIR  = WORKSPACE / "tiles_cache" / "google"
OUT_DIR    = WORKSPACE / "detection_results"

# Text prompts tuned for aerial/satellite top-down view.
CONSTRUCTION_CLASSES = [
    "bare earth rectangular area",      # excavation pit or cleared plot
    "construction site",                # active site with materials/machinery
    "building under construction",      # partial walls / shell from above
    "concrete foundation slab",         # gray rectangular concrete pad
    "excavation",                       # open pit / trench
]

# Per-class BGR colours for OpenCV drawing
CLASS_COLORS = [
    (0,   200, 255),   # bare earth rectangular area  -- amber/orange
    (0,   255,   0),   # construction site             -- green
    (255,  80,  80),   # building under construction   -- blue
    (80,  255, 255),   # concrete foundation slab      -- yellow
    (255,   0, 200),   # excavation                    -- magenta
]

# -- tile math ----------------------------------------------------------------
def tile_to_lat_lon(x, y, zoom):
    """Return (lat, lon) of the NW corner of tile (x, y) at zoom."""
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon

def tile_bbox_lat_lon(x, y, zoom):
    """Return (lat_max, lon_min, lat_min, lon_max) for a tile."""
    lat_nw, lon_nw = tile_to_lat_lon(x, y, zoom)
    lat_se, lon_se = tile_to_lat_lon(x + 1, y + 1, zoom)
    return lat_nw, lon_nw, lat_se, lon_se

def pixel_to_lat_lon(px, py, tile_w, tile_h, x, y, zoom):
    """Convert a pixel coordinate within a tile to (lat, lon)."""
    lat_n, lon_w, lat_s, lon_e = tile_bbox_lat_lon(x, y, zoom)
    lon = lon_w + (px / tile_w) * (lon_e - lon_w)
    lat = lat_n + (py / tile_h) * (lat_s - lat_n)
    return lat, lon

def contour_to_geojson_ring(contour, tile_w, tile_h, x, y, zoom):
    """Convert an OpenCV contour (N,1,2) to a closed GeoJSON ring [[lon,lat],...]."""
    ring = []
    for pt in contour.reshape(-1, 2):
        lat, lon = pixel_to_lat_lon(float(pt[0]), float(pt[1]),
                                    tile_w, tile_h, x, y, zoom)
        ring.append([round(lon, 7), round(lat, 7)])
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])   # close the ring
    return ring

# -- tile collector -----------------------------------------------------------
def collect_tiles(tiles_dir, zoom_filter=None):
    tiles = []
    for z_dir in sorted(tiles_dir.iterdir()):
        if not z_dir.is_dir():
            continue
        try:
            z = int(z_dir.name)
        except ValueError:
            continue
        if zoom_filter and z not in zoom_filter:
            continue
        for x_dir in sorted(z_dir.iterdir()):
            if not x_dir.is_dir():
                continue
            x = int(x_dir.name)
            for img_file in sorted(x_dir.iterdir()):
                if img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    y = int(img_file.stem)
                    tiles.append((z, x, y, img_file))
    return tiles

# -- GeoJSON feature builder --------------------------------------------------
def build_geojson_feature(z, x, y, detections, masks_geo=None):
    lat_n, lon_w, lat_s, lon_e = tile_bbox_lat_lon(x, y, z)
    props = {
        "z": z, "x": x, "y": y,
        "tile": f"{z}/{x}/{y}",
        "lat_n": round(lat_n, 7), "lon_w": round(lon_w, 7),
        "lat_s": round(lat_s, 7), "lon_e": round(lon_e, 7),
        "detection_count": len(detections),
        "classes": list({d["class"] for d in detections}),
        "max_conf": round(max(d["confidence"] for d in detections), 4),
        "detections": detections,
    }
    if masks_geo:
        polys = [[[ring]] for ring in masks_geo if ring and len(ring) >= 4]
        geometry = {"type": "MultiPolygon", "coordinates": polys} if polys else {
            "type": "Point",
            "coordinates": [(lon_w + lon_e) / 2, (lat_n + lat_s) / 2],
        }
    else:
        geometry = {
            "type": "Point",
            "coordinates": [(lon_w + lon_e) / 2, (lat_n + lat_s) / 2],
        }
    return {"type": "Feature", "geometry": geometry, "properties": props}

# -- annotation drawing -------------------------------------------------------
def draw_tile(img_bgr, detections, masks_np):
    """
    Draw segmentation masks (filled + contour lines) and detection boxes.
      masks_np   : numpy array (N, H, W) float32 from SAM, or None
      detections : list of dicts with keys cls_id, class, confidence, bbox_px
    Returns annotated BGR image (copy).
    """
    out     = img_bgr.copy()
    overlay = img_bgr.copy()

    # mask fills (semi-transparent)
    if masks_np is not None:
        for i, mask in enumerate(masks_np):
            cls_id = detections[i]["cls_id"] if i < len(detections) else 0
            color  = CLASS_COLORS[cls_id % len(CLASS_COLORS)]
            overlay[mask > 0.5] = color
        cv2.addWeighted(overlay, 0.30, out, 0.70, 0, out)

        # contour lines drawn on blended image
        for i, mask in enumerate(masks_np):
            cls_id = detections[i]["cls_id"] if i < len(detections) else 0
            color  = CLASS_COLORS[cls_id % len(CLASS_COLORS)]
            mask_u8 = (mask > 0.5).astype(np.uint8) * 255
            contours, _ = cv2.findContours(
                mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(out, contours, -1, color, 2)

    # bounding boxes + labels
    for det in detections:
        cls_id = det["cls_id"]
        color  = CLASS_COLORS[cls_id % len(CLASS_COLORS)]
        x1, y1, x2, y2 = [int(v) for v in det["bbox_px"]]
        label  = f"{det['class'].split()[0]}:{det['confidence']:.2f}"
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        cv2.rectangle(out, (x1, y1 - th - 4), (x1 + tw + 2, y1), color, -1)
        cv2.putText(out, label, (x1 + 1, y1 - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0), 1, cv2.LINE_AA)
    return out

# -- main ---------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Detect + segment construction objects in satellite tiles.")
    parser.add_argument("--zoom",        nargs="+", type=int, default=None,
                        help="Zoom level(s) to process (default: all)")
    parser.add_argument("--conf",        type=float, default=0.12,
                        help="YOLO confidence threshold (default: 0.12)")
    parser.add_argument("--iou",         type=float, default=0.45,
                        help="YOLO NMS IoU threshold (default: 0.45)")
    parser.add_argument("--imgsz",       type=int,   default=640,
                        help="YOLO inference image size (default: 640)")
    parser.add_argument("--limit",       type=int,   default=None,
                        help="Process only first N tiles (testing)")
    parser.add_argument("--no-annotate", action="store_true",
                        help="Skip saving annotated tile images")
    parser.add_argument("--no-segment",  action="store_true",
                        help="Skip SAM segmentation (bboxes only, much faster)")
    parser.add_argument("--sam-model",   type=str,   default="sam_b.pt",
                        help="SAM checkpoint: sam_b.pt | sam2_b.pt | FastSAM-s.pt")
    parser.add_argument("--tiles-dir",   type=Path,  default=TILES_DIR,
                        help="Root tile directory (default: tiles_cache/google)")
    parser.add_argument("--out-dir",     type=Path,  default=OUT_DIR,
                        help="Output directory (default: detection_results/)")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip tiles already recorded in report.csv")
    args = parser.parse_args()

    do_segment    = not args.no_segment
    out_dir       = args.out_dir
    annotated_dir = out_dir / "annotated"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_annotate:
        annotated_dir.mkdir(parents=True, exist_ok=True)

    csv_path     = out_dir / "report.csv"
    geojson_path = out_dir / "report.geojson"
    summary_path = out_dir / "summary.txt"

    # -- load Stage-1: YOLO-World ---------------------------------------------
    print("\n[Stage 1] Loading YOLO-World (yolov8s-worldv2.pt) ...")
    print("          Zero-shot open-vocabulary detector -- no labelled satellite")
    print("          training data required; objects defined by text prompts.\n")
    det_model = YOLOWorld("yolov8s-worldv2.pt")
    det_model.set_classes(CONSTRUCTION_CLASSES)
    print(f"[Stage 1] Classes: {CONSTRUCTION_CLASSES}\n")

    # -- load Stage-2: SAM ----------------------------------------------------
    sam_model = None
    if do_segment:
        print(f"[Stage 2] Loading SAM ({args.sam_model}) for instance segmentation ...")
        print("          First run downloads ~375 MB.")
        print("          SAM is prompted by YOLO bounding boxes -> pixel masks.\n")
        sam_model = SAM(args.sam_model)

    # -- collect tiles ---------------------------------------------------------
    zoom_filter = set(args.zoom) if args.zoom else None
    all_tiles   = collect_tiles(args.tiles_dir, zoom_filter)
    if not all_tiles:
        print("[error] No tiles found. Check --tiles-dir.")
        sys.exit(1)
    print(f"[tiles]   Found {len(all_tiles):,} tiles "
          f"(zooms: {sorted({t[0] for t in all_tiles})})")

    # -- resume ----------------------------------------------------------------
    processed = set()
    if args.resume and csv_path.exists():
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                processed.add((int(row["z"]), int(row["x"]), int(row["y"])))
        if processed:
            all_tiles = [(z, x, y, p) for (z, x, y, p) in all_tiles
                         if (z, x, y) not in processed]
            print(f"[resume]  Skipping {len(processed):,} done. "
                  f"{len(all_tiles):,} remaining.\n")

    if args.limit:
        all_tiles = all_tiles[:args.limit]
        print(f"[limit]   Processing first {len(all_tiles)} tiles only.\n")

    # -- open CSV --------------------------------------------------------------
    csv_mode   = "a" if (args.resume and csv_path.exists()) else "w"
    csv_file   = open(csv_path, csv_mode, newline="")
    fieldnames = ["z", "x", "y", "lat_center", "lon_center",
                  "class", "confidence", "x1", "y1", "x2", "y2",
                  "mask_area_px", "tile_path"]
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    if csv_mode == "w":
        writer.writeheader()

    # -- detection + segmentation loop ----------------------------------------
    stats_total      = 0
    stats_with_det   = 0
    stats_with_mask  = 0
    class_counts     = defaultdict(int)
    zoom_counts      = defaultdict(int)
    geojson_features = []

    print(f"[run]     conf={args.conf}  iou={args.iou}  imgsz={args.imgsz}  "
          f"segment={'ON (' + args.sam_model + ')' if do_segment else 'OFF'}\n")

    pbar = tqdm(all_tiles, unit="tile", dynamic_ncols=True)
    for z, x, y, tile_path in pbar:
        stats_total += 1
        pbar.set_description(f"z={z} x={x} y={y}")

        # Stage 1: YOLO-World detection
        try:
            det_results = det_model.predict(
                source=str(tile_path),
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                verbose=False,
            )
        except Exception as e:
            tqdm.write(f"[warn] YOLO failed on {tile_path}: {e}")
            continue

        res   = det_results[0]
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            continue

        stats_with_det += 1
        zoom_counts[z] += 1
        tile_h, tile_w = res.orig_shape[0], res.orig_shape[1]

        tile_detections = []
        for i in range(len(boxes)):
            cls_id   = int(boxes.cls[i].item())
            cls_name = (CONSTRUCTION_CLASSES[cls_id]
                        if cls_id < len(CONSTRUCTION_CLASSES) else f"cls_{cls_id}")
            conf     = float(boxes.conf[i].item())
            xyxy     = boxes.xyxy[i].tolist()
            cx_px    = (xyxy[0] + xyxy[2]) / 2
            cy_px    = (xyxy[1] + xyxy[3]) / 2
            det_lat, det_lon = pixel_to_lat_lon(cx_px, cy_px, tile_w, tile_h, x, y, z)
            class_counts[cls_name] += 1
            tile_detections.append({
                "cls_id":       cls_id,
                "class":        cls_name,
                "confidence":   round(conf, 4),
                "bbox_px":      [round(v, 1) for v in xyxy],
                "lat":          round(det_lat, 7),
                "lon":          round(det_lon, 7),
                "mask_area_px": None,
            })

        # Stage 2: SAM segmentation
        masks_np  = None   # (N, H, W) float32
        masks_geo = []     # GeoJSON coordinate rings

        if do_segment and sam_model is not None:
            bboxes_list = [d["bbox_px"] for d in tile_detections]
            try:
                sam_results = sam_model(str(tile_path),
                                        bboxes=bboxes_list, verbose=False)
                sr = sam_results[0]
                if sr.masks is not None and len(sr.masks.data) > 0:
                    masks_np = sr.masks.data.cpu().numpy()  # (N, H, W)
                    stats_with_mask += 1
                    for i, mask in enumerate(masks_np):
                        mask_u8  = (mask > 0.5).astype(np.uint8) * 255
                        area_px  = int(np.sum(mask > 0.5))
                        if i < len(tile_detections):
                            tile_detections[i]["mask_area_px"] = area_px
                        contours, _ = cv2.findContours(
                            mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            largest = max(contours, key=cv2.contourArea)
                            eps     = 0.008 * cv2.arcLength(largest, True)
                            approx  = cv2.approxPolyDP(largest, eps, True)
                            ring    = contour_to_geojson_ring(
                                approx, tile_w, tile_h, x, y, z)
                            masks_geo.append(ring)
                        else:
                            masks_geo.append(None)
            except Exception as e:
                tqdm.write(f"[warn] SAM failed on {tile_path}: {e}")

        # write CSV rows
        for det in tile_detections:
            writer.writerow({
                "z": z, "x": x, "y": y,
                "lat_center":   det["lat"],
                "lon_center":   det["lon"],
                "class":        det["class"],
                "confidence":   det["confidence"],
                "x1": det["bbox_px"][0], "y1": det["bbox_px"][1],
                "x2": det["bbox_px"][2], "y2": det["bbox_px"][3],
                "mask_area_px": det["mask_area_px"] if det["mask_area_px"] is not None else "",
                "tile_path":    str(tile_path.relative_to(WORKSPACE)),
            })

        geojson_features.append(
            build_geojson_feature(z, x, y, tile_detections,
                                  masks_geo if masks_geo else None))

        # annotate tile with masks + contours + boxes
        if not args.no_annotate:
            img_bgr = cv2.imread(str(tile_path))
            if img_bgr is not None:
                ann = draw_tile(img_bgr, tile_detections, masks_np)
                ann_dir = annotated_dir / str(z) / str(x)
                ann_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(ann_dir / f"{y}.jpg"), ann,
                            [cv2.IMWRITE_JPEG_QUALITY, 90])

        n_masks = len(masks_np) if masks_np is not None else 0
        tqdm.write(
            f"  DETECT  z={z} x={x} y={y}  "
            f"{len(tile_detections)} boxes  {n_masks} masks  "
            f"classes={[d['class'].split()[0] for d in tile_detections]}"
        )

    csv_file.close()

    # write GeoJSON
    geojson = {
        "type": "FeatureCollection",
        "name": "construction_detections",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "generated": datetime.utcnow().isoformat() + "Z",
        "features": geojson_features,
    }
    with open(geojson_path, "w") as f:
        json.dump(geojson, f, indent=2)

    # write summary
    lines = [
        "=" * 60,
        "  Construction Detection + Segmentation Summary",
        f"  Run at         : {datetime.utcnow().isoformat()}Z",
        f"  Segmentation   : {'ON  (' + args.sam_model + ')' if do_segment else 'OFF (--no-segment)'}",
        "=" * 60,
        f"  Total tiles processed    : {stats_total:,}",
        f"  Tiles with detections    : {stats_with_det:,}  "
          f"({stats_with_det/max(stats_total,1)*100:.1f}%)",
        f"  Tiles with mask segments : {stats_with_mask:,}",
        "",
        "  Detections by class:",
    ]
    for cls, cnt in sorted(class_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {cls:<42} {cnt:>6,}")
    lines += ["", "  Tiles with detections by zoom:"]
    for zz, cnt in sorted(zoom_counts.items()):
        lines.append(f"    zoom {zz}  :  {cnt:,} tiles")
    lines += [
        "",
        "  Output files:",
        f"    {csv_path}",
        f"    {geojson_path}",
        f"    {annotated_dir}  (tiles with masks + contours)",
        "=" * 60,
    ]
    summary_text = "\n".join(lines)
    with open(summary_path, "w") as f:
        f.write(summary_text)
    print("\n" + summary_text)


if __name__ == "__main__":
    main()
