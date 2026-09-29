<script src="js/moment.js"></script>
<script type="text/javascript">

    actualtime = (moment().format("YYYY-MM-DD HH:mm:ss"));
    document.cookie = "usertime =" + actualtime ;

</script>

<?php
// The clock string comes from a cookie set by the script above. Only accept
// an exact "YYYY-MM-DD HH:MM:SS" timestamp; anything else is ignored.
$usertime = isset($_COOKIE['usertime']) ? trim($_COOKIE['usertime']) : '';

if (preg_match('/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$/', $usertime)) {
	shell_exec('sudo date -s ' . escapeshellarg($usertime));
	shell_exec('sudo hwclock -w');
	echo htmlspecialchars($usertime, ENT_QUOTES, 'UTF-8');
}
?>


<script type="text/javascript">

window.close();

</script>
