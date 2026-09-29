<?php
// SPDX-FileCopyrightText: 2026 NXLX and contributors
// SPDX-License-Identifier: Apache-2.0
// Shared request guards for the legacy control panel endpoints.
// Kept compatible with PHP 5.6 (Raspbian Jessie) up to current PHP 8.

function pvj_fail($code, $message) {
	http_response_code($code);
	header('Content-Type: text/plain; charset=utf-8');
	echo $message;
	exit;
}

// Only accept same-origin POSTs sent by our own JavaScript.
// The custom header cannot be set by a cross-site form or image tag, and a
// cross-origin script needs a CORS preflight, which we never grant. This
// blocks CSRF, including replays of state-changing actions on reconnect.
function pvj_require_post() {
	if (!isset($_SERVER['REQUEST_METHOD']) || $_SERVER['REQUEST_METHOD'] !== 'POST') {
		header('Allow: POST');
		pvj_fail(405, 'POST required');
	}
	if (!isset($_SERVER['HTTP_X_PVJ_REQUEST']) || $_SERVER['HTTP_X_PVJ_REQUEST'] !== '1') {
		pvj_fail(403, 'Missing request header');
	}
	if (!empty($_SERVER['HTTP_ORIGIN'])) {
		$host = parse_url($_SERVER['HTTP_ORIGIN'], PHP_URL_HOST);
		$port = parse_url($_SERVER['HTTP_ORIGIN'], PHP_URL_PORT);
		$origin = ($host === null ? '' : $host) . ($port ? ':' . $port : '');
		$self = isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : '';
		if (strcasecmp($origin, $self) !== 0) {
			pvj_fail(403, 'Cross-origin request refused');
		}
	}
}

// Read a POST value that must be an integer within [min, max].
function pvj_post_int($name, $min, $max) {
	$v = isset($_POST[$name]) ? $_POST[$name] : null;
	if (!is_string($v) || !preg_match('/^-?[0-9]{1,6}$/', $v) || (int)$v < $min || (int)$v > $max) {
		pvj_fail(400, 'Invalid ' . $name);
	}
	return (int)$v;
}

// Read a POST value that must be a decimal number within [min, max].
function pvj_post_float($name, $min, $max) {
	$v = isset($_POST[$name]) ? $_POST[$name] : null;
	if (!is_string($v) || !preg_match('/^-?[0-9]{1,3}(\.[0-9]{1,3})?$/', $v) || (float)$v < $min || (float)$v > $max) {
		pvj_fail(400, 'Invalid ' . $name);
	}
	return (float)$v;
}
