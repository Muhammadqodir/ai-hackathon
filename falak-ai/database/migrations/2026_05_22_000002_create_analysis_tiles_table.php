<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('analysis_tiles', function (Blueprint $table) {
            $table->id();
            $table->foreignId('analysis_id')->constrained()->onDelete('cascade');

            $table->integer('tile_x');
            $table->integer('tile_y');
            $table->smallInteger('tile_z');
            $table->string('provider', 16);

            // "{provider}_{z}_{x}_{y}" – used to identify cross-analysis cache hits
            $table->string('cache_key', 64)->index();

            $table->json('detections');                 // raw YOLO detection array
            $table->json('bbox');                       // {north,south,east,west} in WGS-84
            $table->unsignedSmallInteger('detection_count')->default(0);
            $table->boolean('from_cache')->default(false);

            $table->timestamps();

            $table->unique(['analysis_id', 'tile_x', 'tile_y', 'tile_z']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('analysis_tiles');
    }
};
