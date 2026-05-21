<?php

namespace App\Http\Controllers;

use App\Jobs\ProcessAnalysisJob;
use App\Models\Analysis;
use App\Models\AnalysisEvent;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\StreamedResponse;

class AnalysisController extends Controller
{
    // ── POST /api/analysis ────────────────────────────────────────────────────

    /**
     * Create (or reuse) a satellite analysis job.
     *
     * Body params:
     *   lat      float    required  Centre latitude  (-90 … 90)
     *   lon      float    required  Centre longitude (-180 … 180)
     *   radius   int      required  Radius in metres (100 … 5 000)
     *   zoom     int      optional  Tile zoom level  (15 … 20, default 18)
     *   provider string   optional  "esri" | "google" | "all" (default "google")
     */
    public function store(Request $request): JsonResponse
    {
        $data = $request->validate([
            'lat'      => 'required|numeric|between:-90,90',
            'lon'      => 'required|numeric|between:-180,180',
            'radius'   => 'required|integer|min:100|max:5000',
            'zoom'     => 'integer|between:15,20',
            'provider' => 'string|in:esri,google,all',
        ]);

        $zoom     = (int)   ($data['zoom']     ?? config('analysis.default_zoom', 17));
        $provider =         ($data['provider'] ?? config('analysis.default_provider', 'google'));
        $lat      = (float) $data['lat'];
        $lon      = (float) $data['lon'];
        $radius   = (int)   $data['radius'];

        $hash = Analysis::makeHash($lat, $lon, $radius, $zoom, $provider);

        // ── deduplication ──────────────────────────────────────────────────
        // Two simultaneous identical requests are serialised here by the
        // unique constraint on cache_hash.
        $analysis = Analysis::firstOrCreate(
            ['cache_hash' => $hash],
            [
                'lat'      => $lat,
                'lon'      => $lon,
                'radius'   => $radius,
                'zoom'     => $zoom,
                'provider' => $provider,
                'status'   => 'pending',
            ]
        );

        // If the previous run failed, reset it so it can be retried
        $wasRetried = false;
        if (! $analysis->wasRecentlyCreated && $analysis->status === 'failed') {
            $analysis->tiles()->delete();
            $analysis->events()->delete();
            $analysis->update([
                'status'          => 'pending',
                'error_message'   => null,
                'processed_tiles' => 0,
                'total_tiles'     => null,
                'bbox'            => null,
                'started_at'      => null,
                'completed_at'    => null,
            ]);
            $wasRetried = true;
        }

        // Dispatch only for new or retried jobs
        if ($analysis->wasRecentlyCreated || $wasRetried) {
            ProcessAnalysisJob::dispatch($analysis);
        }

        $httpStatus = $analysis->wasRecentlyCreated ? 201 : 200;

        return response()->json($this->buildSummary($analysis), $httpStatus);
    }

    // ── GET /api/analysis/{analysis} ─────────────────────────────────────────

    public function show(Analysis $analysis): JsonResponse
    {
        return response()->json($this->buildSummary($analysis, detailed: true));
    }

    // ── GET /api/analysis/{analysis}/stream ──────────────────────────────────

    /**
     * Server-Sent Events stream.
     *
     * The client connects here after receiving stream_url from POST /api/analysis.
     * Events are read from the analysis_events table, streamed in real-time.
     *
     * SSE spec headers the client must accept:
     *   Content-Type: text/event-stream
     *
     * Reconnection: the browser will automatically reconnect using the
     * Last-Event-ID header; this endpoint honours it.
     *
     * Event names: start | status | tile_processed | complete | error | timeout
     */
    public function stream(Analysis $analysis): StreamedResponse
    {
        return response()->stream(
            function () use ($analysis) {
                // Disable all PHP output buffering layers
                while (ob_get_level() > 0) {
                    ob_end_clean();
                }

                // Honour SSE reconnection cursor
                $lastId  = (int) request()->header('Last-Event-ID', 0);
                $deadline = time() + 300;   // 5-minute hard cap per connection

                // SSE retry hint: reconnect after 3 s on network drop
                echo "retry: 3000\n\n";
                flush();

                // If already completed before the client connected, replay events
                while (time() < $deadline) {
                    if (connection_aborted()) {
                        break;
                    }

                    $events = AnalysisEvent::where('analysis_id', $analysis->id)
                        ->where('id', '>', $lastId)
                        ->orderBy('id')
                        ->limit(100)
                        ->get();

                    foreach ($events as $event) {
                        $lastId = $event->id;

                        echo "id: {$event->id}\n";
                        echo "event: {$event->event_type}\n";
                        echo 'data: ' . json_encode($event->payload) . "\n\n";
                        flush();

                        if (in_array($event->event_type, ['complete', 'error'])) {
                            return;
                        }
                    }

                    // Guard against missed terminal events
                    $analysis->refresh();
                    if ($analysis->status === 'completed') {
                        echo "event: complete\n";
                        echo 'data: ' . json_encode([
                            'message'          => 'Analysis complete.',
                            'total_detections' => $analysis->totalDetections(),
                        ]) . "\n\n";
                        flush();
                        return;
                    }
                    if ($analysis->status === 'failed') {
                        echo "event: error\n";
                        echo 'data: ' . json_encode([
                            'message' => $analysis->error_message ?? 'Analysis failed.',
                        ]) . "\n\n";
                        flush();
                        return;
                    }

                    // Heartbeat comment keeps the TCP connection alive through proxies
                    echo ": ping\n\n";
                    flush();

                    sleep(1);
                }

                echo "event: timeout\n";
                echo 'data: ' . json_encode(['message' => 'Stream timeout. Reconnect or poll /api/analysis/{id}.']) . "\n\n";
                flush();
            },
            200,
            [
                'Content-Type'      => 'text/event-stream',
                'Cache-Control'     => 'no-cache, no-transform',
                'X-Accel-Buffering' => 'no',    // disable Nginx proxy buffering
                'Connection'        => 'keep-alive',
            ]
        );
    }

    // ── GET /api/analysis/{analysis}/tiles ───────────────────────────────────

    /**
     * Paginated list of tile results with detections.
     *
     * Query params:
     *   per_page  int  (default 50, max 200)
     *   page      int
     */
    public function tiles(Analysis $analysis): JsonResponse
    {
        $perPage = min((int) request('per_page', 50), 200);

        $tiles = $analysis->tiles()
            ->select([
                'id', 'tile_x', 'tile_y', 'tile_z', 'provider',
                'detections', 'bbox', 'detection_count', 'from_cache', 'created_at',
            ])
            ->orderBy('id')
            ->paginate($perPage);

        return response()->json($tiles);
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    private function buildSummary(Analysis $analysis, bool $detailed = false): array
    {
        $base = [
            'id'               => $analysis->id,
            'status'           => $analysis->status,
            'lat'              => $analysis->lat,
            'lon'              => $analysis->lon,
            'radius'           => $analysis->radius,
            'zoom'             => $analysis->zoom,
            'provider'         => $analysis->provider,
            'total_tiles'      => $analysis->total_tiles,
            'processed_tiles'  => $analysis->processed_tiles,
            'bbox'             => $analysis->bbox,
            'error_message'    => $analysis->error_message,
            'started_at'       => $analysis->started_at,
            'completed_at'     => $analysis->completed_at,
            'stream_url'       => route('analysis.stream',  $analysis),
            'results_url'      => route('analysis.show',    $analysis),
            'tiles_url'        => route('analysis.tiles',   $analysis),
        ];

        if ($detailed) {
            $base['total_detections'] = $analysis->totalDetections();
        }

        return $base;
    }
}
