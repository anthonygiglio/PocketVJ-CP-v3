<?php
require_once __DIR__ . '/security.php';
pvj_require_post();

$speed = pvj_post_float('speedValue', 0, 4);
$speed = rtrim(rtrim(sprintf('%.3f', $speed), '0'), '.');
shell_exec('sudo /var/www/sync/dbuscontrol.sh rate ' . escapeshellarg($speed));
header('Content-Type: text/plain; charset=utf-8');
echo $speed;
