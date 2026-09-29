<?php
require_once __DIR__ . '/security.php';
pvj_require_post();

$alphavalue = pvj_post_int('opacityValue', 0, 255);
shell_exec('sudo /var/www/sync/dbuscontrol.sh setalpha ' . escapeshellarg((string)$alphavalue));
