<?php

return [
    /*
    |--------------------------------------------------------------------------
    | Python interpreter
    |--------------------------------------------------------------------------
    | Full path to the Python binary that has ultralytics, Pillow, requests,
    | opencv-python, and numpy installed.
    |
    | For the bundled venv:  ../label_env/bin/python
    | Absolute path example: /usr/bin/python3
    */
    'python_bin' => env('PYTHON_BIN', base_path('../label_env/bin/python')),

    /*
    |--------------------------------------------------------------------------
    | YOLO model path
    |--------------------------------------------------------------------------
    */
    'model_path' => env('YOLO_MODEL_PATH', base_path('../ai-model/best.pt')),

    /*
    |--------------------------------------------------------------------------
    | Tile + detection cache directory
    |--------------------------------------------------------------------------
    | Tiles are cached under {cache_dir}/tiles/{provider}/{z}/{x}/{y}.jpg
    | Detection results under {cache_dir}/detections/{provider}_{z}_{x}_{y}.json
    */
    'cache_dir' => env('TILE_CACHE_DIR', storage_path('app/tile_cache')),

    /*
    |--------------------------------------------------------------------------
    | Defaults
    |--------------------------------------------------------------------------
    */
    'default_zoom'     => (int) env('DEFAULT_ZOOM',     18),
    'default_provider' => env('DEFAULT_PROVIDER',       'google'),

    /*
    |--------------------------------------------------------------------------
    | Processing limits
    |--------------------------------------------------------------------------
    */
    'max_tiles' => (int) env('MAX_TILES',   6000),
    'workers'   => (int) env('TILE_WORKERS',  8),

    /*
    |--------------------------------------------------------------------------
    | YOLO inference settings
    |--------------------------------------------------------------------------
    */
    'conf_threshold' => (float) env('YOLO_CONF', 0.25),
    'iou_threshold'  => (float) env('YOLO_IOU',  0.45),
    'imgsz'          => (int)   env('YOLO_IMGSZ', 640),
];
