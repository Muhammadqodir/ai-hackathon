<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ShafofQurilishData extends Model
{
    protected $table = 'shafof_qurilish_data';

    protected $fillable = [
        'object_id', 'object_status',
        'name', 'task_id', 'sphere_id', 'location_building', 'difficulty',
        'organization_name', 'loyiha', 'pudrat',
        'status_id', 'status_name',
        'lat', 'lon',
        'region_soato', 'district_soato',
        'deadline', 'closed_at', 'source_created_at',
        'number_protocol', 'reestr_number', 'rating',
        'block_count', 'apartment_count', 'blocks', 'conclusion_url',
        'fetched_at',
    ];

    protected $casts = [
        'lat'               => 'float',
        'lon'               => 'float',
        'block_count'       => 'integer',
        'apartment_count'   => 'integer',
        'blocks'            => 'array',
        'rating'            => 'array',
        'deadline'          => 'date:Y-m-d',
        'closed_at'         => 'datetime',
        'source_created_at' => 'datetime',
        'fetched_at'        => 'datetime',
    ];
}
