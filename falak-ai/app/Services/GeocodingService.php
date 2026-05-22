<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

/**
 * Reverse-geocodes a lat/lon coordinate using the OSM Nominatim API.
 *
 * Nominatim is free, requires no API key, and enforces a 1 req/s rate limit
 * per the usage policy (https://operations.osmfoundation.org/policies/nominatim/).
 * We honour this by throttling to one request per second.
 *
 * Returns country, region (state/oblast), and district (county/city).
 */
class GeocodingService
{
    private const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/reverse';

    /**
     * Reverse-geocode the given coordinates.
     *
     * @return array{country: string|null, region: string|null, district: string|null}
     */
    public function reverseGeocode(float $lat, float $lon): array
    {
        try {
            $response = Http::timeout(10)
                ->withHeaders([
                    // Nominatim policy: must identify your application
                    'User-Agent' => 'FalakAI/1.0 (satellite-construction-analysis)',
                    'Accept'     => 'application/json',
                ])
                ->get(self::NOMINATIM_URL, [
                    'lat'            => $lat,
                    'lon'            => $lon,
                    'format'         => 'json',
                    'addressdetails' => 1,
                    'zoom'           => 10,   // administrative level detail
                ]);

            if (! $response->successful()) {
                Log::warning('GeocodingService: Nominatim returned HTTP ' . $response->status(), [
                    'lat' => $lat, 'lon' => $lon,
                ]);
                return $this->emptyResult();
            }

            $body    = $response->json();
            $address = $body['address'] ?? [];

            return [
                'country'  => $this->extractCountry($address),
                'region'   => $this->extractRegion($address),
                'district' => $this->extractDistrict($address),
            ];
        } catch (\Throwable $e) {
            Log::warning('GeocodingService: reverse geocode failed', [
                'lat'     => $lat,
                'lon'     => $lon,
                'error'   => $e->getMessage(),
            ]);
            return $this->emptyResult();
        }
    }

    // ── field extractors ──────────────────────────────────────────────────────

    private function extractCountry(array $address): ?string
    {
        return $address['country'] ?? null;
    }

    /**
     * Region = state / oblast / province / territory.
     * Nominatim uses different keys for different countries, so we try several.
     */
    private function extractRegion(array $address): ?string
    {
        foreach (['state', 'province', 'region', 'territory', 'county_code'] as $key) {
            if (! empty($address[$key])) {
                return $address[$key];
            }
        }
        return null;
    }

    /**
     * District = county / city / municipality / district.
     */
    private function extractDistrict(array $address): ?string
    {
        foreach (['county', 'district', 'city_district', 'municipality', 'city', 'town', 'village'] as $key) {
            if (! empty($address[$key])) {
                return $address[$key];
            }
        }
        return null;
    }

    /** @return array{country: null, region: null, district: null} */
    private function emptyResult(): array
    {
        return ['country' => null, 'region' => null, 'district' => null];
    }
}
