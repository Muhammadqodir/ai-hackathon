<?php

namespace Database\Seeders;

use Carbon\Carbon;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;

/**
 * Seeds the shafof_qurilish_data table from a JSON file produced by the Python
 * parser script.
 *
 * To regenerate the data file:
 *   cd ai-model-src
 *   python shafofqurilish.py --with-details --workers 10 --out-dir ./output
 *   cp output/combined.json ../falak-ai/database/data/shafof_qurilish_data.json
 */
class ShafofQurilishSeeder extends Seeder
{
    /** Rows per INSERT batch (balances memory vs. query overhead). */
    protected int $chunkSize = 500;

    /** Absolute path to the JSON data file. */
    protected string $dataPath;

    public function __construct()
    {
        $this->dataPath = database_path('data/shafof_qurilish_data.json');
    }

    public function run(): void
    {
        if (! file_exists($this->dataPath)) {
            $this->command->error("Data file not found: {$this->dataPath}");
            $this->command->warn('Generate it by running from the repo root:');
            $this->command->warn('  cd ai-model-src');
            $this->command->warn('  python shafofqurilish.py --with-details --workers 10 --out-dir ./output');
            $this->command->warn('  cp output/combined.json ../falak-ai/database/data/shafof_qurilish_data.json');

            return;
        }

        $raw = json_decode(file_get_contents($this->dataPath), true);

        if (! is_array($raw) || empty($raw)) {
            $this->command->error("Failed to parse JSON from {$this->dataPath}");

            return;
        }

        $total = count($raw);
        $now   = now()->toDateTimeString();

        $this->command->info("Seeding {$total} records into shafof_qurilish_data …");

        // Columns updated on duplicate object_id (everything except the PK
        // surrogate and the original creation timestamp).
        $updateColumns = [
            'object_status', 'name', 'task_id', 'sphere_id',
            'location_building', 'difficulty', 'organization_name',
            'loyiha', 'pudrat', 'status_id', 'status_name',
            'lat', 'lon', 'region_soato', 'district_soato',
            'deadline', 'closed_at', 'source_created_at',
            'number_protocol', 'reestr_number', 'rating',
            'block_count', 'apartment_count', 'blocks', 'conclusion_url',
            'fetched_at', 'updated_at',
        ];

        $processed = 0;

        foreach (array_chunk($raw, $this->chunkSize) as $chunk) {
            $rows = array_map(fn (array $item) => $this->mapRow($item, $now), $chunk);

            DB::table('shafof_qurilish_data')->upsert($rows, ['object_id'], $updateColumns);

            $processed += count($chunk);
            $this->command->line("  {$processed} / {$total}");
        }

        $this->command->info("Done. {$total} records seeded.");
    }

    // -------------------------------------------------------------------------

    private function mapRow(array $item, string $now): array
    {
        return [
            // --- Identifiers -------------------------------------------------
            'object_id'         => (int) $item['object_id'],
            'object_status'     => isset($item['object_status']) ? (int) $item['object_status'] : null,

            // --- Names & organisations ---------------------------------------
            'name'              => $item['name'] ?? null,
            'task_id'           => isset($item['task_id']) ? (int) $item['task_id'] : null,
            'sphere_id'         => isset($item['sphere_id']) ? (int) $item['sphere_id'] : null,
            'location_building' => $this->str($item['location_building'] ?? null),
            'difficulty'        => $item['difficulty'] ?? null,
            'organization_name' => $this->str($item['organization_name'] ?? null),
            'loyiha'            => $this->str($item['loyiha'] ?? null),
            'pudrat'            => $this->str($item['pudrat'] ?? null),

            // --- Status ------------------------------------------------------
            'status_id'         => isset($item['status_id']) ? (int) $item['status_id'] : null,
            'status_name'       => $item['status_name'] ?? null,

            // --- Geography ---------------------------------------------------
            'lat'               => $item['lat'] ?? null,
            'lon'               => $item['lon'] ?? null,
            'region_soato'      => isset($item['region_soato']) ? (int) $item['region_soato'] : null,
            'district_soato'    => isset($item['district_soato']) ? (int) $item['district_soato'] : null,

            // --- Dates -------------------------------------------------------
            'deadline'          => $item['deadline'] ?? null, // DATE string or null
            'closed_at'         => $this->toDatetime($item['closed_at'] ?? null),
            'source_created_at' => $this->toDatetime($item['source_created_at'] ?? null),

            // --- Misc --------------------------------------------------------
            'number_protocol'   => $this->str($item['number_protocol'] ?? null),
            'reestr_number'     => $item['reestr_number'] ?? null,

            // Already a serialised JSON string (or null) from the Python script
            'rating'            => $item['rating'] ?? null,

            // --- Block / apartment stats ------------------------------------
            'block_count'       => (int) ($item['block_count'] ?? 0),
            'apartment_count'   => (int) ($item['apartment_count'] ?? 0),

            // Already a serialised JSON string from the Python script
            'blocks'            => $item['blocks'] ?? null,

            'conclusion_url'    => $item['conclusion_url'] ?? null,

            // --- Timestamps --------------------------------------------------
            'fetched_at'        => $now,
            'created_at'        => $now,
            'updated_at'        => $now,
        ];
    }

    /** Normalise empty strings to null. */
    private function str(?string $value): ?string
    {
        if ($value === null || trim($value) === '') {
            return null;
        }

        return $value;
    }

    /** Parse an ISO-8601 / datetime string to MySQL DATETIME format. */
    private function toDatetime(?string $value): ?string
    {
        if (! $value) {
            return null;
        }

        try {
            return Carbon::parse($value)->format('Y-m-d H:i:s');
        } catch (\Throwable) {
            return null;
        }
    }
}
