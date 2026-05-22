<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('detected_objects', function (Blueprint $table) {
            $table->id();
            $table->foreignId('analysis_id')->constrained()->onDelete('cascade');
            $table->foreignId('analysis_tile_id')->constrained('analysis_tiles')->onDelete('cascade');

            // When the object was detected
            $table->timestamp('detected_at');

            // Geographic centre of the polygon (WGS-84)
            $table->decimal('center_lat', 10, 7);
            $table->decimal('center_lon', 10, 7);

            // Polygon vertices as [[lat, lon], ...] (WGS-84)
            $table->json('polygon_points');

            $table->unsignedSmallInteger('class_id');
            $table->string('class_name', 64);
            $table->decimal('confidence', 5, 4)->nullable();

            $table->timestamps();

            $table->index(['analysis_id', 'class_id']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('detected_objects');
    }
};
