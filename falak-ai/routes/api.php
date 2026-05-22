<?php

use App\Http\Controllers\AnalysisController;
use App\Http\Controllers\DetectedObjectController;
use Illuminate\Support\Facades\Route;

/*
|--------------------------------------------------------------------------
| API Routes
|--------------------------------------------------------------------------
*/

Route::prefix('analysis')->group(function () {
    // Submit a new analysis (or reuse a cached one)
    Route::post('/',              [AnalysisController::class, 'store'])->name('analysis.store');

    // Get analysis summary + aggregate stats
    Route::get('/{analysis}',         [AnalysisController::class, 'show'])->name('analysis.show');

    // Server-Sent Events real-time progress stream
    Route::get('/{analysis}/stream',  [AnalysisController::class, 'stream'])->name('analysis.stream');

    // Paginated tile results with raw detections
    Route::get('/{analysis}/tiles',   [AnalysisController::class, 'tiles'])->name('analysis.tiles');
});

// ── Detected objects ──────────────────────────────────────────────────────────
Route::get('/detected-objects', [DetectedObjectController::class, 'index'])->name('detected-objects.index');
