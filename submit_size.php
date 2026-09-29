<?php
require_once __DIR__ . '/security.php';
pvj_require_post();

$videosize = pvj_post_int('sizeValue', 0, 200);
shell_exec('sudo /var/www/sync/omxsizetocenter ' . escapeshellarg((string)$videosize));
