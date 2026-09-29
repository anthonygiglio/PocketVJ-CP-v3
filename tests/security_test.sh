#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
# Exercises the legacy PHP endpoints against php's built-in server with a
# stub `sudo` that records its arguments instead of running anything.
set -u
cd "$(dirname "$0")/.."
work=$(mktemp -d)
trap 'kill $srv 2>/dev/null; rm -rf "$work"' EXIT
cat > "$work/sudo" <<STUB
#!/bin/sh
printf '%s\n' "\$*" >> "$work/calls.log"
STUB
chmod +x "$work/sudo"
port=${PORT:-8765}
PATH="$work:$PATH" php -S 127.0.0.1:$port >/dev/null 2>&1 &
srv=$!
sleep 1
base=http://127.0.0.1:$port
fail=0
H='X-PVJ-Request: 1'

check() { # name expected_code actual_code
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (want $2 got $3)"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
: > "$work/calls.log"

check "opacity valid"            200 "$(code -H "$H" -d 'opacityValue=128' $base/submit_opacity.php)"
check "opacity injection"        400 "$(code -H "$H" --data-urlencode 'opacityValue=1; touch /tmp/pwn' $base/submit_opacity.php)"
check "opacity out of range"     400 "$(code -H "$H" -d 'opacityValue=999' $base/submit_opacity.php)"
check "size valid"               200 "$(code -H "$H" -d 'sizeValue=100' $base/submit_size.php)"
check "size injection"           400 "$(code -H "$H" --data-urlencode 'sizeValue=$(id)' $base/submit_size.php)"
check "xpos negative valid"      200 "$(code -H "$H" -d 'XpositionValue=-500' $base/submit_Xposition.php)"
check "xpos injection"           400 "$(code -H "$H" --data-urlencode 'XpositionValue=1 && reboot' $base/submit_Xposition.php)"
check "speed valid"              200 "$(code -H "$H" -d 'speedValue=1.5' $base/submit_speed.php)"
check "speed xss/injection"      400 "$(code -H "$H" --data-urlencode 'speedValue=<script>alert(1)</script>' $base/submit_speed.php)"
check "missing header refused"   403 "$(code -d 'opacityValue=1' $base/submit_opacity.php)"
check "GET refused"              405 "$(code $base/submit_opacity.php?opacityValue=1)"
check "cross-origin refused"     403 "$(code -H "$H" -H 'Origin: http://evil.example' -d 'opacityValue=1' $base/submit_opacity.php)"
check "same-origin accepted"     200 "$(code -H "$H" -H "Origin: http://127.0.0.1:$port" -d 'opacityValue=1' $base/submit_opacity.php)"
check "backend GET refused"      405 "$(code "$base/backend.php?action=reboot")"
check "backend POST no header"   403 "$(code -d 'action=reboot' $base/backend.php)"
check "backend POST ok"          200 "$(code -H "$H" -d 'action=pause' $base/backend.php)"
check "clock bad cookie ignored" 200 "$(code -b "usertime=2020-01-01'; reboot; '" $base/time_change.php)"
check "clock good cookie"        200 "$(code -b 'usertime=2026-09-29 12:00:00' $base/time_change.php)"

calls=$(cat "$work/calls.log")
for bad in pwn reboot 'id' '<script'; do
  if printf '%s' "$calls" | grep -q -- "$bad"; then
    if [ "$bad" = id ] && ! printf '%s' "$calls" | grep -q '\$(id)'; then continue; fi
    echo "FAIL hostile text reached sudo: $bad"; fail=1
  fi
done
printf '%s' "$calls" | grep -q "setalpha 128$" && echo "ok   opacity args passed as single argument" || { echo "FAIL opacity args"; fail=1; }
printf '%s' "$calls" | grep -q "date -s 2026-09-29 12:00:00" && echo "ok   clock args passed as single argument" || { echo "FAIL clock args"; fail=1; }
printf '%s' "$calls" | grep -q "rate 1.5$" && echo "ok   speed args passed as single argument" || { echo "FAIL speed args"; fail=1; }
exit $fail
