<?php

namespace App\Http\Controllers;

use App\Models\ShafofQurilishData;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class ShafofQurilishController extends Controller
{
    // ── GET /api/shafof-qurilish ──────────────────────────────────────────────

    /**
     * Paginated list of construction objects with optional filters.
     *
     * Query params:
     *   search         string   Full-text search across name and organisation_name
     *   region_soato   int      Filter by region SOATO code
     *   district_soato int      Filter by district SOATO code
     *   status_id      int      1=in-progress  2=frozen  3=stopped  5=delivered
     *   sphere_id      int      Sector/sphere ID
     *   difficulty     string   I | II | III | IV
     *   per_page       int      1–200 (default 50)
     *   page           int      Page number (default 1)
     */
    public function index(Request $request): JsonResponse
    {
        $request->validate([
            'search'         => 'nullable|string|max:255',
            'region_soato'   => 'nullable|integer',
            'district_soato' => 'nullable|integer',
            'status_id'      => 'nullable|integer|in:1,2,3,5',
            'sphere_id'      => 'nullable|integer',
            'difficulty'     => 'nullable|string|in:I,II,III,IV',
            'per_page'       => 'nullable|integer|min:1|max:200',
            'page'           => 'nullable|integer|min:1',
        ]);

        $query = ShafofQurilishData::query()
            ->select([
                'object_id', 'object_status',
                'name', 'sphere_id',
                'organization_name', 'status_id', 'status_name',
                'difficulty', 'lat', 'lon',
                'region_soato', 'district_soato',
                'deadline', 'block_count', 'apartment_count',
                'reestr_number', 'fetched_at',
            ]);

        if ($search = $request->input('search')) {
            $query->where(function ($q) use ($search) {
                $q->where('name', 'like', "%{$search}%")
                  ->orWhere('organization_name', 'like', "%{$search}%")
                  ->orWhere('location_building', 'like', "%{$search}%");
            });
        }

        if ($v = $request->input('region_soato'))   $query->where('region_soato',   $v);
        if ($v = $request->input('district_soato')) $query->where('district_soato', $v);
        if ($v = $request->input('status_id'))      $query->where('status_id',      $v);
        if ($v = $request->input('sphere_id'))      $query->where('sphere_id',      $v);
        if ($v = $request->input('difficulty'))     $query->where('difficulty',     $v);

        $perPage = (int) $request->input('per_page', 50);
        $result  = $query->orderBy('object_id')->paginate($perPage);

        return response()->json($result);
    }

    // ── GET /api/shafof-qurilish/stats ────────────────────────────────────────

    /**
     * Aggregate statistics across the entire dataset.
     */
    public function stats(): JsonResponse
    {
        $byStatus = DB::table('shafof_qurilish_data')
            ->select('status_id', 'status_name', DB::raw('COUNT(*) as count'))
            ->groupBy('status_id', 'status_name')
            ->orderBy('status_id')
            ->get();

        $byDifficulty = DB::table('shafof_qurilish_data')
            ->select('difficulty', DB::raw('COUNT(*) as count'))
            ->whereNotNull('difficulty')
            ->groupBy('difficulty')
            ->orderBy('difficulty')
            ->get();

        $byRegion = DB::table('shafof_qurilish_data')
            ->select('region_soato', DB::raw('COUNT(*) as count'))
            ->whereNotNull('region_soato')
            ->groupBy('region_soato')
            ->orderByDesc('count')
            ->get();

        $totals = DB::table('shafof_qurilish_data')
            ->selectRaw('
                COUNT(*)                    AS total_objects,
                SUM(block_count)            AS total_blocks,
                SUM(apartment_count)        AS total_apartments,
                COUNT(DISTINCT region_soato)   AS total_regions,
                COUNT(DISTINCT district_soato) AS total_districts,
                COUNT(DISTINCT sphere_id)      AS total_spheres
            ')
            ->first();

        return response()->json([
            'totals'        => $totals,
            'by_status'     => $byStatus,
            'by_difficulty' => $byDifficulty,
            'by_region'     => $byRegion,
        ]);
    }

    // ── GET /api/shafof-qurilish/{object_id} ──────────────────────────────────

    /**
     * Full detail for a single construction object.
     */
    public function show(int $objectId): JsonResponse
    {
        $record = ShafofQurilishData::where('object_id', $objectId)->firstOrFail();

        return response()->json(['data' => $record]);
    }
}
