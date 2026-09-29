<?php
require_once __DIR__ . '/security.php';
pvj_require_post();

$Xposition = pvj_post_int('XpositionValue', -1000, 1000);
shell_exec('sudo /var/www/sync/omxXposition ' . escapeshellarg((string)$Xposition));
