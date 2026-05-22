"""
Parser for https://dshk.shaffofqurilish.uz/
Fetches construction objects and their detailed data from the Shaffof Qurilish API.

Endpoints:
  POST /api/list-construction  -> all objects (object_id, status, lat, long)
  POST /api/get-gasn-info      -> detailed info for a single object

Usage examples:
  # Fetch all objects and save JSON + CSV
  python shafofqurilish.py

  # Filter by status (0=all, 1=in-progress, 2=frozen, 3=stopped, 5=delivered)
  python shafofqurilish.py --status 1

  # Full run with details + MySQL SQL dump (all 26 000+ objects, 8 workers)
  # Default payload: country_id=0, sphere_id=0, status=2 (returns full dataset)
  python shafofqurilish.py --with-details --export-sql --workers 8 --out-dir ./output

  # SQL only, no CSV/JSON intermediate files
  python shafofqurilish.py --with-details --export-sql --no-csv --no-json

  # Custom table name in the SQL dump
  python shafofqurilish.py --with-details --export-sql --sql-table my_table
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api-dshk.shaffofqurilish.uz/api"
LIST_CONSTRUCTION_URL = f"{BASE_URL}/list-construction"
GASN_INFO_URL = f"{BASE_URL}/get-gasn-info"

# Human-readable status labels returned by the API
STATUS_LABELS: Dict[int, str] = {
    0: "all",
    1: "Jarayonda",       # In progress
    2: "Muzlatilgan",     # Frozen
    3: "Toxtatilgan",     # Stopped
    5: "Topshirilgan",    # Delivered / completed
}

DEFAULT_TIMEOUT = 30          # seconds per request
DEFAULT_WORKERS = 5           # concurrent detail fetches
DEFAULT_RETRY_TOTAL = 3       # max retries on transient errors
DEFAULT_BACKOFF = 1.0         # retry backoff factor
SQL_INSERT_BATCH = 500        # rows per INSERT statement
DEFAULT_SQL_TABLE = "shafof_qurilish_data"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP session with retry logic
# ---------------------------------------------------------------------------

def build_session(retries: int = DEFAULT_RETRY_TOTAL, backoff: float = DEFAULT_BACKOFF) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Content-Type": "application/json"})
    return session


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def fetch_object_list(
    session: requests.Session,
    country_id: int = 0,
    sphere_id: int = 0,
    status: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Fetch the list of all construction objects.

    Parameters
    ----------
    country_id : int
        Country filter (0 = all countries).
    sphere_id : int
        Sphere/sector filter (0 = all spheres).
    status : int
        Object status filter (0 = all statuses).

    Returns
    -------
    dict
        Parsed JSON response:
        {
          "code": 200,
          "message": "Success",
          "count": <int>,
          "stats": { ... },
          "stats_status": { ... },
          "data": [
            {"object_id": <int>, "object_status": <int>, "lat": <float>, "long": <float>},
            ...
          ]
        }
    """
    # The API requires at least country_id in the payload to produce a valid
    # SQL WHERE clause; sending an empty body triggers a 500 server bug.
    # Always include all three keys; the server treats 0 as "no filter".
    payload: Dict[str, Any] = {
        "country_id": country_id,
        "sphere_id": sphere_id,
        "status": status,
    }

    log.info("Fetching object list (country_id=%s, sphere_id=%s, status=%s) …", country_id, sphere_id, status)
    resp = session.post(LIST_CONSTRUCTION_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 200:
        raise RuntimeError(f"API error from list-construction: {data}")

    log.info("Received %d objects (total reported: %s).", len(data.get("data", [])), data.get("count"))
    return data


def fetch_object_detail(
    session: requests.Session,
    object_id: int,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """
    Fetch detailed information for a single construction object.

    Parameters
    ----------
    object_id : int
        The unique object identifier returned by list-construction.

    Returns
    -------
    dict or None
        Parsed ``data`` block from the response:
        {
          "id": <int>,
          "name": <str>,
          "task_id": <int>,
          "created_at": <str>,
          "sphere_id": <int>,
          "location_building": <str>,
          "difficulty": <str>,
          "organization_name": <str>,
          "loyiha": <str>,     # design organisation
          "pudrat": <str>,     # contractor
          "status": {"id": <int>, "name": <str>},
          "closed_at": <str|null>,
          "region_soato": <int>,
          "district_soato": <int>,
          "deadline": <str|null>,
          "lat": <str>,
          "long": <str>,
          "rating": <float|null>,
          "number_protocol": <str>,
          "reestr_number": <str|null>,
          "block_count": <int>,
          "apartment_count": <int>,
          "blocks": [{"id", "name", "apartment_count", "accepted", "area", "floor"}, ...],
          "conclusion": {"url": <str|null>}
        }
        Returns None on non-fatal errors (object not found, etc.).
    """
    resp = session.post(GASN_INFO_URL, json={"object_id": object_id}, timeout=timeout)
    if resp.status_code == 404:
        log.warning("Object %d not found (404).", object_id)
        return None
    resp.raise_for_status()
    body = resp.json()
    return body.get("data")


# ---------------------------------------------------------------------------
# Bulk detail fetching with concurrency
# ---------------------------------------------------------------------------

def fetch_all_details(
    session: requests.Session,
    object_ids: List[int],
    workers: int = DEFAULT_WORKERS,
    timeout: int = DEFAULT_TIMEOUT,
    delay: float = 0.0,
) -> Dict[int, Optional[Dict[str, Any]]]:
    """
    Fetch details for a list of object IDs concurrently.

    Returns a dict mapping object_id -> detail dict (or None on error).
    """
    results: Dict[int, Optional[Dict[str, Any]]] = {}
    total = len(object_ids)

    log.info("Fetching details for %d objects with %d workers …", total, workers)

    def _fetch(oid: int) -> tuple:
        if delay:
            time.sleep(delay)
        try:
            return oid, fetch_object_detail(session, oid, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.error("Error fetching detail for object %d: %s", oid, exc)
            return oid, None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch, oid): oid for oid in object_ids}
        done = 0
        for future in as_completed(futures):
            oid, detail = future.result()
            results[oid] = detail
            done += 1
            if done % 100 == 0 or done == total:
                log.info("  Progress: %d / %d", done, total)

    return results


# ---------------------------------------------------------------------------
# Flat row builder (for CSV export)
# ---------------------------------------------------------------------------

def flatten_detail(obj_summary: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge the list-level summary with the detail response into a single flat dict.
    Nested structures (blocks list, status dict, conclusion dict) are serialised
    to JSON strings so they fit into a CSV cell.
    """
    row: Dict[str, Any] = {
        "object_id":     obj_summary["object_id"],
        "object_status": obj_summary["object_status"],
        # Use detail lat/lon when available; fall back to list values
        "lat":           obj_summary["lat"],
        "lon":           obj_summary["long"],   # 'long' is reserved in MySQL → lon
    }

    if not detail:
        return row

    status_block = detail.get("status") or {}

    # The API returns `rating` as either null or a JSON-encoded string.
    # Normalise it to a parsed object so it can be stored as proper JSON.
    raw_rating = detail.get("rating")
    if isinstance(raw_rating, str):
        try:
            rating_value = json.loads(raw_rating)
        except (json.JSONDecodeError, ValueError):
            rating_value = raw_rating   # keep raw string if unparseable
    else:
        rating_value = raw_rating

    row.update({
        "name":              detail.get("name"),
        "task_id":           detail.get("task_id"),
        "source_created_at": detail.get("created_at"),
        "sphere_id":         detail.get("sphere_id"),
        "location_building": detail.get("location_building"),
        "difficulty":        detail.get("difficulty"),
        "organization_name": detail.get("organization_name"),
        "loyiha":            detail.get("loyiha"),
        "pudrat":            detail.get("pudrat"),
        "status_id":         status_block.get("id"),
        "status_name":       status_block.get("name"),
        "closed_at":         detail.get("closed_at"),
        "region_soato":      detail.get("region_soato"),
        "district_soato":    detail.get("district_soato"),
        "deadline":          detail.get("deadline"),
        "rating":            json.dumps(rating_value, ensure_ascii=False) if rating_value is not None else None,
        "number_protocol":   detail.get("number_protocol"),
        "reestr_number":     detail.get("reestr_number"),
        "block_count":       detail.get("block_count") or 0,
        "apartment_count":   detail.get("apartment_count") or 0,
        "blocks":            json.dumps(detail.get("blocks") or [], ensure_ascii=False),
        "conclusion_url":    (detail.get("conclusion") or {}).get("url"),
        # Overwrite with detail lat/lon (more precise)
        "lat":               detail.get("lat") or obj_summary.get("lat"),
        "lon":               detail.get("long") or obj_summary.get("long"),
    })
    return row


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    log.info("Saved JSON → %s", path)


def save_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    if not rows:
        log.warning("No rows to write to CSV.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Saved CSV  → %s  (%d rows)", path, len(rows))


# ---------------------------------------------------------------------------
# SQL export helpers
# ---------------------------------------------------------------------------

# Column order must match the CREATE TABLE in the Laravel migration.
_SQL_COLUMNS = (
    "object_id", "object_status",
    "name", "task_id", "sphere_id", "location_building", "difficulty",
    "organization_name", "loyiha", "pudrat",
    "status_id", "status_name",
    "lat", "lon",
    "region_soato", "district_soato",
    "deadline", "closed_at", "source_created_at",
    "number_protocol", "reestr_number", "rating",
    "block_count", "apartment_count", "blocks", "conclusion_url",
    "fetched_at",
)


def _escape_str(value: str) -> str:
    """Escape a string for safe inclusion in a MySQL single-quoted literal."""
    return (
        value
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\x1a", "\\Z")
    )


def _sql_literal(value: Any) -> str:
    """Convert a Python value to a MySQL literal string."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # str (includes pre-serialised JSON strings)
    return "'" + _escape_str(str(value)) + "'"


_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS `{table}` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `object_id`          INT UNSIGNED NOT NULL,
  `object_status`      TINYINT UNSIGNED DEFAULT NULL,
  `name`               TEXT COLLATE utf8mb4_unicode_ci,
  `task_id`            BIGINT UNSIGNED DEFAULT NULL,
  `sphere_id`          SMALLINT UNSIGNED DEFAULT NULL,
  `location_building`  TEXT COLLATE utf8mb4_unicode_ci,
  `difficulty`         CHAR(3) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `organization_name`  TEXT COLLATE utf8mb4_unicode_ci,
  `loyiha`             TEXT COLLATE utf8mb4_unicode_ci COMMENT 'Design organisation',
  `pudrat`             TEXT COLLATE utf8mb4_unicode_ci COMMENT 'Contractor',
  `status_id`          TINYINT UNSIGNED DEFAULT NULL,
  `status_name`        VARCHAR(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `lat`                DECIMAL(15,10) DEFAULT NULL,
  `lon`                DECIMAL(15,10) DEFAULT NULL COMMENT '`long` is reserved in MySQL',
  `region_soato`       INT UNSIGNED DEFAULT NULL,
  `district_soato`     INT UNSIGNED DEFAULT NULL,
  `deadline`           DATE DEFAULT NULL,
  `closed_at`          DATETIME DEFAULT NULL,
  `source_created_at`  DATETIME DEFAULT NULL,
  `number_protocol`    TEXT COLLATE utf8mb4_unicode_ci,
  `reestr_number`      VARCHAR(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rating`             JSON DEFAULT NULL,
  `block_count`        SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `apartment_count`    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `blocks`             JSON DEFAULT NULL,
  `conclusion_url`     TEXT COLLATE utf8mb4_unicode_ci,
  `fetched_at`         DATETIME DEFAULT NULL,
  `created_at`         TIMESTAMP NULL DEFAULT NULL,
  `updated_at`         TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_object_id` (`object_id`),
  KEY `idx_status_id` (`status_id`),
  KEY `idx_sphere_id` (`sphere_id`),
  KEY `idx_region` (`region_soato`),
  KEY `idx_district` (`district_soato`),
  KEY `idx_lat_lon` (`lat`, `lon`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def save_sql(rows: List[Dict[str, Any]], path: Path, table: str = DEFAULT_SQL_TABLE) -> None:
    """
    Write a MySQL-importable .sql file containing:
      - SET / charset headers
      - CREATE TABLE IF NOT EXISTS
      - Batched INSERT … ON DUPLICATE KEY UPDATE statements
    """
    if not rows:
        log.warning("No rows to write to SQL.")
        return

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)

    col_list = ", ".join(f"`{c}`" for c in _SQL_COLUMNS)
    # Columns to update on duplicate object_id (everything except PK and object_id)
    update_clause = ", ".join(
        f"`{c}` = VALUES(`{c}`)"
        for c in _SQL_COLUMNS
        if c != "object_id"
    )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"-- Shaffof Qurilish data export\n")
        fh.write(f"-- Generated: {now_utc} UTC\n")
        fh.write(f"-- Rows: {len(rows)}\n\n")
        fh.write("SET NAMES utf8mb4;\n")
        fh.write("SET time_zone = '+00:00';\n")
        fh.write("SET foreign_key_checks = 0;\n")
        fh.write("SET sql_mode = 'NO_AUTO_VALUE_ON_ZERO';\n\n")
        fh.write(_CREATE_TABLE_DDL.format(table=table))
        fh.write("\n")

        # Write in batches for efficient import
        for batch_start in range(0, len(rows), SQL_INSERT_BATCH):
            batch = rows[batch_start : batch_start + SQL_INSERT_BATCH]
            fh.write(
                f"INSERT INTO `{table}` ({col_list})\nVALUES\n"
            )
            value_rows = []
            for row in batch:
                # Inject fetched_at timestamp
                row_copy = dict(row)
                row_copy.setdefault("fetched_at", now_utc)
                vals = ", ".join(_sql_literal(row_copy.get(c)) for c in _SQL_COLUMNS)
                value_rows.append(f"  ({vals})")
            fh.write(",\n".join(value_rows))
            fh.write(f"\nON DUPLICATE KEY UPDATE\n  {update_clause};\n\n")

        fh.write("SET foreign_key_checks = 1;\n")

    log.info("Saved SQL  → %s  (%d rows, batch size %d)", path, len(rows), SQL_INSERT_BATCH)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse construction objects from dshk.shaffofqurilish.uz",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--country-id", type=int, default=0,
        help="Country filter for list-construction (0 = all countries).",
    )
    parser.add_argument(
        "--sphere-id", type=int, default=0,
        help="Sphere/sector filter for list-construction (0 = all).",
    )
    parser.add_argument(
        "--status", type=int, default=2, choices=[0, 1, 2, 3, 5],
        help=(
            "Status filter: 0=all, 1=jarayonda (in-progress), "
            "2=all objects (website default, returns full 26k+ dataset), "
            "3=toxtatilgan (stopped), 5=topshirilgan (delivered)."
        ),
    )
    parser.add_argument(
        "--with-details", action="store_true",
        help="Fetch full detail for every object via get-gasn-info.",
    )
    parser.add_argument(
        "--object-ids", nargs="+", type=int, metavar="ID",
        help="Fetch details only for these specific object IDs (skips list fetch).",
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help="Number of parallel workers for detail fetching.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.0,
        help="Per-request delay (seconds) between detail fetches to avoid rate-limiting.",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("output"),
        help="Directory to write output files.",
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="Skip CSV output (only write JSON).",
    )
    parser.add_argument(
        "--no-json", action="store_true",
        help="Skip JSON output (only write CSV).",
    )
    parser.add_argument(
        "--export-sql", action="store_true",
        help="Generate a MySQL-importable .sql file (implies --with-details).",
    )
    parser.add_argument(
        "--sql-table", default=DEFAULT_SQL_TABLE, metavar="TABLE",
        help="Table name to use in the generated SQL file.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    session = build_session()

    # ---- Step 1: get the object list ----------------------------------------
    if args.object_ids:
        # User provided explicit IDs — build minimal summary dicts
        object_list = [
            {"object_id": oid, "object_status": None, "lat": None, "long": None}
            for oid in args.object_ids
        ]
        list_response = None
        log.info("Using %d user-supplied object IDs.", len(object_list))
    else:
        list_response = fetch_object_list(
            session,
            country_id=args.country_id,
            sphere_id=args.sphere_id,
            status=args.status,
            timeout=args.timeout,
        )
        object_list = list_response.get("data", [])

    if not object_list:
        log.warning("No objects returned. Exiting.")
        sys.exit(0)

    # Save the raw list response
    if list_response and not args.no_json:
        save_json(list_response, args.out_dir / "list_construction.json")

    # ---- Step 2: fetch details (optional) -----------------------------------
    details: Dict[int, Optional[Dict[str, Any]]] = {}
    if args.with_details or args.object_ids or args.export_sql:
        object_ids = [obj["object_id"] for obj in object_list]
        details = fetch_all_details(
            session,
            object_ids,
            workers=args.workers,
            timeout=args.timeout,
            delay=args.delay,
        )
        if not args.no_json:
            save_json(details, args.out_dir / "gasn_details.json")

    # ---- Step 3: build flat rows and save CSV --------------------------------
    rows = [
        flatten_detail(obj, details.get(obj["object_id"]))
        for obj in object_list
    ]

    if not args.no_json and rows:
        save_json(rows, args.out_dir / "combined.json")

    if not args.no_csv:
        save_csv(rows, args.out_dir / "construction_objects.csv")

    if args.export_sql:
        save_sql(rows, args.out_dir / "shafof_qurilish_data.sql", table=args.sql_table)

    log.info("Done. %d objects processed.", len(rows))


if __name__ == "__main__":
    main()
