<?php

namespace App\Http\Controllers;

use Carbon\Carbon;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class DashboardController extends Controller
{
    /** Maximum search radius (metres) when linking a detection to shafof data. */
    private const MATCH_RADIUS_METRES = 100.0;

    // ── GET /api/dashboard ────────────────────────────────────────────────────

    public function index(Request $request): JsonResponse
    {
        return response()->json([
            'area_stats'             => $this->buildAreaStats(),
            'detected_objects_stats' => $this->buildDetectedObjectsStats(),
            'objects'                => $this->buildObjectsList(),
        ]);
    }

    // ── Area stats ─────────────────────────────────────────────────────────────

    private function buildAreaStats(): array
    {
        // Total cumulative scanned area (km²) across all completed analyses
        $totalKm2 = (float) DB::table('analyses')
            ->where('status', 'completed')
            ->sum(DB::raw('PI() * POW(radius / 1000.0, 2)'));

        $today     = Carbon::today();
        $yesterday = Carbon::yesterday();

        $todayKm2 = (float) DB::table('analyses')
            ->where('status', 'completed')
            ->whereDate('completed_at', $today)
            ->sum(DB::raw('PI() * POW(radius / 1000.0, 2)'));

        $yesterdayKm2 = (float) DB::table('analyses')
            ->where('status', 'completed')
            ->whereDate('completed_at', $yesterday)
            ->sum(DB::raw('PI() * POW(radius / 1000.0, 2)'));

        // Last 7 days: area scanned per day (fill missing days with 0)
        $chartRaw = DB::table('analyses')
            ->select(
                DB::raw('DATE(completed_at) as d'),
                DB::raw('SUM(PI() * POW(radius / 1000.0, 2)) as area')
            )
            ->where('status', 'completed')
            ->where('completed_at', '>=', Carbon::now()->subDays(6)->startOfDay())
            ->groupBy(DB::raw('DATE(completed_at)'))
            ->pluck('area', 'd');

        $lastWeekChart = [];
        for ($i = 6; $i >= 0; $i--) {
            $day = Carbon::now()->subDays($i);
            $lastWeekChart[] = [
                'date'   => $day->format('d.m.Y'),
                'number' => round((float) ($chartRaw[$day->format('Y-m-d')] ?? 0), 4),
            ];
        }

        return [
            'area_scanned'    => round($totalKm2, 4),
            'change'          => $this->pctChange($todayKm2, $yesterdayKm2),
            'last_week_chart' => $lastWeekChart,
        ];
    }

    // ── Detected-objects stats ─────────────────────────────────────────────────

    private function buildDetectedObjectsStats(): array
    {
        $total = DB::table('detected_objects')->count();

        $today     = Carbon::today();
        $yesterday = Carbon::yesterday();

        $todayCount     = DB::table('detected_objects')->whereDate('detected_at', $today)->count();
        $yesterdayCount = DB::table('detected_objects')->whereDate('detected_at', $yesterday)->count();

        $byClass = DB::table('detected_objects')
            ->select('class_name', DB::raw('COUNT(*) as number'))
            ->groupBy('class_name')
            ->orderByDesc('number')
            ->get()
            ->map(fn($r) => ['class' => $r->class_name, 'number' => (int) $r->number])
            ->values()
            ->toArray();

        $matchedWithShafof = $this->countMatchedWithShafof();

        return [
            'detected_objects'    => $total,
            'change'              => $this->absChange($todayCount, $yesterdayCount),
            'by_class'            => $byClass,
            'matched_with_shafof' => $matchedWithShafof,
        ];
    }

    // ── Objects list ───────────────────────────────────────────────────────────

    private function buildObjectsList(): array
    {
        $rows = DB::table('detected_objects as d')
            ->join('analyses as a', 'd.analysis_id', '=', 'a.id')
            ->select([
                'd.id',
                'd.analysis_id',
                'd.analysis_tile_id',
                'd.detected_at',
                'd.center_lat',
                'd.center_lon',
                'd.polygon_points',
                'd.class_id',
                'd.class_name',
                'd.confidence',
                'd.created_at',
                'd.updated_at',
                'a.country',
                'a.region',
                'a.district',
            ])
            ->orderByDesc('d.detected_at')
            ->get();

        $data = [];
        foreach ($rows as $row) {
            $item = (array) $row;

            if (is_string($item['polygon_points'])) {
                $item['polygon_points'] = json_decode($item['polygon_points'], true);
            }

            $shafof = $this->findNearestShafof((float) $row->center_lat, (float) $row->center_lon);

            $item['is_on_shafof'] = $shafof !== null;
            $item['shafof_data']  = $shafof;

            $data[] = $item;
        }

        return [
            'data'  => $data,
            'total' => count($data),
        ];
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /**
     * Find the nearest shafof_qurilish_data record within MATCH_RADIUS_METRES.
     *
     * The bounding-box WHERE clause pre-filters to a small tile so MySQL can
     * use the idx_lat_lon index.  The Haversine expression in HAVING then
     * applies the exact circular boundary.  LEAST(1, SQRT(...)) guards ASIN
     * against a domain error on floating-point values marginally above 1.
     */
    private function findNearestShafof(float $lat, float $lon): ?array
    {
        $deltaLat = self::MATCH_RADIUS_METRES / 111320.0;
        $deltaLon = self::MATCH_RADIUS_METRES / (111320.0 * cos(deg2rad($lat)));

        $nearest = DB::table('shafof_qurilish_data')
            ->whereNotNull('lat')
            ->whereNotNull('lon')
            ->whereBetween('lat', [$lat - $deltaLat, $lat + $deltaLat])
            ->whereBetween('lon', [$lon - $deltaLon, $lon + $deltaLon])
            ->selectRaw(
                '*, (6371000 * 2 * ASIN(LEAST(1, SQRT(
                    POW(SIN(RADIANS((lat - ?) / 2)), 2) +
                    COS(RADIANS(?)) * COS(RADIANS(lat)) *
                    POW(SIN(RADIANS((lon - ?) / 2)), 2)
                )))) AS _distance',
                [$lat, $lat, $lon]
            )
            ->having('_distance', '<=', self::MATCH_RADIUS_METRES)
            ->orderBy('_distance')
            ->first();

        if ($nearest === null) {
            return null;
        }

        $result = (array) $nearest;
        unset($result['_distance']);

        foreach (['rating', 'blocks'] as $field) {
            if (isset($result[$field]) && is_string($result[$field])) {
                $result[$field] = json_decode($result[$field], true);
            }
        }

        return $result;
    }

    /**
     * Count detected objects that have at least one shafof match within 100 m.
     *
     * Uses a correlated EXISTS sub-query with MySQL trig functions.
     * The bounding-box pre-conditions let MySQL use the idx_lat_lon index on
     * shafof_qurilish_data before evaluating the more expensive Haversine.
     * The lon delta is computed per-row via COS(RADIANS(d.center_lat)) so it
     * is correct at every latitude.  LEAST(1, ...) prevents ASIN domain errors.
     */
    private function countMatchedWithShafof(): int
    {
        return (int) DB::table('detected_objects as d')
            ->whereExists(function ($query) {
                $query->from('shafof_qurilish_data as s')
                    ->whereNotNull('s.lat')
                    ->whereNotNull('s.lon')
                    ->whereRaw('s.lat BETWEEN d.center_lat - 0.001 AND d.center_lat + 0.001')
                    ->whereRaw(
                        's.lon BETWEEN
                            d.center_lon - (0.001 / COS(RADIANS(d.center_lat))) AND
                            d.center_lon + (0.001 / COS(RADIANS(d.center_lat)))'
                    )
                    ->whereRaw(
                        '(6371000 * 2 * ASIN(LEAST(1, SQRT(
                            POW(SIN(RADIANS((s.lat - d.center_lat) / 2)), 2) +
                            COS(RADIANS(d.center_lat)) * COS(RADIANS(s.lat)) *
                            POW(SIN(RADIANS((s.lon - d.center_lon) / 2)), 2)
                        )))) <= ?',
                        [self::MATCH_RADIUS_METRES]
                    );
            })
            ->count();
    }

    /**
     * Percentage day-over-day change.
     * Returns a string like "+12.5%" or "-3.0%".
     */
    private function pctChange(float $today, float $yesterday): string
    {
        if ($yesterday == 0.0) {
            return $today > 0.0 ? '+100%' : '0%';
        }

        $pct  = (($today - $yesterday) / $yesterday) * 100.0;
        $sign = $pct >= 0 ? '+' : '';

        return $sign . number_format($pct, 1) . '%';
    }

    /**
     * Absolute day-over-day change in object count.
     * Returns a string like "+123" or "-50".
     */
    private function absChange(int $today, int $yesterday): string
    {
        $diff = $today - $yesterday;
        $sign = $diff >= 0 ? '+' : '';

        return $sign . $diff;
    }
}
