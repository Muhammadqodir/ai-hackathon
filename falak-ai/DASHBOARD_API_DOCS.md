# Dashboard API — Documentation

## Overview

The Dashboard endpoint aggregates data from multiple tables (`detected_objects`, `analyses`, `shafof_qurilish_data`) into a single response covering:

- **Area statistics** — total scanned area, day-over-day change, and a 7-day chart
- **Detection statistics** — total object count, day-over-day change, per-class breakdown, and cross-match count with official Shafof Qurilish records
- **Object list** — paginated detected objects enriched with regional metadata and the nearest Shafof Qurilish record (if any exists within 100 metres)

---

## Base URL

```
http://localhost:8000/api
```

---

## Endpoint

### `GET /api/dashboard`

Returns a unified dashboard payload.

#### Query parameters

| Parameter  | Type    | Required | Default | Constraints | Description                      |
|------------|---------|----------|---------|-------------|----------------------------------|
| `per_page` | integer | —        | `50`    | 1 – 200     | Number of objects per page       |
| `page`     | integer | —        | `1`     | ≥ 1         | Page number for the objects list |

#### Example request

```bash
curl "http://localhost:8000/api/dashboard?per_page=25&page=1"
```

---

## Response structure

```
HTTP 200 OK
Content-Type: application/json
```

```json
{
  "area_stats": { ... },
  "detected_objects_stats": { ... },
  "objects": { ... }
}
```

---

### `area_stats` object

Statistics about the total geographic area that has been analysed.

| Field             | Type             | Description                                                                                    |
|-------------------|------------------|------------------------------------------------------------------------------------------------|
| `area_scanned`    | float            | Total cumulative scanned area in **km²** (sum of π·r² for every completed analysis)            |
| `change`          | string           | Percentage change in area scanned today vs. yesterday. Format: `"+12.5%"` or `"-3.0%"`        |
| `last_week_chart` | array of objects | Area scanned per calendar day for the past 7 days (including today). Missing days show `0`    |

**`last_week_chart` item**

| Field    | Type   | Description                              |
|----------|--------|------------------------------------------|
| `date`   | string | Calendar date in `DD.MM.YYYY` format     |
| `number` | float  | Area scanned on that day in **km²**      |

**Example**

```json
"area_stats": {
  "area_scanned": 47.1239,
  "change": "+12.5%",
  "last_week_chart": [
    { "date": "16.05.2026", "number": 3.1416 },
    { "date": "17.05.2026", "number": 0 },
    { "date": "18.05.2026", "number": 6.2832 },
    { "date": "19.05.2026", "number": 12.5664 },
    { "date": "20.05.2026", "number": 9.4248 },
    { "date": "21.05.2026", "number": 8.4823 },
    { "date": "22.05.2026", "number": 7.0686 }
  ]
}
```

---

### `detected_objects_stats` object

Aggregate statistics for all detected construction objects.

| Field                 | Type             | Description                                                                                       |
|-----------------------|------------------|---------------------------------------------------------------------------------------------------|
| `detected_objects`    | integer          | Total number of detected objects across all completed analyses                                    |
| `change`              | string           | Absolute change in detection count today vs. yesterday. Format: `"+123"` or `"-50"`              |
| `by_class`            | array of objects | Detection count grouped by YOLO class                                                             |
| `matched_with_shafof` | integer          | Number of detected objects that have a Shafof Qurilish record within **100 metres**              |

**`by_class` item**

| Field    | Type    | Description                          |
|----------|---------|--------------------------------------|
| `class`  | string  | YOLO class name (e.g. `construction`) |
| `number` | integer | Count of detections for that class   |

**Example**

```json
"detected_objects_stats": {
  "detected_objects": 4821,
  "change": "+123",
  "by_class": [
    { "class": "construction", "number": 3200 },
    { "class": "excavation",   "number": 1100 },
    { "class": "crane",        "number": 521  }
  ],
  "matched_with_shafof": 876
}
```

---

### `objects` object

Paginated list of detected objects, each enriched with regional columns from the parent analysis and the nearest Shafof Qurilish record.

**Pagination wrapper**

| Field          | Type    | Description                             |
|----------------|---------|-----------------------------------------|
| `data`         | array   | Array of object items (see below)       |
| `total`        | integer | Total number of detected objects        |
| `per_page`     | integer | Items per page (as requested)           |
| `current_page` | integer | Current page number                     |
| `last_page`    | integer | Total number of pages                   |

**Object item fields**

| Field               | Type           | Description                                                            |
|---------------------|----------------|------------------------------------------------------------------------|
| `id`                | integer        | Primary key of the detected object                                     |
| `analysis_id`       | integer        | ID of the parent analysis                                              |
| `analysis_tile_id`  | integer        | ID of the tile this object was detected on                             |
| `detected_at`       | string (ISO 8601) | Timestamp when the object was detected                              |
| `center_lat`        | float          | Latitude of the polygon centroid (WGS-84)                              |
| `center_lon`        | float          | Longitude of the polygon centroid (WGS-84)                             |
| `polygon_points`    | array          | Segmentation polygon as `[[lat, lon], ...]`                            |
| `class_id`          | integer        | YOLO numeric class ID                                                  |
| `class_name`        | string         | YOLO class label                                                       |
| `confidence`        | float \| null  | Model confidence score (0.0 – 1.0)                                    |
| `created_at`        | string (ISO 8601) | Record creation timestamp                                           |
| `updated_at`        | string (ISO 8601) | Record last-update timestamp                                        |
| `country`           | string \| null | Country name from the parent analysis (reverse-geocoded)               |
| `region`            | string \| null | Region / oblast from the parent analysis                               |
| `district`          | string \| null | District / city from the parent analysis                               |
| `is_on_shafof`      | boolean        | `true` if a Shafof Qurilish record exists within 100 m of this object  |
| `shafof_data`       | object \| null | Full Shafof Qurilish record for the nearest match, or `null`           |

**`shafof_data` fields** (present when `is_on_shafof` is `true`)

| Field               | Type           | Description                                        |
|---------------------|----------------|----------------------------------------------------|
| `id`                | integer        | Internal DB primary key                            |
| `object_id`         | integer        | Shafof Qurilish system object ID                   |
| `object_status`     | integer \| null | Raw status code from the list endpoint            |
| `name`              | string \| null | Official name of the construction object           |
| `task_id`           | integer \| null | Task ID in the source system                      |
| `sphere_id`         | integer \| null | Sector/sphere classification ID                   |
| `location_building` | string \| null | Address / location description                     |
| `difficulty`        | string \| null | Complexity class: `I`, `II`, `III`, or `IV`        |
| `organization_name` | string \| null | Investor / developer organisation                  |
| `loyiha`            | string \| null | Design organisation                                |
| `pudrat`            | string \| null | General contractor                                 |
| `status_id`         | integer \| null | Status ID (1=in-progress, 2=frozen, 3=stopped, 5=delivered) |
| `status_name`       | string \| null | Human-readable status label                        |
| `lat`               | float \| null  | Latitude of the construction site                  |
| `lon`               | float \| null  | Longitude of the construction site                 |
| `region_soato`      | integer \| null | Region SOATO code                                 |
| `district_soato`    | integer \| null | District SOATO code                               |
| `deadline`          | string \| null | Scheduled completion date (`YYYY-MM-DD`)           |
| `closed_at`         | string \| null | Actual completion timestamp                        |
| `source_created_at` | string \| null | Creation timestamp in the source system            |
| `number_protocol`   | string \| null | Protocol number                                    |
| `reestr_number`     | string \| null | Registry number                                    |
| `rating`            | array \| null  | Rating JSON from the source system                 |
| `block_count`       | integer        | Number of blocks                                   |
| `apartment_count`   | integer        | Number of apartments                               |
| `blocks`            | array \| null  | Block details array                                |
| `conclusion_url`    | string \| null | URL to the official ekspertiza PDF                 |
| `fetched_at`        | string \| null | Timestamp when this record was scraped             |

**Example response (abbreviated)**

```json
{
  "area_stats": {
    "area_scanned": 47.1239,
    "change": "+12.5%",
    "last_week_chart": [
      { "date": "16.05.2026", "number": 3.1416 },
      { "date": "17.05.2026", "number": 0 },
      { "date": "18.05.2026", "number": 6.2832 },
      { "date": "19.05.2026", "number": 12.5664 },
      { "date": "20.05.2026", "number": 9.4248 },
      { "date": "21.05.2026", "number": 8.4823 },
      { "date": "22.05.2026", "number": 7.0686 }
    ]
  },
  "detected_objects_stats": {
    "detected_objects": 4821,
    "change": "+123",
    "by_class": [
      { "class": "construction", "number": 3200 },
      { "class": "excavation",   "number": 1100 },
      { "class": "crane",        "number": 521  }
    ],
    "matched_with_shafof": 876
  },
  "objects": {
    "data": [
      {
        "id": 1,
        "analysis_id": 3,
        "analysis_tile_id": 17,
        "detected_at": "2026-05-22T08:14:00.000000Z",
        "center_lat": 40.3839,
        "center_lon": 71.7864,
        "polygon_points": [
          [40.3840, 71.7862],
          [40.3841, 71.7866],
          [40.3838, 71.7867],
          [40.3837, 71.7863]
        ],
        "class_id": 0,
        "class_name": "construction",
        "confidence": 0.8712,
        "created_at": "2026-05-22T08:14:01.000000Z",
        "updated_at": "2026-05-22T08:14:01.000000Z",
        "country": "Uzbekistan",
        "region": "Fergana Region",
        "district": "Fergana",
        "is_on_shafof": true,
        "shafof_data": {
          "id": 42,
          "object_id": 100523,
          "name": "16-qavatli turar-joy binosi",
          "status_id": 1,
          "status_name": "Qurilmoqda",
          "lat": 40.38391,
          "lon": 71.78638,
          "region_soato": 1703,
          "district_soato": 170306,
          "organization_name": "Namuna Qurilish LLC",
          "difficulty": "II",
          "deadline": "2027-12-31",
          "block_count": 1,
          "apartment_count": 80,
          "blocks": [
            {
              "id": 1,
              "name": "1-blok",
              "apartment_count": 80,
              "accepted": 0,
              "area": 4200.5,
              "floor": 16
            }
          ],
          "rating": null,
          "conclusion_url": null,
          "fetched_at": "2026-05-20T12:00:00.000000Z"
        }
      },
      {
        "id": 2,
        "analysis_id": 3,
        "analysis_tile_id": 18,
        "detected_at": "2026-05-22T08:14:05.000000Z",
        "center_lat": 40.3910,
        "center_lon": 71.7920,
        "polygon_points": [
          [40.3911, 71.7918],
          [40.3912, 71.7922],
          [40.3909, 71.7923]
        ],
        "class_id": 0,
        "class_name": "construction",
        "confidence": 0.7340,
        "created_at": "2026-05-22T08:14:06.000000Z",
        "updated_at": "2026-05-22T08:14:06.000000Z",
        "country": "Uzbekistan",
        "region": "Fergana Region",
        "district": "Fergana",
        "is_on_shafof": false,
        "shafof_data": null
      }
    ],
    "total": 4821,
    "per_page": 50,
    "current_page": 1,
    "last_page": 97
  }
}
```

---

## Business logic details

### Area calculation

Each completed analysis covers a circular geographic area. The area in km² is:

$$A = \pi \times \left(\frac{r}{1000}\right)^2$$

where `r` is the radius in metres stored in the `analyses` table. The `area_scanned` field sums this formula across **all completed analyses**.

### Day-over-day change

| Stat field                        | Comparison window            | Format            |
|-----------------------------------|------------------------------|-------------------|
| `area_stats.change`               | Today vs. yesterday (km²)    | `"+12.5%"` string |
| `detected_objects_stats.change`   | Today vs. yesterday (count)  | `"+123"` string   |

"Today" = calendar day in the server's local timezone from midnight to the current moment.  
"Yesterday" = the previous full calendar day.

### Shafof Qurilish matching

For each detected object, the system searches for the nearest `shafof_qurilish_data` record with valid coordinates within a **100-metre radius**:

1. A SQL bounding-box pre-filter (≈ ±0.001° lat, ±0.0013° lon) narrows candidates efficiently.
2. The exact distance is calculated in PHP using the Haversine formula.
3. The single closest record (if within 100 m) is attached to the object as `shafof_data`.
4. `is_on_shafof` is `true` when a match exists, `false` otherwise.

The `matched_with_shafof` counter in `detected_objects_stats` reflects how many objects across the **entire dataset** (not just the current page) have a match.

---

## Error responses

| HTTP status | When                                       |
|-------------|--------------------------------------------|
| `422`       | Invalid `per_page` or `page` query param   |
| `500`       | Unexpected server error                    |

---

## Notes

- The objects list is ordered by `detected_at` descending (newest first).
- `per_page` is capped at **200** to protect against oversized responses.
- The `last_week_chart` always contains exactly **7 entries** (today and the 6 preceding days). Days with no completed analyses show `number: 0`.
- `area_scanned` and `last_week_chart` values are rounded to 4 decimal places (km²).
