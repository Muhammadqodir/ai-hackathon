<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('shafof_qurilish_data', function (Blueprint $table) {
            $table->id();

            // ---- Core identifiers (from list-construction) ----------------
            $table->unsignedInteger('object_id')->unique();
            // Raw numeric status from the list endpoint (may differ from status_id)
            $table->unsignedTinyInteger('object_status')->nullable();

            // ---- Detail fields (from get-gasn-info) -----------------------
            $table->text('name')->nullable();
            $table->unsignedBigInteger('task_id')->nullable();
            $table->unsignedSmallInteger('sphere_id')->nullable();

            // Building address / location description
            $table->text('location_building')->nullable();

            // Complexity class: I, II, III, IV
            $table->char('difficulty', 3)->nullable();

            // Investor / developer organisation
            $table->text('organization_name')->nullable();
            // Design organisation (loyiha = project)
            $table->text('loyiha')->nullable();
            // General contractor (pudrat = contractor)
            $table->text('pudrat')->nullable();

            // Status from detail endpoint
            $table->unsignedTinyInteger('status_id')->nullable();
            $table->string('status_name', 64)->nullable();

            // ---- Geography ------------------------------------------------
            // NOTE: `long` is a reserved word in MySQL – using `lon` instead
            $table->decimal('lat', 15, 10)->nullable();
            $table->decimal('lon', 15, 10)->nullable();

            // SOATO (national statistical classification) codes
            $table->unsignedInteger('region_soato')->nullable();
            $table->unsignedInteger('district_soato')->nullable();

            // ---- Dates ----------------------------------------------------
            $table->date('deadline')->nullable();
            $table->timestamp('closed_at')->nullable();
            // Original creation timestamp in the source system
            $table->timestamp('source_created_at')->nullable();

            // ---- Construction details -------------------------------------
            $table->text('number_protocol')->nullable();
            $table->string('reestr_number', 64)->nullable();

            // Rating is returned as a JSON string by the API
            // e.g. [{"loyiha": {"inn": "...", "reyting_loyha": "CC"}, "qurilish": {...}}]
            $table->json('rating')->nullable();

            $table->unsignedSmallInteger('block_count')->default(0);
            $table->unsignedSmallInteger('apartment_count')->default(0);

            // Full blocks array: [{id, name, apartment_count, accepted, area, floor}]
            $table->json('blocks')->nullable();

            // URL of the official conclusion (ekspertiza) PDF
            $table->text('conclusion_url')->nullable();

            // ---- Metadata -------------------------------------------------
            // When our scraper fetched this record
            $table->timestamp('fetched_at')->nullable();
            $table->timestamps();

            // ---- Indexes --------------------------------------------------
            $table->index('status_id');
            $table->index('sphere_id');
            $table->index('region_soato');
            $table->index('district_soato');
            $table->index(['lat', 'lon'], 'idx_lat_lon');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('shafof_qurilish_data');
    }
};
