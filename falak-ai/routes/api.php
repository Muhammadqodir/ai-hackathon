<?php

use App\Http\Controllers\MapTileController;
use Illuminate\Support\Facades\Route;

/*
|--------------------------------------------------------------------------
| Map Tile Routes
|--------------------------------------------------------------------------
|
| GET /api/map/tile-url?map_type=satellite|map|hybrid
|   Returns the flutter_map-compatible tile URL template and metadata.
|
| GET /api/map/tile/{z}/{x}/{y}?map_type=satellite|map|hybrid
|   Proxies a single tile from Google and streams it back (optional).
|
*/

Route::prefix('map')->group(function () {
    Route::get('tile-url', [MapTileController::class, 'tileUrl']);
    Route::get('tile/{z}/{x}/{y}', [MapTileController::class, 'proxyTile'])
        ->where(['z' => '[0-9]+', 'x' => '[0-9]+', 'y' => '[0-9]+']);
});
