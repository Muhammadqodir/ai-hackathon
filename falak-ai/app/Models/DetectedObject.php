<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class DetectedObject extends Model
{
    protected $fillable = [
        'analysis_id',
        'analysis_tile_id',
        'detected_at',
        'center_lat',
        'center_lon',
        'polygon_points',
        'class_id',
        'class_name',
        'confidence',
    ];

    protected $casts = [
        'detected_at'    => 'datetime',
        'center_lat'     => 'float',
        'center_lon'     => 'float',
        'polygon_points' => 'array',
        'class_id'       => 'integer',
        'confidence'     => 'float',
    ];

    public function analysis(): BelongsTo
    {
        return $this->belongsTo(Analysis::class);
    }

    public function tile(): BelongsTo
    {
        return $this->belongsTo(AnalysisTile::class, 'analysis_tile_id');
    }
}
