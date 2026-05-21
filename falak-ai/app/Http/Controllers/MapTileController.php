<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class MapTileController extends Controller
{
    /**
     * Google Maps tile layer codes.
     *
     * s = satellite
     * m = roadmap
     * y = hybrid (satellite + roads overlay)
     */
    private const LAYER_CODES = [
        'satellite' => 's',
        'map'       => 'm',
        'hybrid'    => 'y',
    ];

    /**
     * Return the tile URL template for flutter_map.
     *
     * GET /api/map/tile-url?map_type=satellite|map|hybrid
     *
     * Response:
     * {
     *   "tile_url": "https://mt{s}.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
     *   "map_type": "satellite",
     *   "subdomains": ["0","1","2","3"],
     *   "max_zoom": 20
     * }
     *
     * flutter_map usage:
     *   TileLayer(
     *     urlTemplate: response['tile_url'],
     *     subdomains: ['0','1','2','3'],
     *     maxZoom: 20,
     *   )
     */
    public function tileUrl(Request $request): JsonResponse
    {
        $request->validate([
            'map_type' => 'sometimes|string|in:satellite,map,hybrid',
        ]);

        $mapType = $request->query('map_type', 'satellite');
        $lyrs    = self::LAYER_CODES[$mapType];

        // flutter_map uses {s} for subdomains, {x}, {y}, {z} for tile coords.
        $tileUrl = "https://mt{s}.google.com/vt/lyrs={$lyrs}&hl=en&x={x}&y={y}&z={z}";

        return response()->json([
            'tile_url'   => $tileUrl,
            'map_type'   => $mapType,
            'subdomains' => ['0', '1', '2', '3'],
            'max_zoom'   => 20,
        ]);
    }

    /**
     * Proxy a single tile and stream it back.
     *
     * GET /api/map/tile/{z}/{x}/{y}?map_type=satellite|map|hybrid
     *
     * Useful when the client cannot hit Google directly (CORS, firewall, etc.).
     */
    public function proxyTile(Request $request, int $z, int $x, int $y)
    {
        $request->validate([
            'map_type' => 'sometimes|string|in:satellite,map,hybrid',
        ]);

        if ($z < 0 || $z > 20 || $x < 0 || $y < 0) {
            abort(400, 'Invalid tile coordinates.');
        }

        $limit = (2 ** $z) - 1;
        if ($x > $limit || $y > $limit) {
            abort(400, 'Tile coordinates out of range for the given zoom level.');
        }

        $mapType  = $request->query('map_type', 'satellite');
        $lyrs     = self::LAYER_CODES[$mapType];
        $subdomain = $x % 4; // rotate through 0-3

        $url = "https://mt{$subdomain}.google.com/vt/lyrs={$lyrs}&hl=en&x={$x}&y={$y}&z={$z}";

        $client   = new \Illuminate\Http\Client\PendingRequest();
        $response = \Illuminate\Support\Facades\Http::withHeaders([
            'User-Agent' => 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          . 'AppleWebKit/537.36 (KHTML, like Gecko) '
                          . 'Chrome/124.0.0.0 Safari/537.36',
            'Accept'     => 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer'    => 'https://www.google.com/maps',
        ])->timeout(15)->get($url);

        if (!$response->successful()) {
            abort(502, 'Failed to fetch tile from upstream provider.');
        }

        $contentType = $response->header('Content-Type') ?? 'image/jpeg';

        return response($response->body(), 200)
            ->header('Content-Type', $contentType)
            ->header('Cache-Control', 'public, max-age=86400');
    }
}
