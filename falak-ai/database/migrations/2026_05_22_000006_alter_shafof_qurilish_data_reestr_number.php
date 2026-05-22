<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('shafof_qurilish_data', function (Blueprint $table) {
            $table->text('reestr_number')->nullable()->change();
        });
    }

    public function down(): void
    {
        Schema::table('shafof_qurilish_data', function (Blueprint $table) {
            // Truncate to fit VARCHAR(64) before reverting to avoid data loss errors
            \Illuminate\Support\Facades\DB::statement(
                "UPDATE shafof_qurilish_data SET reestr_number = LEFT(reestr_number, 64) WHERE LENGTH(reestr_number) > 64"
            );
            $table->string('reestr_number', 64)->nullable()->change();
        });
    }
};
