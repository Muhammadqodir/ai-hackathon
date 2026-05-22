<?php

namespace App\Jobs;

use App\Models\Analysis;
use App\Models\AnalysisEvent;
use App\Models\AnalysisTile;
use App\Models\DetectedObject;
use App\Services\GeocodingService;
use Illuminate\Bus\Queueable;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;
use Illuminate\Support\Facades\Log;

class ProcessAnalysisJob implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, Queueable, SerializesModels;

    /**
     * Maximum seconds this job may run.
     * Large radius at high zoom can produce hundreds of tiles × ~2 s each.
     */
    public int $timeout = 3600;

    /**
     * Never auto-retry – a failure indicates a real error (bad model path,
     * network outage, etc.). The client can re-submit via POST /api/analysis.
     */
    public int $tries = 1;

    public function __construct(public Analysis $analysis) {}

    // ─────────────────────────────────────────────────────────────────────────

    public function handle(): void
    {
        $id = $this->analysis->id;

        Log::info("[Analysis:{$id}] Job started.");

        $this->analysis->update([
            'status'     => 'processing',
            'started_at' => now(),
        ]);
        $this->emitEvent('status', ['message' => 'Job started.']);

        // ── reverse-geocode the centre point (non-fatal) ──────────────────
        try {
            $this->emitEvent('status', ['message' => 'Resolving location…']);
            $geo = app(GeocodingService::class)->reverseGeocode(
                $this->analysis->lat,
                $this->analysis->lon,
            );
            if (array_filter($geo)) {
                $this->analysis->update($geo);
                Log::info("[Analysis:{$id}] Geocode result", $geo);
            }
        } catch (\Throwable $e) {
            // Geocoding failure must never abort the analysis job
            Log::warning("[Analysis:{$id}] Geocoding failed: " . $e->getMessage());
        }

        $pythonBin  = config('analysis.python_bin');
        $scriptPath = base_path('../ai-model/tile_processor.py');
        $cacheDir   = config('analysis.cache_dir');
        $modelPath  = config('analysis.model_path');

        Log::info("[Analysis:{$id}] Resolved paths", [
            'python_bin'  => $pythonBin,
            'script_path' => $scriptPath,
            'model_path'  => $modelPath,
            'cache_dir'   => $cacheDir,
            'python_exists' => file_exists($pythonBin),
            'script_exists' => file_exists($scriptPath),
            'model_exists'  => file_exists($modelPath),
        ]);

        if (! file_exists($pythonBin)) {
            $this->abort("Python binary not found: {$pythonBin}");
            return;
        }

        if (! file_exists($scriptPath)) {
            $this->abort("Python script not found: {$scriptPath}");
            return;
        }

        if (! file_exists($modelPath)) {
            $this->abort("YOLO model not found: {$modelPath}");
            return;
        }

        // Build command as an array – avoids shell injection entirely
        $cmd = array_map('strval', [
            $pythonBin,
            $scriptPath,
            '--lat',       $this->analysis->lat,
            '--lon',       $this->analysis->lon,
            '--radius',    $this->analysis->radius,
            '--zoom',      $this->analysis->zoom,
            '--model',     $modelPath,
            '--cache-dir', $cacheDir,
            '--workers',   config('analysis.workers', 8),
            '--provider',  $this->analysis->provider,
            '--conf',      config('analysis.conf_threshold', 0.25),
            '--iou',       config('analysis.iou_threshold', 0.45),
            '--imgsz',     config('analysis.imgsz', 640),
        ]);

        Log::info("[Analysis:{$id}] Spawning Python process", [
            'command' => implode(' ', $cmd),
        ]);

        $descriptors = [
            0 => ['pipe', 'r'],   // stdin  (closed immediately)
            1 => ['pipe', 'w'],   // stdout (JSON progress lines)
            2 => ['pipe', 'w'],   // stderr (captured for error reporting)
        ];

        $process = proc_open($cmd, $descriptors, $pipes);

        if (! is_resource($process)) {
            $this->abort('proc_open failed – could not spawn Python process.');
            return;
        }

        Log::info("[Analysis:{$id}] Python process spawned, reading stdout…");

        fclose($pipes[0]);   // no stdin needed

        $linesRead = 0;

        // Read stdout line-by-line (blocking). The Python script flushes after
        // every emit(), so each fgets() returns as soon as a line is ready.
        while (! feof($pipes[1])) {
            $line = fgets($pipes[1]);
            if ($line === false) {
                break;
            }
            $line = trim($line);
            if ($line === '') {
                continue;
            }
            $linesRead++;
            Log::debug("[Analysis:{$id}] stdout line #{$linesRead}: {$line}");

            $data = json_decode($line, true);
            if (is_array($data)) {
                $this->handlePythonEvent($data);
            } else {
                Log::warning("[Analysis:{$id}] Non-JSON stdout line: {$line}");
            }
        }

        $stderr   = stream_get_contents($pipes[2]);
        fclose($pipes[1]);
        fclose($pipes[2]);
        $exitCode = proc_close($process);

        Log::info("[Analysis:{$id}] Python process finished", [
            'exit_code'  => $exitCode,
            'lines_read' => $linesRead,
            'stderr'     => $stderr ?: '(empty)',
        ]);

        // If Python exited non-zero and we haven't already marked it complete/failed
        $this->analysis->refresh();
        if ($exitCode !== 0 && ! in_array($this->analysis->status, ['completed', 'failed'])) {
            $msg = $stderr
                ? 'Python stderr: ' . substr($stderr, -1000)
                : "Python process exited with code {$exitCode}.";
            $this->abort($msg);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────

    private function handlePythonEvent(array $data): void
    {
        $type = $data['type'] ?? 'unknown';

        switch ($type) {

            case 'start':
                $this->analysis->update([
                    'total_tiles' => $data['total_tiles'] ?? 0,
                    'bbox'        => $data['bbox'] ?? null,
                ]);
                $this->emitEvent('start', $data);
                break;

            case 'status':
                $this->emitEvent('status', $data);
                break;

            case 'tile_processed':
                $tile = $data['tile'] ?? [];
                if (! empty($tile) && isset($tile['x'], $tile['y'], $tile['z'])) {
                    $detections = $tile['detections'] ?? [];

                    // Upsert avoids duplicate rows if the job is somehow re-run
                    $tileRecord = AnalysisTile::updateOrCreate(
                        [
                            'analysis_id' => $this->analysis->id,
                            'tile_x'      => $tile['x'],
                            'tile_y'      => $tile['y'],
                            'tile_z'      => $tile['z'],
                        ],
                        [
                            'provider'        => $this->analysis->provider,
                            'cache_key'       => "{$this->analysis->provider}_{$tile['z']}_{$tile['x']}_{$tile['y']}",
                            'detections'      => $detections,
                            'bbox'            => $tile['bbox'] ?? [],
                            'detection_count' => count($detections),
                            'from_cache'      => (bool) ($tile['cached'] ?? false),
                        ]
                    );

                    // Persist individual detected objects with geo coordinates
                    $bbox = $tile['bbox'] ?? [];
                    if (! empty($bbox) && isset($bbox['north'], $bbox['south'], $bbox['east'], $bbox['west'])) {
                        $this->saveDetectedObjects($tileRecord, $detections, $bbox);
                    }

                    $this->analysis->increment('processed_tiles');
                }

                // Emit a slim progress event (full detections stay in the DB record)
                $this->emitEvent('tile_processed', [
                    'progress'        => $data['progress']              ?? 0,
                    'total'           => $data['total']                 ?? 0,
                    'tile_x'          => $tile['x']                     ?? 0,
                    'tile_y'          => $tile['y']                     ?? 0,
                    'tile_z'          => $tile['z']                     ?? 0,
                    'detection_count' => count($tile['detections']      ?? []),
                    'from_cache'      => (bool) ($tile['cached']        ?? false),
                    'bbox'            => $tile['bbox']                  ?? [],
                    'detections'      => $tile['detections']            ?? [],
                ]);
                break;

            case 'complete':
                $this->analysis->update([
                    'status'       => 'completed',
                    'completed_at' => now(),
                ]);
                $this->emitEvent('complete', [
                    'total_tiles'      => $data['total_tiles'] ?? $this->analysis->total_tiles,
                    'processed'        => $data['processed']  ?? $this->analysis->processed_tiles,
                    'total_detections' => $this->analysis->totalDetections(),
                ]);
                break;

            case 'error':
                $msg = $data['message'] ?? 'Unknown error from Python script.';
                $this->analysis->update([
                    'status'        => 'failed',
                    'error_message' => $msg,
                ]);
                $this->emitEvent('error', ['message' => $msg]);
                break;

            default:
                Log::debug('ProcessAnalysisJob: unknown event type', ['type' => $type, 'data' => $data]);
        }
    }

    private function emitEvent(string $type, array $payload): void
    {
        AnalysisEvent::create([
            'analysis_id' => $this->analysis->id,
            'event_type'  => $type,
            'payload'     => $payload,
        ]);
    }

    /**
     * Convert pixel-space polygon points to WGS-84 coordinates and persist
     * each detection as a DetectedObject row.
     *
     * Pixel coordinates come from the Python script relative to the 256×256
     * tile image. The tile's geographic bounding box (north/south/east/west)
     * is used to interpolate each point to lat/lon.
     *
     * @param  AnalysisTile  $tileRecord
     * @param  array         $detections  Raw detection dicts from Python
     * @param  array         $bbox        {north, south, east, west}
     */
    private function saveDetectedObjects(AnalysisTile $tileRecord, array $detections, array $bbox): void
    {
        $north = (float) $bbox['north'];
        $south = (float) $bbox['south'];
        $east  = (float) $bbox['east'];
        $west  = (float) $bbox['west'];

        $latSpan = $north - $south;
        $lonSpan = $east  - $west;

        // Tile images are always 256×256 pixels
        $tileSize = 256.0;

        $now = now();

        foreach ($detections as $det) {
            $polygon = $det['polygon'] ?? null;

            // Skip detections without a segmentation polygon
            if (empty($polygon) || ! is_array($polygon)) {
                continue;
            }

            // Convert pixel [[x,y],...] to geo [[lat,lon],...]
            $geoPoints = [];
            foreach ($polygon as $point) {
                if (! isset($point[0], $point[1])) {
                    continue;
                }
                $px = (float) $point[0];
                $py = (float) $point[1];

                $lat = $north - ($py / $tileSize) * $latSpan;
                $lon = $west  + ($px / $tileSize) * $lonSpan;

                $geoPoints[] = [round($lat, 7), round($lon, 7)];
            }

            if (empty($geoPoints)) {
                continue;
            }

            // Centre = arithmetic mean of polygon vertices
            $centerLat = array_sum(array_column($geoPoints, 0)) / count($geoPoints);
            $centerLon = array_sum(array_column($geoPoints, 1)) / count($geoPoints);

            DetectedObject::create([
                'analysis_id'      => $this->analysis->id,
                'analysis_tile_id' => $tileRecord->id,
                'detected_at'      => $now,
                'center_lat'       => round($centerLat, 7),
                'center_lon'       => round($centerLon, 7),
                'polygon_points'   => $geoPoints,
                'class_id'         => (int) ($det['class_id']   ?? 0),
                'class_name'       => (string) ($det['class_name'] ?? ''),
                'confidence'       => isset($det['confidence']) ? round((float) $det['confidence'], 4) : null,
            ]);
        }
    }

    private function abort(string $message): void
    {
        Log::error('ProcessAnalysisJob failed', [
            'analysis_id' => $this->analysis->id,
            'message'     => $message,
        ]);
        $this->analysis->update([
            'status'        => 'failed',
            'error_message' => $message,
        ]);
        $this->emitEvent('error', ['message' => $message]);
    }

    /**
     * Called by Laravel when the job is forcibly failed (e.g. timeout).
     */
    public function failed(\Throwable $exception): void
    {
        $this->abort($exception->getMessage());
    }
}
