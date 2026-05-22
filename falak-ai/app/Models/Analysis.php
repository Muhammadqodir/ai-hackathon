<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Analysis extends Model
{
    protected $fillable = [
        'cache_hash',
        'lat',
        'lon',
        'radius',
        'zoom',
        'provider',
        'status',
        'total_tiles',
        'processed_tiles',
        'error_message',
        'bbox',
        'country',
        'region',
        'district',
        'started_at',
        'completed_at',
    ];

    protected $casts = [
        'lat'           => 'float',
        'lon'           => 'float',
        'bbox'          => 'array',
        'started_at'    => 'datetime',
        'completed_at'  => 'datetime',
    ];

    public function tiles(): HasMany
    {
        return $this->hasMany(AnalysisTile::class);
    }

    public function events(): HasMany
    {
        return $this->hasMany(AnalysisEvent::class);
    }

    public function totalDetections(): int
    {
        return (int) $this->tiles()->sum('detection_count');
    }

    /**
     * Generate the cache hash for a given parameter set.
     */
    public static function makeHash(
        float $lat,
        float $lon,
        int $radius,
        int $zoom,
        string $provider
    ): string {
        return hash('sha256', implode(':', [
            round($lat, 7),
            round($lon, 7),
            $radius,
            $zoom,
            $provider,
        ]));
    }
}
