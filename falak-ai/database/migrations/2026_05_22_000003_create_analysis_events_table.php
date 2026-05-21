<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('analysis_events', function (Blueprint $table) {
            $table->id();
            $table->foreignId('analysis_id')->constrained()->onDelete('cascade');

            // start | status | tile_processed | complete | error
            $table->string('event_type', 32)->index();
            $table->json('payload');

            // Events are append-only; we only need created_at for ordering.
            $table->timestamp('created_at')->useCurrent();

            // Composite index for SSE cursor: "give me events > id for analysis X"
            $table->index(['analysis_id', 'id']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('analysis_events');
    }
};
