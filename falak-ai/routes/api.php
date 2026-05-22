<?php

use App\Http\Controllers\AnalysisController;
use App\Http\Controllers\DashboardController;
use App\Http\Controllers\DetectedObjectController;
use App\Http\Controllers\ShafofQurilishController;
use Illuminate\Support\Facades\Route;

/*
|--------------------------------------------------------------------------
| API Routes
|--------------------------------------------------------------------------
*/

// ── Dashboard ─────────────────────────────────────────────────────────────────
Route::get('/dashboard', [DashboardController::class, 'index'])->name('dashboard');

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

// ── Shaffof Qurilish data ─────────────────────────────────────────────────────
Route::prefix('shafof-qurilish')->group(function () {
    Route::get('/',            [ShafofQurilishController::class, 'index'])->name('shafof-qurilish.index');
    Route::get('/stats',       [ShafofQurilishController::class, 'stats'])->name('shafof-qurilish.stats');
    Route::get('/{object_id}', [ShafofQurilishController::class, 'show'])->name('shafof-qurilish.show');
});
