<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('analyses', function (Blueprint $table) {
            $table->id();

            // Deduplication key: SHA-256 of "lat:lon:radius:zoom:provider"
            $table->string('cache_hash', 64)->unique()->index();

            $table->decimal('lat',    10, 7);
            $table->decimal('lon',    10, 7);
            $table->unsignedInteger('radius');          // metres
            $table->unsignedSmallInteger('zoom')->default(17);
            $table->string('provider', 16)->default('google');

            $table->enum('status', ['pending', 'processing', 'completed', 'failed'])
                  ->default('pending')
                  ->index();

            $table->unsignedInteger('total_tiles')->nullable();
            $table->unsignedInteger('processed_tiles')->default(0);
            $table->text('error_message')->nullable();
            $table->json('bbox')->nullable();           // geographic bounding box

            $table->timestamp('started_at')->nullable();
            $table->timestamp('completed_at')->nullable();
            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('analyses');
    }
};
