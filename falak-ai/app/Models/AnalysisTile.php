<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class AnalysisTile extends Model
{
    protected $fillable = [
        'analysis_id',
        'tile_x',
        'tile_y',
        'tile_z',
        'provider',
        'cache_key',
        'detections',
        'bbox',
        'detection_count',
        'from_cache',
    ];

    protected $casts = [
        'detections' => 'array',
        'bbox'       => 'array',
        'from_cache' => 'boolean',
    ];

    public function analysis(): BelongsTo
    {
        return $this->belongsTo(Analysis::class);
    }
}
