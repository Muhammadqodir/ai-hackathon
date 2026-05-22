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
            'objects'                => $this->buildObjectsList($request),
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

    private function buildObjectsList(Request $request): array
    {
        $perPage = min((int) $request->input('per_page', 50), 200);
        $page    = max((int) $request->input('page', 1), 1);
        $offset  = ($page - 1) * $perPage;

        $total = DB::table('detected_objects')->count();

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
            ->limit($perPage)
            ->offset($offset)
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
            'data'         => $data,
            'total'        => $total,
            'per_page'     => $perPage,
            'current_page' => $page,
            'last_page'    => (int) ceil($total / max($perPage, 1)),
        ];
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    /**
     * Find the nearest shafof_qurilish_data record within MATCH_RADIUS_METRES.
     * Uses a bounding-box pre-filter in SQL, then exact Haversine in PHP.
     * Returns null when no record exists within the radius.
     */
    private function findNearestShafof(float $lat, float $lon): ?array
    {
        $deltaLat = self::MATCH_RADIUS_METRES / 111320.0;
        $deltaLon = self::MATCH_RADIUS_METRES / (111320.0 * cos(deg2rad($lat)));

        $candidates = DB::table('shafof_qurilish_data')
            ->whereNotNull('lat')
            ->whereNotNull('lon')
            ->whereBetween('lat', [$lat - $deltaLat, $lat + $deltaLat])
            ->whereBetween('lon', [$lon - $deltaLon, $lon + $deltaLon])
            ->get();

        $nearest = null;
        $minDist  = PHP_FLOAT_MAX;

        foreach ($candidates as $s) {
            $dist = $this->haversine($lat, $lon, (float) $s->lat, (float) $s->lon);
            if ($dist <= self::MATCH_RADIUS_METRES && $dist < $minDist) {
                $minDist = $dist;
                $nearest  = $s;
            }
        }

        if ($nearest === null) {
            return null;
        }

        $result = (array) $nearest;
        foreach (['rating', 'blocks'] as $field) {
            if (isset($result[$field]) && is_string($result[$field])) {
                $result[$field] = json_decode($result[$field], true);
            }
        }

        return $result;
    }

    /**
     * Count detected objects that have at least one shafof match within 100 m.
     * Uses a bounding-box EXISTS sub-query followed by Haversine for precision.
     */
    private function countMatchedWithShafof(): int
    {
        return (int) DB::table('detected_objects as d')
            ->whereExists(function ($query) {
                $query->from('shafof_qurilish_data as s')
                    ->whereNotNull('s.lat')
                    ->whereNotNull('s.lon')
                    // Quick bounding-box (≈100 m) before the expensive trig
                    ->whereRaw('s.lat BETWEEN d.center_lat - 0.001  AND d.center_lat + 0.001')
                    ->whereRaw('s.lon BETWEEN d.center_lon - 0.0013 AND d.center_lon + 0.0013')
                    // Exact Haversine using the numerically stable formula
                    ->whereRaw(
                        '(6371000 * 2 * ASIN(SQRT(
                            POW(SIN(RADIANS((s.lat - d.center_lat) / 2)), 2) +
                            COS(RADIANS(d.center_lat)) * COS(RADIANS(s.lat)) *
                            POW(SIN(RADIANS((s.lon - d.center_lon) / 2)), 2)
                        ))) <= ?',
                        [self::MATCH_RADIUS_METRES]
                    );
            })
            ->count();
    }

    /** Haversine distance in metres between two WGS-84 points. */
    private function haversine(float $lat1, float $lon1, float $lat2, float $lon2): float
    {
        $R    = 6371000.0;
        $phi1 = deg2rad($lat1);
        $phi2 = deg2rad($lat2);
        $dphi = deg2rad($lat2 - $lat1);
        $dlam = deg2rad($lon2 - $lon1);

        $a = sin($dphi / 2) ** 2 + cos($phi1) * cos($phi2) * sin($dlam / 2) ** 2;

        return 2.0 * $R * asin(sqrt($a));
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
