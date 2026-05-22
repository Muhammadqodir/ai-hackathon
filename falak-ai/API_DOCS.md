# Satellite Analysis API — Documentation

## Overview

This API accepts geographic coordinates and a radius, downloads high-resolution satellite map tiles for that area, runs YOLOv11 segmentation on each tile, and streams real-time detection progress back to the client via **Server-Sent Events (SSE)**.

### Key capabilities

| Feature | Details |
|---|---|
| Real-time progress | Server-Sent Events stream – no WebSocket server required |
| Tile caching | Tiles cached on disk; repeated requests skip downloads |
| Detection caching | Per-tile YOLO results cached; re-analysis reuses them instantly |
| Request deduplication | Identical requests (same lat/lon/radius/zoom/provider) return the same job |
| Auto-retry on failure | Re-POST the same parameters to restart a failed job |

---

## Base URL

```
http://localhost:8000/api
```

For production replace with your actual domain.

---

## Endpoints

### 1. `POST /api/analysis` — Submit analysis

Create a new satellite analysis job (or return an existing one if the same parameters were used before).

#### Request

```http
POST /api/analysis
Content-Type: application/json
```

| Field | Type | Required | Default | Constraints | Description |
|---|---|---|---|---|---|
| `lat` | float | ✅ | — | −90 to 90 | Centre latitude |
| `lon` | float | ✅ | — | −180 to 180 | Centre longitude |
| `radius` | integer | ✅ | — | 100 – 50 000 | Radius in **metres** |
| `zoom` | integer | — | `17` | 15 – 20 | Map tile zoom level |
| `provider` | string | — | `"google"` | `esri` \| `google` \| `all` | Tile provider |

**Zoom guidance:**

| Zoom | Resolution | Tiles in a 1 km radius |
|---|---|---|
| 15 | ~4.8 m/px | ~4 |
| 16 | ~2.4 m/px | ~16 |
| **17** | **~1.2 m/px** | **~64** |
| 18 | ~0.6 m/px | ~256 |
| 19 | ~0.3 m/px | ~500 (max) |
| 20 | ~0.15 m/px | >500 (rejected) |

#### Example

```bash
curl -X POST http://localhost:8000/api/analysis \
  -H "Content-Type: application/json" \
  -d '{
    "lat":    40.3839,
    "lon":    71.7864,
    "radius": 1000,
    "zoom":   17,
    "provider": "google"
  }'
```

#### Response `201 Created` (new job) / `200 OK` (existing job)

```json
{
  "id":              42,
  "status":          "pending",
  "lat":             40.3839,
  "lon":             71.7864,
  "radius":          1000,
  "zoom":            17,
  "provider":        "google",
  "total_tiles":     null,
  "processed_tiles": 0,
  "bbox":            null,
  "error_message":   null,
  "started_at":      null,
  "completed_at":    null,
  "stream_url":      "http://localhost:8000/api/analysis/42/stream",
  "results_url":     "http://localhost:8000/api/analysis/42",
  "tiles_url":       "http://localhost:8000/api/analysis/42/tiles"
}
```

**`status` lifecycle:** `pending` → `processing` → `completed` | `failed`

---

### 2. `GET /api/analysis/{id}/stream` — Real-time SSE stream

Connect to this endpoint immediately after receiving `stream_url` from the POST response. The server sends events as the job progresses.

#### Headers

```http
GET /api/analysis/42/stream
Accept: text/event-stream
Cache-Control: no-cache
```

The browser `EventSource` API handles these automatically.

#### SSE protocol

Each message follows the [SSE spec](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events):

```
id: <monotonic-event-id>
event: <event-name>
data: <JSON-payload>

```

#### Event types

---

##### `start` — Job initialised

Fired once when the Python worker begins and the tile grid is computed.

```json
{
  "type":        "start",
  "total_tiles": 64,
  "zoom":        17,
  "bbox": {
    "south": 40.37484,
    "north": 40.39296,
    "west":  71.76778,
    "east":  71.80502
  }
}
```

---

##### `status` — Human-readable progress message

```json
{
  "type":    "status",
  "message": "Loading YOLO model…"
}
```

---

##### `tile_processed` — One tile completed

Fired for every tile (in the order they finish downloading + inference). Includes full detections for the tile.

```json
{
  "type":            "tile_processed",
  "progress":        12,
  "total":           64,
  "tile_x":          41803,
  "tile_y":          23490,
  "tile_z":          17,
  "detection_count": 3,
  "from_cache":      false,
  "bbox": {
    "north": 40.38756,
    "west":  71.77124,
    "south": 40.38483,
    "east":  71.77673
  },
  "detections": [
    {
      "class_id":   0,
      "class_name": "construction",
      "confidence": 0.8712,
      "bbox": { "x1": 45.2, "y1": 102.1, "x2": 198.7, "y2": 241.3 },
      "polygon": [[45, 102], [198, 102], [198, 241], [45, 241]]
    }
  ]
}
```

> **Note:** `bbox` coordinates inside `detections` are pixel-relative to the 256 × 256 tile image. Use the tile's geographic `bbox` to convert to WGS-84.

---

##### `complete` — All tiles processed

```json
{
  "type":             "complete",
  "total_tiles":      64,
  "processed":        64,
  "total_detections": 187
}
```

---

##### `error` — Fatal failure

```json
{
  "type":    "error",
  "message": "YOLO model not found: /path/to/best.pt"
}
```

---

##### `timeout` — SSE connection timeout

Emitted after 5 minutes of inactivity. Reconnect or poll `GET /api/analysis/{id}`.

---

#### JavaScript client example

```javascript
const res = await fetch('/api/analysis', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ lat: 40.3839, lon: 71.7864, radius: 1000 }),
});
const { id, stream_url } = await res.json();

const es = new EventSource(stream_url);

es.addEventListener('start', e => {
  const data = JSON.parse(e.data);
  console.log(`Starting: ${data.total_tiles} tiles`);
});

es.addEventListener('tile_processed', e => {
  const data = JSON.parse(e.data);
  console.log(`${data.progress}/${data.total} — ${data.detection_count} detections`);
  // data.detections[] contains full YOLO results for this tile
});

es.addEventListener('complete', e => {
  const data = JSON.parse(e.data);
  console.log('Done! Total detections:', data.total_detections);
  es.close();
});

es.addEventListener('error', e => {
  const data = JSON.parse(e.data);
  console.error('Failed:', data.message);
  es.close();
});
```

#### Reconnection (built-in SSE feature)

If the connection drops, the browser automatically reconnects using the last received `id` as the `Last-Event-ID` header. The server replays all missed events from that point.

---

### 3. `GET /api/analysis/{id}` — Get analysis summary

Returns the current state and aggregate statistics.

#### Response

```json
{
  "id":                42,
  "status":            "completed",
  "lat":               40.3839,
  "lon":               71.7864,
  "radius":            1000,
  "zoom":              17,
  "provider":          "google",
  "total_tiles":       64,
  "processed_tiles":   64,
  "bbox": {
    "south": 40.37484, "north": 40.39296,
    "west":  71.76778, "east":  71.80502
  },
  "error_message":     null,
  "started_at":        "2026-05-22T10:15:30.000000Z",
  "completed_at":      "2026-05-22T10:17:45.000000Z",
  "total_detections":  187,
  "stream_url":        "http://localhost:8000/api/analysis/42/stream",
  "results_url":       "http://localhost:8000/api/analysis/42",
  "tiles_url":         "http://localhost:8000/api/analysis/42/tiles"
}
```

---

### 4. `GET /api/analysis/{id}/tiles` — Paginated tile results

Returns all tiles with their YOLO detection results. Useful for post-processing or rendering a GeoJSON layer.

#### Query parameters

| Param | Type | Default | Max | Description |
|---|---|---|---|---|
| `per_page` | int | `50` | `200` | Results per page |
| `page` | int | `1` | — | Page number |

#### Example

```bash
curl "http://localhost:8000/api/analysis/42/tiles?per_page=10&page=1"
```

#### Response

```json
{
  "current_page": 1,
  "data": [
    {
      "id":              1,
      "tile_x":          41803,
      "tile_y":          23490,
      "tile_z":          17,
      "provider":        "google",
      "detection_count": 3,
      "from_cache":      false,
      "bbox": {
        "north": 40.38756, "west": 71.77124,
        "south": 40.38483, "east": 71.77673
      },
      "detections": [
        {
          "class_id":   0,
          "class_name": "construction",
          "confidence": 0.8712,
          "bbox": { "x1": 45.2, "y1": 102.1, "x2": 198.7, "y2": 241.3 },
          "polygon": [[45, 102], [198, 102], [198, 241], [45, 241]]
        }
      ],
      "created_at": "2026-05-22T10:15:45.000000Z"
    }
  ],
  "from":         1,
  "last_page":    2,
  "per_page":     10,
  "to":           10,
  "total":        64
}
```

---

### 5. `GET /api/detected-objects` — All detected objects

Returns every detected object across all analyses in a single flat list, ordered by detection time descending. No pagination.

Each row represents one segmented object: its geographic polygon (converted from pixel space to WGS-84 during job processing), the centroid of that polygon, the YOLO class, and the timestamp it was detected.

#### Example

```bash
curl http://localhost:8000/api/detected-objects
```

#### Response `200 OK`

```json
{
  "total": 2,
  "data": [
    {
      "id":               17,
      "analysis_id":      42,
      "analysis_tile_id": 8,
      "detected_at":      "2026-05-22T10:16:03.000000Z",
      "center_lat":       40.38620,
      "center_lon":       71.77401,
      "polygon_points":   [
        [40.38634, 71.77371],
        [40.38634, 71.77431],
        [40.38606, 71.77431],
        [40.38606, 71.77371]
      ],
      "class_id":         0,
      "class_name":       "construction",
      "confidence":       0.8712
    }
  ]
}
```

#### Response fields

| Field | Type | Description |
|---|---|---|
| `id` | integer | Primary key |
| `analysis_id` | integer | Parent analysis job |
| `analysis_tile_id` | integer | Tile the object was found in |
| `detected_at` | ISO 8601 | When the tile was processed |
| `center_lat` | float | Centroid latitude (WGS-84) |
| `center_lon` | float | Centroid longitude (WGS-84) |
| `polygon_points` | `[[lat,lon], …]` | Polygon vertices in WGS-84 |
| `class_id` | integer | YOLO class index |
| `class_name` | string | YOLO class label |
| `confidence` | float \| null | Detection confidence (0–1) |

> **Note:** Only detections that include a segmentation polygon are stored. Bounding-box-only detections (no mask) are skipped.

---

## Pixel → Geographic coordinate conversion

Each detection's `bbox` is in **tile-pixel coordinates** (0–255). To convert to WGS-84:

```javascript
function pixelToGeo(px, py, tileBbox) {
  const fracX = px / 256;
  const fracY = py / 256;
  return {
    lon: tileBbox.west  + fracX * (tileBbox.east  - tileBbox.west),
    lat: tileBbox.north - fracY * (tileBbox.north - tileBbox.south),
  };
}

// Example: top-left corner of a detection
const geo = pixelToGeo(45.2, 102.1, tile.bbox);
```

---

## Error responses

All errors follow a consistent JSON shape:

```json
{
  "message": "The lat field must be a number.",
  "errors": {
    "lat": ["The lat field must be a number."]
  }
}
```

| HTTP code | Meaning |
|---|---|
| `422` | Validation failed (bad parameters) |
| `404` | Analysis not found |
| `500` | Server error |

---

## Setup & running

### 1. Install PHP dependencies

```bash
cd falak-ai
composer install
```

### 2. Configure environment

Copy `.env.example` → `.env` and set:

```ini
PYTHON_BIN=/path/to/label_env/bin/python
YOLO_MODEL_PATH=/path/to/ai-model/best.pt
TILE_CACHE_DIR=/path/to/falak-ai/storage/app/tile_cache
```

The bundled virtual environment is pre-configured:

```ini
PYTHON_BIN=/Users/mqodir/Documents/GitHub/ai-hackathon/label_env/bin/python
```

### 3. Run migrations

```bash
php artisan migrate
```

### 4. Start all services (development)

```bash
composer run dev
```

This starts concurrently:
- **PHP server** (`php artisan serve` → `localhost:8000`)
- **Queue worker** (`php artisan queue:listen`) — processes analysis jobs
- **Vite** (frontend asset watcher)
- **Log tail** (`php artisan pail`)

> ⚠️ The queue worker is **required**. Without it, analysis jobs will queue but never run.

### 5. Test with curl

```bash
# Submit job
curl -s -X POST http://localhost:8000/api/analysis \
  -H "Content-Type: application/json" \
  -d '{"lat":40.3839,"lon":71.7864,"radius":500}' | python3 -m json.tool

# Stream progress (ctrl+C to stop)
curl -N http://localhost:8000/api/analysis/1/stream
```

---

## Caching behaviour

### Tile image cache

Location: `TILE_CACHE_DIR/tiles/{provider}/{z}/{x}/{y}.jpg`

Tiles are never re-downloaded if the cache file exists and is > 500 bytes. The cache is shared across all analyses.

### Detection result cache

Location: `TILE_CACHE_DIR/detections/{provider}_{z}_{x}_{y}.json`

Per-tile inference results are cached permanently. If you re-run an analysis (e.g., retry after a failed job) over the same tiles, YOLO inference is skipped entirely.

### Analysis deduplication

A SHA-256 hash of `lat:lon:radius:zoom:provider` is computed for every request. If an analysis with that hash already exists:

| Existing status | Behaviour |
|---|---|
| `pending` or `processing` | Returns existing job (connect to its `stream_url`) |
| `completed` | Returns cached results immediately (no reprocessing) |
| `failed` | Resets and re-runs the job |

---

## Production deployment checklist

- [ ] Switch `DB_CONNECTION` from `sqlite` to `mysql` or `pgsql` (SQLite has limited concurrent write support under load)
- [ ] Set `QUEUE_CONNECTION=redis` and run Redis for reliable job queuing
- [ ] Run multiple queue workers: `php artisan queue:work --queue=default --tries=1`
- [ ] Use [Supervisor](http://supervisord.org/) to manage queue workers
- [ ] Configure Nginx with `proxy_buffering off` and `proxy_read_timeout 310s` for SSE endpoints
- [ ] Set `APP_ENV=production` and `APP_DEBUG=false`
- [ ] Add rate limiting to `POST /api/analysis` (prevent abuse)
- [ ] Consider a tile proxy / CDN cache for high-volume deployments

### Nginx SSE configuration

```nginx
location /api/analysis {
    proxy_pass         http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header   Connection "";
    proxy_buffering    off;
    proxy_cache        off;
    proxy_read_timeout 310s;   # slightly longer than the 5-min SSE timeout
    chunked_transfer_encoding on;
}
```

### Supervisor queue worker

```ini
[program:falak-queue]
command=php /var/www/falak-ai/artisan queue:work database --tries=1 --timeout=3600
directory=/var/www/falak-ai
autostart=true
autorestart=true
numprocs=4
redirect_stderr=true
stdout_logfile=/var/log/falak-queue.log
```

---

## Detection schema reference

Each detection object in the `detections` array:

```typescript
interface Detection {
  class_id:   number;        // YOLO class index
  class_name: string;        // e.g. "construction"
  confidence: number;        // 0.0 – 1.0
  bbox: {
    x1: number;              // pixel coordinates within the 256×256 tile
    y1: number;
    x2: number;
    y2: number;
  };
  polygon?: number[][];      // [[x,y], ...] simplified contour (optional)
}
```

---

## Python script reference

`ai-model/tile_processor.py` is a standalone command-line tool:

```
usage: tile_processor.py [-h] --lat LAT --lon LON --radius RADIUS
                          [--zoom ZOOM] --model MODEL --cache-dir CACHE_DIR
                          [--workers WORKERS] [--provider {esri,google,all}]
                          [--conf CONF] [--iou IOU] [--imgsz IMGSZ]

Options:
  --lat         Centre latitude
  --lon         Centre longitude
  --radius      Radius in metres
  --zoom        Tile zoom level (default: 17)
  --model       Path to YOLOv11 .pt model file
  --cache-dir   Directory for tile and detection caches
  --workers     Parallel download threads (default: 8)
  --provider    Tile provider: esri, google, or all (default: google)
  --conf        YOLO confidence threshold (default: 0.25)
  --iou         YOLO NMS IoU threshold (default: 0.45)
  --imgsz       Inference image size (default: 640)
```

The script can be run standalone for debugging:

```bash
cd /Users/mqodir/Documents/GitHub/ai-hackathon
source label_env/bin/activate

python ai-model/tile_processor.py \
  --lat    40.3839 \
  --lon    71.7864 \
  --radius 500 \
  --zoom   17 \
  --model  ai-model/best.pt \
  --cache-dir /tmp/tile_cache
```

---

---

# Shaffof Qurilish Data API

Data sourced from [dshk.shaffofqurilish.uz](https://dshk.shaffofqurilish.uz/) — the Uzbekistan national construction transparency registry. 4 100+ construction objects are pre-loaded via the Python parser + Laravel seeder.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/shafof-qurilish` | Paginated list with filters |
| `GET` | `/api/shafof-qurilish/stats` | Aggregate statistics |
| `GET` | `/api/shafof-qurilish/{object_id}` | Full detail for one object |

---

### 1. `GET /api/shafof-qurilish` — List construction objects

Returns a paginated, filterable list of construction objects. List rows include key fields only; use the detail endpoint for the full record.

#### Query parameters

| Parameter | Type | Description |
|---|---|---|
| `search` | string | Full-text search across `name`, `organization_name`, `location_building` |
| `region_soato` | integer | Filter by region SOATO code (e.g. `1703` = Andijan region) |
| `district_soato` | integer | Filter by district SOATO code |
| `status_id` | integer | `1` in-progress · `2` frozen · `3` stopped · `5` delivered |
| `sphere_id` | integer | Construction sector ID |
| `difficulty` | string | Complexity class: `I` · `II` · `III` · `IV` |
| `per_page` | integer | Rows per page (1–200, default `50`) |
| `page` | integer | Page number (default `1`) |

#### Example

```bash
# All in-progress objects in Andijan region
curl "http://localhost:8000/api/shafof-qurilish?region_soato=1703&status_id=1&per_page=20"

# Full-text search
curl "http://localhost:8000/api/shafof-qurilish?search=Milano+qurilish"
```

#### Response `200 OK`

```json
{
  "current_page": 1,
  "data": [
    {
      "object_id":         70166,
      "object_status":     2,
      "name":              "Андижон вилояти Асака тумани \"Байналминал\" МФЙдаги 40-ДМТТни мукаммал таъмирлаш",
      "sphere_id":         15,
      "organization_name": "\"VAZIRLAR MAHKAMASI HUZURIDAGI ENERGIYA SAMARADORLIGI MILLIY AGENTLIGI\" DAVLAT MUASSASASI",
      "status_id":         2,
      "status_name":       "Jarayonda",
      "difficulty":        "II",
      "lat":               40.6529181190822,
      "lon":               72.252675890923,
      "region_soato":      1703,
      "district_soato":    1703224,
      "deadline":          "2027-01-15",
      "block_count":       1,
      "apartment_count":   0,
      "reestr_number":     "273935",
      "fetched_at":        "2026-05-22T00:23:14.000000Z"
    }
  ],
  "first_page_url": "http://localhost:8000/api/shafof-qurilish?page=1",
  "from":           1,
  "last_page":      83,
  "last_page_url":  "http://localhost:8000/api/shafof-qurilish?page=83",
  "next_page_url":  "http://localhost:8000/api/shafof-qurilish?page=2",
  "path":           "http://localhost:8000/api/shafof-qurilish",
  "per_page":       50,
  "prev_page_url":  null,
  "to":             50,
  "total":          4112
}
```

#### List row fields

| Field | Type | Description |
|---|---|---|
| `object_id` | integer | Unique object ID from the source registry |
| `object_status` | integer | Raw numeric status from the list endpoint |
| `name` | string | Full project name (Uzbek/Russian/Cyrillic) |
| `sphere_id` | integer | Construction sector ID |
| `organization_name` | string | Investor / developer legal name |
| `status_id` | integer | `1` in-progress · `2` frozen · `3` stopped · `5` delivered |
| `status_name` | string | Human-readable status label |
| `difficulty` | string | Complexity class: `I`–`IV` |
| `lat` / `lon` | float | WGS-84 coordinates |
| `region_soato` | integer | SOATO region code |
| `district_soato` | integer | SOATO district code |
| `deadline` | date \| null | Planned completion date (`Y-m-d`) |
| `block_count` | integer | Number of building blocks |
| `apartment_count` | integer | Total apartments across all blocks |
| `reestr_number` | string \| null | Registry / protocol reference number |
| `fetched_at` | ISO 8601 | When this record was scraped |

---

### 2. `GET /api/shafof-qurilish/stats` — Aggregate statistics

Returns summary counts and totals across the entire dataset. No parameters.

#### Example

```bash
curl http://localhost:8000/api/shafof-qurilish/stats
```

#### Response `200 OK`

```json
{
  "totals": {
    "total_objects":   4112,
    "total_blocks":    4596,
    "total_apartments": 44694,
    "total_regions":   14,
    "total_districts": 183,
    "total_spheres":   62
  },
  "by_status": [
    { "status_id": 1, "status_name": "Jarayonda",   "count": 1137 },
    { "status_id": 2, "status_name": "Muzlatilgan", "count": 38   },
    { "status_id": 3, "status_name": "Toxtatilgan", "count": 50   },
    { "status_id": 5, "status_name": "Topshirilgan","count": 2855 }
  ],
  "by_difficulty": [
    { "difficulty": "I",   "count": 423  },
    { "difficulty": "II",  "count": 1204 },
    { "difficulty": "III", "count": 1897 },
    { "difficulty": "IV",  "count": 588  }
  ],
  "by_region": [
    { "region_soato": 1726, "count": 812 },
    { "region_soato": 1703, "count": 734 }
  ]
}
```

#### `totals` fields

| Field | Description |
|---|---|
| `total_objects` | Total construction objects in the database |
| `total_blocks` | Sum of all building blocks across all objects |
| `total_apartments` | Sum of all apartments across all objects |
| `total_regions` | Number of distinct regions (SOATO codes) represented |
| `total_districts` | Number of distinct districts represented |
| `total_spheres` | Number of distinct construction sectors represented |

---

### 3. `GET /api/shafof-qurilish/{object_id}` — Object detail

Returns the complete record for a single construction object, including nested blocks and rating data.

#### Path parameter

| Parameter | Type | Description |
|---|---|---|
| `object_id` | integer | The `object_id` from the source registry (not the database `id`) |

#### Example

```bash
curl http://localhost:8000/api/shafof-qurilish/70166
```

#### Response `200 OK`

```json
{
  "data": {
    "id":                  3,
    "object_id":           70166,
    "object_status":       2,
    "name":                "Андижон вилояти Асака тумани \"Байналминал\" МФЙдаги 40-ДМТТни мукаммал таъмирлаш ID:2501032240103002",
    "task_id":             285848602,
    "sphere_id":           15,
    "location_building":   "Baynal Minal MFY, Eski Andijon ko'chasi, 179-uy",
    "difficulty":          "II",
    "organization_name":   "\"VAZIRLAR MAHKAMASI HUZURIDAGI ENERGIYA SAMARADORLIGI MILLIY AGENTLIGI\" DAVLAT MUASSASASI",
    "loyiha":              "INTEGRAL BIRLASHGAN LOYIHA",
    "pudrat":              "Milano qurilish",
    "status_id":           2,
    "status_name":         "Jarayonda",
    "lat":                 40.6529181190822,
    "lon":                 72.252675890923,
    "region_soato":        1703,
    "district_soato":      1703224,
    "deadline":            "2027-01-15",
    "closed_at":           null,
    "source_created_at":   "2026-04-20T05:35:22.000000Z",
    "number_protocol":     "0",
    "reestr_number":       "273935",
    "rating": [
      {
        "loyiha":    { "inn": "306858382", "name": "\"INTEGRAL BIRLASHGAN LOYIHA\" MCHJ", "reyting_loyha": "CC" },
        "qurilish":  { "inn": "205927782", "name": "\"MILANO QURILISH\" MCHJ", "reyting_umumiy": "CCC" }
      }
    ],
    "block_count":         1,
    "apartment_count":     0,
    "blocks": [
      {
        "id":              103352,
        "name":            "А",
        "apartment_count": null,
        "accepted":        false,
        "area":            null,
        "floor":           "2"
      }
    ],
    "conclusion_url":      "https://api-ekspertiza.mc.uz/appeal-final-conclusion-pdf/110873",
    "fetched_at":          "2026-05-22T00:23:14.000000Z",
    "created_at":          "2026-05-22T00:23:14.000000Z",
    "updated_at":          "2026-05-22T00:23:14.000000Z"
  }
}
```

#### Full record fields

| Field | Type | Description |
|---|---|---|
| `object_id` | integer | Source registry ID |
| `object_status` | integer | Raw status from list endpoint |
| `name` | string | Full project name |
| `task_id` | integer | Source task/application ID |
| `sphere_id` | integer | Construction sector |
| `location_building` | string | Building address |
| `difficulty` | string | `I` · `II` · `III` · `IV` — structural complexity class |
| `organization_name` | string | Investor / developer |
| `loyiha` | string | Design organisation |
| `pudrat` | string | General contractor |
| `status_id` / `status_name` | integer / string | Construction status |
| `lat` / `lon` | float | WGS-84 coordinates |
| `region_soato` | integer | SOATO region code |
| `district_soato` | integer | SOATO district code |
| `deadline` | date \| null | Planned completion (`Y-m-d`) |
| `closed_at` | datetime \| null | Actual close date |
| `source_created_at` | datetime | When the object was registered in the source system |
| `number_protocol` | string \| null | Architectural council protocol text |
| `reestr_number` | string \| null | State registry reference |
| `rating` | array \| null | Contractor/designer ratings from the source (`reyting_loyha`, `reyting_umumiy`) |
| `block_count` | integer | Number of building blocks |
| `apartment_count` | integer | Total apartments |
| `blocks` | array | Per-block detail: `id`, `name`, `apartment_count`, `accepted`, `area`, `floor` |
| `conclusion_url` | string \| null | URL to the official ekspertiza (expert conclusion) PDF |
| `fetched_at` | datetime | When our scraper last fetched this record |

#### `404 Not Found`

```json
{ "message": "No query results for model [App\\Models\\ShafofQurilishData]." }
```

---

### Status codes reference

| `status_id` | `status_name` | Meaning |
|---|---|---|
| `1` | Jarayonda | Under construction (in-progress) |
| `2` | Muzlatilgan | Frozen / suspended |
| `3` | Toxtatilgan | Stopped |
| `5` | Topshirilgan | Delivered / completed |

### Difficulty class reference

| `difficulty` | Description |
|---|---|
| `I` | Simple — 1–2 storey residential/utility |
| `II` | Medium complexity |
| `III` | Complex — multi-storey residential or commercial |
| `IV` | Highly complex — industrial, infrastructure |

---

### Refreshing the data

The dataset is a point-in-time snapshot. To re-scrape and update:

```bash
# 1. Fetch all objects with details from the source API
cd ai-model-src
python shafofqurilish.py --with-details --workers 10 --out-dir ./output

# 2. Copy the JSON into the Laravel data folder
cp output/combined.json ../falak-ai/database/data/shafof_qurilish_data.json

# 3. Re-seed (upserts — safe to run repeatedly)
cd ../falak-ai
php artisan db:seed --class=ShafofQurilishSeeder
```
