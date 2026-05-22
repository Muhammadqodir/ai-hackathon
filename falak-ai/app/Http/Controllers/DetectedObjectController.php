<?php

namespace App\Http\Controllers;

use App\Models\DetectedObject;
use Illuminate\Http\JsonResponse;

class DetectedObjectController extends Controller
{
    /**
     * GET /api/detected-objects
     *
     * Returns every detected object across all analyses, ordered by
     * detection time descending. No pagination.
     */
    public function index(): JsonResponse
    {
        $objects = DetectedObject::orderBy('detected_at', 'desc')
            ->get([
                'id',
                'analysis_id',
                'analysis_tile_id',
                'detected_at',
                'center_lat',
                'center_lon',
                'polygon_points',
                'class_id',
                'class_name',
                'confidence',
            ]);

        return response()->json([
            'data'  => $objects,
            'total' => $objects->count(),
        ]);
    }
}
