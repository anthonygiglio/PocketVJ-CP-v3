// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
(function () {
  'use strict';

  var RANK = { view: 1, live: 2, full: 3 };
  var ACCENTS = ['#f59e0b', '#c2410c', '#22d3ee', '#e879f9', '#a3e635', '#ffffff'];
  var S = {
    tab: 'live', device: null, status: null, banks: [], bank: 0, media: [], modules: [], theme: null,
    themes: [], devices: [], editing: false, sheet: null, msg: '', msgErr: false, token: null, failures: 0
  };
  var app = document.getElementById('app');
  var offlineBanner = document.getElementById('offline');

  // ---- helpers --------------------------------------------------------
  function h(tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v === null || v === undefined) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    var add = function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) return kid.forEach(add);
      el.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    };
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return el;
  }
  function can(role) { return !!S.device && RANK[S.device.role] >= RANK[role]; }
  function base(path) { return (path || '').split('/').pop(); }
  function clock(sec) {
    if (typeof sec !== 'number' || sec < 0) return '--:--';
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function throttle(fn, ms) {
    var last = 0, timer = null, args;
    return function () {
      args = arguments;
      var wait = ms - (Date.now() - last);
      if (wait <= 0) { last = Date.now(); fn.apply(null, args); }
      else if (!timer) timer = setTimeout(function () { timer = null; last = Date.now(); fn.apply(null, args); }, wait);
    };
  }

  function api(method, path, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (method === 'POST') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-PVJ-Request'] = '1';
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        S.failures = 0;
        offlineBanner.hidden = true;
        if (r.status === 401 && S.device) { S.device = null; render(); }
        return { ok: r.ok, status: r.status, data: data };
      });
    }, function () {
      S.failures++;
      if (S.failures >= 2) offlineBanner.hidden = false;
      return { ok: false, status: 0, data: { error: 'no connection' } };
    });
  }
  function say(text, isErr) { S.msg = text || ''; S.msgErr = !!isErr; var m = document.getElementById('msg'); if (m) { m.textContent = S.msg; m.className = 'msg' + (isErr ? ' err' : ''); } }
  function act(method, path, body, after) {
    return api(method, path, body).then(function (r) {
      if (!r.ok) say(r.data.error || 'Something went wrong', true);
      else if (after) after(r.data);
      return r;
    });
  }

  // ---- data loading ---------------------------------------------------
  function loadAll() {
    return Promise.all([
      api('GET', '/api/status'), api('GET', '/api/pads'), api('GET', '/api/media'),
      api('GET', '/api/modules'), api('GET', '/api/theme'), can('full') ? api('GET', '/api/devices') : null
    ]).then(function (r) {
      if (r[0].ok) { S.status = r[0].data; S.device = r[0].data.device; }
      if (r[1].ok) S.banks = r[1].data.banks;
      if (r[2].ok) { S.media = r[2].data.files; S.mediaInfo = r[2].data; }
      if (r[3].ok) S.modules = r[3].data.modules;
      if (r[4].ok) { S.theme = r[4].data.theme; S.themes = r[4].data.available; }
      if (r[5] && r[5].ok) S.devices = r[5].data.devices;
    });
  }
  function poll() {
    if (!S.device || document.visibilityState === 'hidden') return;
    api('GET', '/api/status').then(function (r) {
      if (r.ok) { S.status = r.data; patchLive(); }
    });
  }

  // ---- connect --------------------------------------------------------
  function connect() {
    var pins = [0, 1, 2, 3].map(function (i) {
      return h('input', { class: 'pin', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 1, 'aria-label': 'PIN digit ' + (i + 1), pattern: '[0-9]' });
    });
    function pinValue() { return pins.map(function (p) { return p.value; }).join(''); }
    pins.forEach(function (p, i) {
      p.addEventListener('input', function () {
        p.value = p.value.replace(/\D/g, '').slice(0, 1);
        if (p.value && pins[i + 1]) pins[i + 1].focus();
      });
      p.addEventListener('keydown', function (e) { if (e.key === 'Backspace' && !p.value && pins[i - 1]) pins[i - 1].focus(); });
      p.addEventListener('paste', function (e) {
        var text = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 4);
        if (!text) return;
        e.preventDefault();
        text.split('').forEach(function (c, j) { pins[j].value = c; });
      });
    });
    var sc = S.scanned;
    if (sc && sc.kind === 'pin') sc.value.split('').forEach(function (c, j) { if (pins[j]) pins[j].value = c; });
    var code = h('input', { class: 'text-input mono', id: 'joincode', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6,
      'aria-label': 'Guest or presenter code (6 digits)', placeholder: '6 digit code', value: sc && sc.kind === 'code' ? sc.value : '' });
    code.addEventListener('input', function () { code.value = code.value.replace(/\D/g, '').slice(0, 6); });
    var name = h('input', { class: 'text-input', value: sc && sc.kind === 'code' ? 'Phone' : 'My phone', 'aria-label': 'Name for this device', maxlength: 40 });
    function join(secret, button) {
      button.disabled = true;
      api('POST', '/api/pair', { pin: secret, name: name.value || 'device' }).then(function (r) {
        button.disabled = false;
        if (!r.ok) {
          var wait = r.data.retry_after ? ' Try again in ' + r.data.retry_after + ' seconds.' : '';
          var why = r.status === 403 ? (secret.length === 6 ? 'That code is wrong or has expired. Ask for a new one.' : 'Wrong PIN.') : (r.data.error || 'Could not connect.');
          return say(why + wait, true);
        }
        S.scanned = null;
        S.device = r.data.device;
        S.token = r.data.token;
        start();
      });
    }
    var pinButton = h('button', { class: 'btn' + (sc ? '' : ' on') + ' big', id: 'pairbtn', text: 'Pair with PIN' });
    pinButton.addEventListener('click', function () {
      var pin = pinValue();
      if (pin.length !== 4) return say('Enter the 4 digit PIN shown on the box.', true);
      join(pin, pinButton);
    });
    var codeButton = h('button', { class: 'btn' + (sc && sc.kind === 'code' ? ' on' : '') + ' big', id: 'joinbtn', text: 'Join with code' });
    codeButton.addEventListener('click', function () {
      if (code.value.length !== 6) return say('Enter the 6 digit code shown on the screen.', true);
      join(code.value, codeButton);
    });
    var scannedNote = sc && sc.kind === 'code' ? h('div', { class: 'msg', id: 'scannednote', text: 'Code read from the QR code. Tap Join.' }) : null;
    return h('div', { class: 'shell' },
      h('div', { class: 'screen' },
        h('h1', { text: 'Connect to your box' }),
        h('p', { text: 'Scan the QR code on the screen, or type the code shown there. The box\'s owner can use the 4 digit PIN instead. No internet needed.' }),
        scannedNote,
        h('div', { class: 'k', text: 'Guest or presenter code' }), code,
        h('label', { class: 'k', for: 'devname', text: 'Name for this device' }),
        (name.id = 'devname', name),
        codeButton,
        h('div', { class: 'k', text: 'Owner: PIN' }),
        h('div', { class: 'pin-row' }, pins),
        pinButton,
        h('div', { id: 'msg', class: 'msg', role: 'status' }),
        h('div', { class: 'grow' }),
        h('div', { class: 'k', text: 'Connection lost? The box keeps playing. Reconnect any time.' })));
  }
  // ---- live -----------------------------------------------------------
  function padButton(bank, index, pad) {
    var playing = S.status && S.status.player && S.status.player.path && pad.file && base(S.status.player.path) === pad.file;
    var label = pad.label || (pad.file ? pad.file.replace(/\.[^.]+$/, '') : 'Empty');
    var b = h('button', { class: 'pad' + (playing ? ' on' : '') + (pad.file ? '' : ' empty'), 'aria-pressed': playing ? 'true' : 'false', 'data-pad': index },
      h('span', { class: 'n', text: (index + 1 < 10 ? '0' : '') + (index + 1) }), h('span', { class: 't', text: label }));
    b.addEventListener('click', function () {
      if (S.editing && can('full')) return openSheet(bank, index);
      if (!pad.file) return say(can('full') ? 'Empty pad. Tap "Edit pads" to assign a clip.' : 'Empty pad.', true);
      if (!can('live')) return say('This device is view only.', true);
      act('POST', '/api/play', { pad: [bank, index] }, function () { say(''); poll(); });
    });
    return b;
  }
  function live() {
    var st = S.status || {};
    var pl = st.player || {};
    var bank = S.banks[S.bank];
    var pads = h('div', { class: 'pads', id: 'pads' });
    (bank ? bank.pads : []).forEach(function (p, i) { pads.appendChild(padButton(S.bank, i, p)); });
    var canLive = can('live');
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'nxlx.mastercontrol' }), h('div', { class: 'pill k', id: 'pill' })),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Now playing' }),
        h('div', { id: 'np', style: false, text: '' }),
        seekBar(canLive),
        h('div', { class: 'row between' }, h('div', { class: 'k', id: 'time' }), h('div', { class: 'k', id: 'plpos' })),
        h('div', { class: 'row transport' },
          h('button', { class: 'btn small grow', id: 'prev', text: '\u23ee Prev', 'aria-label': 'Previous clip', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'prev' }, poll); } }),
          h('button', { class: 'btn small grow', id: 'back10', text: '\u2212 10 s', 'aria-label': 'Back 10 seconds', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'seek', value: -10 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'fwd10', text: '+ 10 s', 'aria-label': 'Forward 10 seconds', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'seek', value: 10 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'next', text: 'Next \u23ed', 'aria-label': 'Next clip', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'next' }, poll); } })),
        h('div', { class: 'row transport' },
          h('button', { class: 'btn small grow', id: 'fadein', text: 'Fade in', disabled: !canLive, onclick: function () { act('POST', '/api/fadein', { seconds: 2 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'testpattern', text: 'Test pattern', disabled: !canLive, onclick: function () {
            var on = !(S.status && S.status.player && S.status.player.test_pattern);
            act('POST', '/api/testpattern', { on: on }, poll);
          } }))),
      previewBlock(),
      h('div', { class: 'banks' }, S.banks.map(function (b, i) {
        return h('button', { class: 'btn' + (i === S.bank ? ' on' : ''), text: b.name.replace('Bank ', 'Bank '), 'aria-pressed': i === S.bank ? 'true' : 'false',
          onclick: function () { S.bank = i; render(); } });
      })),
      pads,
      can('full') ? h('button', { class: 'btn small', text: S.editing ? 'Done editing' : 'Edit pads', onclick: function () { S.editing = !S.editing; render(); } }) : null,
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      h('div', { class: 'grow' }),
      h('div', { class: 'row' },
        h('button', { class: 'btn big grow', id: 'fade', text: 'Fade out', disabled: !canLive, onclick: function () { act('POST', '/api/fadeout', { seconds: 2 }); } }),
        h('button', { class: 'btn big grow', id: 'freeze', text: 'Freeze', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'pause' }, poll); } }),
        h('button', { class: 'btn big grow', id: 'stop', text: 'Stop', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'stop' }, poll); } }),
        h('button', { class: 'btn big grow', id: 'black', text: 'Blackout', disabled: !canLive, onclick: function () {
          var on = !(S.status && S.status.mix && S.status.mix.blackout);
          act('POST', '/api/blackout', { on: on }, poll);
        } })));
  }
  // ---- screen snapshot ----------------------------------------------------
  // One picture of what the box is showing, on request. Not a live view: on a Pi 4 each snapshot stalls playback
  // for about a quarter of a second (measured: a continuous preview dropped 4.7 frames a second, one every five
  // seconds still dropped 1.3), so nothing here repeats by itself.
  function previewBlock() {
    var img = h('img', { id: 'preview', alt: 'What the screen was showing', hidden: true });
    var note = h('div', { class: 'k', id: 'previewmsg', hidden: true });
    var btn = h('button', { class: 'btn small', id: 'previewbtn', text: 'Take snapshot' });
    function done() { btn.disabled = false; }
    img.addEventListener('load', function () { note.hidden = true; img.hidden = false; done(); });
    img.addEventListener('error', function () { img.hidden = true; note.hidden = false; note.textContent = 'No picture: the player may be idle or not running.'; done(); });
    btn.addEventListener('click', function () {
      btn.disabled = true; note.hidden = false; note.textContent = 'Taking a snapshot...';
      img.src = '/api/preview.jpg?t=' + Date.now();
    });
    return h('div', { class: 'card', id: 'previewcard' },
      h('div', { class: 'row between' }, h('div', { class: 'k', text: 'Screen' }), btn),
      h('div', { class: 'k', text: 'A snapshot briefly stalls playback, so it only happens when you tap.' }),
      img, note);
  }
  // Position: a slider that follows the clip, and jumps where it is released. While a finger is on it, the
  // once-a-second status update must not move it.
  var seeking = false;
  function seekBar(canLive) {
    var bar = h('input', { type: 'range', id: 'seek', class: 'seek', min: 0, max: 1000, step: 1, value: 0, 'aria-label': 'Position in the clip', disabled: !canLive });
    bar.addEventListener('pointerdown', function () { seeking = true; });
    bar.addEventListener('input', function () {
      seeking = true;
      var pl = (S.status && S.status.player) || {};
      if (pl.duration > 0) document.getElementById('time').textContent = clock(pl.duration * bar.value / 1000) + ' / ' + clock(pl.duration);
    });
    bar.addEventListener('change', function () {
      var pl = (S.status && S.status.player) || {};
      if (pl.duration > 0) act('POST', '/api/control', { action: 'seek_to', value: Math.round(pl.duration * bar.value / 10) / 100 }, function () { seeking = false; poll(); });
      else seeking = false;
    });
    bar.addEventListener('pointerup', function () { setTimeout(function () { seeking = false; }, 1500); });
    return bar;
  }
  function patchLive() {
    var st = S.status || {}, pl = st.player || {}, sys = st.system || {};
    var np = document.getElementById('np');
    if (!np) return;
    np.textContent = pl.running && pl.path ? (pl.stream || base(pl.path)) : (pl.running ? 'Player idle' : 'Player not running');
    var seekEl = document.getElementById('seek');
    var frac = pl.duration > 0 && pl.position >= 0 ? Math.min(1, pl.position / pl.duration) : 0;
    if (seekEl && !seeking) { seekEl.value = Math.round(frac * 1000); seekEl.disabled = !can('live') || !(pl.duration > 0); }
    if (!seeking) document.getElementById('time').textContent = clock(pl.position) + ' / ' + clock(pl.duration);
    var plpos = document.getElementById('plpos');
    if (plpos) plpos.textContent = pl.playlist_count > 1 && pl.playlist_pos >= 0 ? 'Clip ' + (pl.playlist_pos + 1) + ' of ' + pl.playlist_count : '';
    ['prev', 'next'].forEach(function (id) { var el = document.getElementById(id); if (el) el.disabled = !can('live') || !(pl.playlist_count > 1); });
    var tp = document.getElementById('testpattern');
    if (tp) { tp.textContent = pl.test_pattern ? 'Test pattern off' : 'Test pattern'; tp.className = 'btn small grow' + (pl.test_pattern ? ' on' : ''); }
    if (pl.test_pattern) np.textContent = 'Test pattern (colour bars)';
    if (pl.test_tone) np.textContent = 'Test tone (' + pl.test_tone + ')';
    var temp = typeof sys.temp_c === 'number' ? Math.round(sys.temp_c) + '°C' : '';
    document.getElementById('pill').textContent = [sys.board, temp, pl.running ? 'OK' : 'No player'].filter(Boolean).join(' · ');
    var f = document.getElementById('freeze'); if (f) f.textContent = pl.paused ? 'Resume' : 'Freeze';
    var b = document.getElementById('black'); if (b) { var on = st.mix && st.mix.blackout; b.className = 'btn big grow' + (on ? ' solid' : ''); b.textContent = on ? 'Show' : 'Blackout'; }
    var pads = document.getElementById('pads');
    if (pads && S.banks[S.bank]) {
      S.banks[S.bank].pads.forEach(function (p, i) {
        var el = pads.children[i];
        if (!el) return;
        var playing = pl.path && p.file && base(pl.path) === p.file;
        el.classList.toggle('on', !!playing);
        el.setAttribute('aria-pressed', playing ? 'true' : 'false');
      });
    }
  }
  function openSheet(bank, index) { S.sheet = { bank: bank, index: index }; render(); }
  var ENDINGS = [['loop', 'Loop'], ['stop', 'Play once, then black'], ['hold', 'Play once, hold the last frame']];
  function sheet() {
    var s = S.sheet;
    var close = function () { S.sheet = null; render(); };
    var current = (S.banks[s.bank] && S.banks[s.bank].pads[s.index]) || {};
    var ending = h('select', { class: 'text-input', id: 'padending', 'aria-label': 'When the clip ends' },
      ENDINGS.map(function (e) { return h('option', { value: e[0], text: e[1], selected: e[0] === (current.ending || 'loop') }); }));
    var pick = function (file) {
      var label = file ? file.replace(/\.[^.]+$/, '').slice(0, 40) : '';
      act('POST', '/api/pads', { bank: s.bank, index: s.index, label: label, file: file, ending: ending.value }, function (d) { S.banks = d.banks; close(); });
    };
    return h('div', { class: 'picker', onclick: function (e) { if (e.target.className === 'picker') close(); } },
      h('div', { class: 'sheet', role: 'dialog', 'aria-label': 'Choose a clip for this pad' },
        h('h2', { text: 'Pad ' + (s.index + 1) }),
        h('label', { class: 'k', for: 'padending', text: 'When the clip ends' }), ending,
        h('div', { class: 'list' }, S.media.length ? S.media.map(function (f) {
          return h('button', { class: 'btn', text: f, onclick: function () { pick(f); } });
        }) : h('div', { class: 'k', text: 'No clips in the media folder yet.' })),
        h('button', { class: 'btn', text: 'Clear pad', onclick: function () { pick(''); } }),
        h('button', { class: 'btn', text: 'Cancel', onclick: close })));
  }

  // ---- mix ------------------------------------------------------------
  function slider(id, label, min, max, step, value, fmt, send) {
    var out = h('span', { class: 'k', text: fmt(value) });
    var input = h('input', { id: id, type: 'range', min: min, max: max, step: step, value: value, disabled: !can('live') });
    var sendSoon = throttle(send, 80);
    input.addEventListener('input', function () { out.textContent = fmt(+input.value); sendSoon(+input.value); });
    input.addEventListener('change', function () { send(+input.value); });
    return h('div', { class: 'card slider' }, h('label', { for: id }, label, out), input);
  }
  function mix() {
    var m = (S.status && S.status.mix) || {}, pl = (S.status && S.status.player) || {};
    var ctl = function (action) { return function (value) { act('POST', '/api/control', { action: action, value: value }); }; };
    var choice = function (opts, current, onpick) {
      return h('div', { class: 'row' }, opts.map(function (o) {
        return h('button', { class: 'btn grow' + (o.value === current ? ' on' : ''), text: o.label, disabled: !can('live') || o.disabled,
          onclick: function () { onpick(o.value); } });
      }));
    };
    var setMix = function (patch) {
      var next = { transition: m.transition, duration: m.duration };
      Object.keys(patch).forEach(function (k) { next[k] = patch[k]; });
      act('POST', '/api/mix', next, function () { poll(); setTimeout(render, 150); });
    };
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Mix' })),
      h('div', { class: 'grid2' },
        slider('mo', 'Opacity', 0, 100, 1, m.opacity === undefined ? 100 : m.opacity, function (v) { return v + '%'; }, ctl('opacity')),
        slider('ms', 'Size', 1, 200, 1, m.size === undefined ? 100 : m.size, function (v) { return v + '%'; }, ctl('size')),
        slider('mp', 'Position X', -100, 100, 1, m.position === undefined ? 0 : m.position, function (v) { return String(v); }, ctl('position')),
        slider('mpy', 'Position Y', -100, 100, 1, m.position_y === undefined ? 0 : m.position_y, function (v) { return String(v); }, ctl('position_y')),
        slider('mv', 'Speed', 25, 200, 5, Math.round((pl.speed || 1) * 100), function (v) { return (v / 100).toFixed(2) + 'x'; },
          function (v) { ctl('speed')(v / 100); }),
        slider('mvol', 'Volume', 0, 130, 1, Math.round(pl.volume === undefined || pl.volume === null ? 100 : pl.volume), function (v) { return v + '%'; }, ctl('volume'))),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Transition between clips' }),
        choice([{ label: 'Cut', value: 'cut' }, { label: 'Dip to black', value: 'dip' }, { label: 'Crossfade (soon)', value: 'x', disabled: true }],
          m.transition, function (v) { setMix({ transition: v }); }),
        h('div', { class: 'k', text: 'Duration' }),
        choice([0.5, 1, 2, 5].map(function (d) { return { label: d + 's', value: d }; }), m.duration, function (v) { setMix({ duration: v }); })),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Mirror (for rear projection or a mirror rig; costs the box some work)' }),
        h('div', { class: 'row' },
          h('button', { class: 'btn grow' + (m.flip_h ? ' on' : ''), id: 'fliph', text: 'Flip left-right', 'aria-pressed': m.flip_h ? 'true' : 'false', disabled: !can('live'),
            onclick: function () { act('POST', '/api/control', { action: 'flip_h', value: !m.flip_h }, function () { poll(); setTimeout(render, 200); }); } }),
          h('button', { class: 'btn grow' + (m.flip_v ? ' on' : ''), id: 'flipv', text: 'Flip upside down', 'aria-pressed': m.flip_v ? 'true' : 'false', disabled: !can('live'),
            onclick: function () { act('POST', '/api/control', { action: 'flip_v', value: !m.flip_v }, function () { poll(); setTimeout(render, 200); }); } }))),
      overlayCard(),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Rotate' }),
        choice([0, 90, 180, 270].map(function (d) { return { label: d + '°', value: d }; }), m.rotate === undefined ? 0 : m.rotate,
          function (v) { ctl('rotate')(v); setTimeout(function () { poll(); render(); }, 200); })),
      h('div', { class: 'row' },
        h('button', { class: 'btn grow', text: 'Loop: ' + (pl.loop_file && pl.loop_file !== 'no' || pl.loop_playlist && pl.loop_playlist !== 'no' ? 'on' : 'off'), disabled: !can('live'),
          onclick: function () { var on = !(pl.loop_file && pl.loop_file !== 'no' || pl.loop_playlist && pl.loop_playlist !== 'no'); act('POST', '/api/control', { action: 'loop', value: on }, function () { poll(); setTimeout(render, 200); }); } }),
        h('button', { class: 'btn grow', text: 'Audio: ' + (pl.muted ? 'mute' : 'on'), disabled: !can('live'),
          onclick: function () { act('POST', '/api/control', { action: 'mute', value: !pl.muted }, function () { poll(); setTimeout(render, 200); }); } }),
        h('button', { class: 'btn grow', text: 'Reset mix', disabled: !can('live'),
          onclick: function () { act('POST', '/api/control', { action: 'reset' }, function () { poll(); setTimeout(render, 200); }); } })),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }));
  }

  // A picture over the video: a PNG from the media folder (logo, watermark, mask), fitted to the screen.
  function overlayCard() {
    var body = h('div', { class: 'list', id: 'overlaybody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'overlaycard' }, h('div', { class: 'k', text: 'Overlay picture (logo or mask over the video)' }), body);
    function draw(d) {
      body.textContent = '';
      if (!d.choices.length) { body.appendChild(h('div', { class: 'k', id: 'overlaynone', text: 'Upload a PNG (transparent where the video should show) on the Media screen to use it here.' })); return; }
      var sel = h('select', { class: 'text-input', id: 'overlayfile', 'aria-label': 'Overlay picture', disabled: !can('live') },
        d.choices.map(function (n) { return h('option', { value: n, text: n, selected: n === d.file }); }));
      body.appendChild(sel);
      body.appendChild(h('button', { class: 'btn' + (d.on ? ' on' : ''), id: 'overlaytoggle', 'aria-pressed': d.on ? 'true' : 'false', disabled: !can('live'),
        text: d.on ? 'Overlay is on. Turn off' : 'Show overlay',
        onclick: function (e) {
          e.target.disabled = true; e.target.textContent = d.on ? 'Turning off...' : 'Preparing the picture...';
          act('POST', '/api/overlay', { file: sel.value, on: !d.on }, function (data) { say(''); draw(data); }).then(function (r) { if (!r.ok) draw(d); });
        } }));
    }
    api('GET', '/api/overlay').then(function (r) {
      if (!document.getElementById('overlaycard')) return;
      if (r.ok) draw(r.data); else { body.textContent = ''; body.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' })); }
    });
    return card;
  }

  // ---- media ----------------------------------------------------------
  function describeClip(name, i) {
    var parts = [];
    if (i.codec) parts.push(i.codec.toUpperCase() + (i.width ? ' ' + i.width + 'x' + i.height : '') + (i.fps ? ' ' + i.fps + ' fps' : ''));
    else parts.push('no picture');
    parts.push(i.audio ? 'sound: ' + i.audio : 'no sound');
    if (i.duration) parts.push(clock(i.duration));
    if (i.container) parts.push(i.container.split(',')[0]);
    return name + ': ' + parts.join(' \u00b7 ');
  }
  function megabytes(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : (n / 1048576).toFixed(1) + ' MB'; }
  function refreshMedia() {
    return api('GET', '/api/media').then(function (r) {
      if (!r.ok) return;
      S.media = r.data.files; S.mediaInfo = r.data;
      if (!S.uploading) render();  // a redraw would wipe the progress bars of uploads still running
    });
  }
  // Results are kept so the redraw after the last upload does not wipe an error message.
  function note(text) { S.uploadNotes = (S.uploadNotes || []).concat(text).slice(-8); }
  function uploadFile(file, bar, label) {
    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media/upload?name=' + encodeURIComponent(file.name));
      xhr.setRequestHeader('X-PVJ-Request', '1');
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = function (e) { if (e.lengthComputable) bar.style.width = Math.round(100 * e.loaded / e.total) + '%'; };
      xhr.onload = function () {
        var reply = {};
        try { reply = JSON.parse(xhr.responseText); } catch (e) { /* keep empty */ }
        label.textContent = file.name + (xhr.status === 200 ? ': done' : ': ' + (reply.error || 'failed (' + xhr.status + ')'));
        note(label.textContent);
        if (xhr.status === 200) bar.style.width = '100%';
        resolve(xhr.status === 200);
      };
      xhr.onerror = function () { label.textContent = file.name + ': connection lost'; note(label.textContent); resolve(false); };
      xhr.send(file);
    });
  }
  function media() {
    var info = S.mediaInfo || {};
    var full = can('full');
    var details = info.details || S.media.map(function (n) { return { name: n, size: 0 }; });
    var uploads = h('div', { class: 'list', id: 'uploads' });
    (S.uploadNotes || []).forEach(function (t) { uploads.appendChild(h('div', { class: 'item' }, h('div', { class: 'k', text: t }))); });
    var picker = h('input', { type: 'file', id: 'filepick', multiple: true, hidden: true, 'aria-label': 'Choose video or image files',
      accept: 'video/*,image/*,audio/*,.mkv,.mov,.mp4,.avi,.webm,.m4v,.mpg,.mpeg,.ts,.wmv,.mp3,.wav,.flac,.ogg,.m4a,.aac,.opus' });
    picker.addEventListener('change', function () {
      var files = Array.prototype.slice.call(picker.files);
      picker.value = '';
      S.uploading = (S.uploading || 0) + files.length;
      files.reduce(function (chain, file) {
        var bar = h('div', {}); var label = h('div', { class: 'k', text: file.name + ' (' + megabytes(file.size) + ')' });
        uploads.appendChild(h('div', { class: 'item' }, h('div', { class: 'grow' }, label, h('div', { class: 'progress' }, bar))));
        return chain.then(function () { return uploadFile(file, bar, label); }).then(function () { S.uploading -= 1; });
      }, Promise.resolve()).then(refreshMedia);
    });
    var items = details.map(function (d) {
      return h('div', { class: 'item' },
        h('span', {}, d.name, h('br'), h('span', { class: 'k', text: d.size ? megabytes(d.size) : '' })),
        h('span', { class: 'row' },
          h('button', { class: 'btn small', text: 'Play', disabled: !can('live'), onclick: function () { act('POST', '/api/play', { file: d.name }, function () { say('Playing ' + d.name); poll(); }); } }),
          h('button', { class: 'btn small', text: 'Info', 'aria-label': 'Details of ' + d.name, onclick: function () {
            say('Reading ' + d.name + '...');
            act('POST', '/api/media/info', { name: d.name }, function (i) { say(describeClip(d.name, i)); });
          } }),
          full ? h('button', { class: 'btn small', text: 'Rename', onclick: function () {
            var to = window.prompt('New name', d.name);
            if (to && to !== d.name) act('POST', '/api/media/rename', { name: d.name, new: to }, refreshMedia);
          } }) : null,
          full ? h('button', { class: 'btn small', text: 'Delete', onclick: function () {
            if (window.confirm('Delete ' + d.name + '?')) act('POST', '/api/media/delete', { name: d.name }, refreshMedia);
          } }) : null));
    });
    // Quick play (the old Video tab): everything in the folder, or the clips whose names start with a number
    // ("01_intro.mp4" is clip 01), looping or once. Uses the same presets as OSC and autostart.
    var numbers = [];
    S.media.forEach(function (n) { var m = /^([0-9]{2})/.exec(n); if (m && numbers.indexOf(m[1]) < 0) numbers.push(m[1]); });
    numbers.sort();
    var preset = function (name, label) { act('POST', '/api/play', { preset: name }, function (d) { say('Playing ' + label + ' (' + d.files + ' clip' + (d.files === 1 ? '' : 's') + ')'); poll(); }); };
    var numSel = numbers.length ? h('select', { class: 'text-input', id: 'numsel', 'aria-label': 'Clip number' }, numbers.map(function (n) { return h('option', { value: n, text: n + '_' }); })) : null;
    var quick = can('live') && S.media.length ? h('div', { class: 'card', id: 'quickplay' },
      h('div', { class: 'k', text: 'Play the whole folder' }),
      h('div', { class: 'row' },
        h('button', { class: 'btn small grow', id: 'playall', text: 'Play all, loop', onclick: function () { preset('startless', 'all clips'); } }),
        h('button', { class: 'btn small grow', id: 'playallonce', text: 'Play all once', onclick: function () { preset('startlessonce', 'all clips once'); } }),
        h('button', { class: 'btn small grow', id: 'shuffleall', text: 'Shuffle all', onclick: function () {
          act('POST', '/api/play', { preset: 'startless', shuffle: true }, function (d) { say('Playing all clips in a random order (' + d.files + ')'); poll(); });
        } })),
      numbers.length ? h('div', { class: 'k', text: 'Play by number (files named 01_..., 02_...)' }) : null,
      numbers.length ? h('div', { class: 'row' }, numSel,
        h('button', { class: 'btn small grow', id: 'playnum', text: 'Loop', onclick: function () { preset('startless' + numSel.value, numSel.value + '_'); } }),
        h('button', { class: 'btn small grow', id: 'playnumonce', text: 'Once', onclick: function () { preset('startlessonce' + numSel.value, numSel.value + '_ once'); } })) : null) : null;
    // Slideshow (the old Presenter tab): the pictures of the media folder or of a USB drive, one after another.
    var imagesHere = S.media.filter(function (n) { return /\.(png|jpe?g|bmp|gif)$/i.test(n); }).length;
    var drives = (info.usb || []).filter(function (d) { return d.files.some(function (f) { return /\.(png|jpe?g|bmp|gif)$/i.test(f.name); }); });
    var slideshow = null;
    if (can('live') && (imagesHere || drives.length)) {
      var src = h('select', { class: 'text-input', id: 'slidesrc', 'aria-label': 'Pictures from' },
        (imagesHere ? [h('option', { value: 'media', text: 'Media folder (' + imagesHere + ' pictures)' })] : []).concat(
          drives.map(function (d) { return h('option', { value: d.drive, text: 'USB drive ' + d.drive }); })));
      var secs = h('select', { class: 'text-input', id: 'slidesecs', 'aria-label': 'Each picture for' },
        [[0.1, 'fastest (0.1 s)'], [1, '1 second'], [2, '2 seconds'], [5, '5 seconds'], [10, '10 seconds'], [15, '15 seconds'], [30, '30 seconds'], [60, '1 minute']].map(function (o) {
          return h('option', { value: o[0], text: 'Each picture for ' + o[1], selected: o[0] === 5 });
        }));
      var end = h('select', { class: 'text-input', id: 'slideend', 'aria-label': 'After the last picture' },
        [['loop', 'Then start again'], ['hold', 'Then keep the last picture'], ['stop', 'Then black']].map(function (o) { return h('option', { value: o[0], text: o[1] }); }));
      var mix = h('input', { type: 'checkbox', id: 'slideshuffle' });
      slideshow = h('div', { class: 'card', id: 'slideshow' },
        h('div', { class: 'k', text: 'Slideshow' }), src, secs, end,
        h('label', { class: 'row', for: 'slideshuffle' }, mix, h('span', { text: 'Random order' })),
        h('button', { class: 'btn on small', id: 'slidestart', text: 'Start slideshow', onclick: function () {
          act('POST', '/api/play', { slideshow: { source: src.value, seconds: parseFloat(secs.value), ending: end.value, shuffle: mix.checked } }, function (d) {
            say('Slideshow: ' + d.images + ' pictures'); poll();
          });
        } }),
        h('div', { class: 'k', text: 'Prev and Next on the Live screen step through the pictures.' }));
    }
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Media' }), h('button', { class: 'btn small', text: 'Refresh', onclick: refreshMedia })),
      quick,
      slideshow,
      full ? h('div', { class: 'card' },
        h('div', { class: 'k', id: 'freeline', text: (info.free !== undefined ? megabytes(info.free) + ' free' : '') + (info.max_upload ? ' \u00b7 largest file ' + megabytes(info.max_upload) : '') }),
        picker, h('button', { class: 'btn on', id: 'uploadbtn', text: 'Upload clips', onclick: function () { picker.click(); } }), uploads) : null,
      h('div', { class: 'card' }, h('div', { class: 'list' }, items.length ? items : h('div', { class: 'k', text: 'No clips yet. Upload some, or play them straight from a USB drive.' }))),
      (info.usb || []).map(function (drive) {
        return h('div', { class: 'card usb-drive', 'data-drive': drive.drive },
          h('div', { class: 'k', text: 'USB drive: ' + drive.drive + ' (read only, plays straight from the drive)' }),
          h('div', { class: 'list' }, drive.files.length ? drive.files.map(function (f) {
            return h('div', { class: 'item' },
              h('span', {}, f.name, h('br'), h('span', { class: 'k', text: megabytes(f.size) })),
              h('button', { class: 'btn small', text: 'Play', 'aria-label': 'Play ' + f.name + ' from USB', disabled: !can('live'),
                onclick: function () { act('POST', '/api/play', { usb: drive.drive + '/' + f.name }, function () { say('Playing ' + f.name); poll(); }); } }));
          }) : h('div', { class: 'k', text: 'No video or image files at the top of this drive.' })));
      }),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }));
  }

  // ---- system ---------------------------------------------------------
  function system() {
    var st = S.status || {}, sys = st.system || {}, pl = st.player || {};
    var full = can('full');
    var vitals = h('div', { class: 'card' }, h('h2', { text: 'Vitals' }),
      kv('Board', sys.model || sys.board || '?'),
      kv('Temperature', typeof sys.temp_c === 'number' ? Math.round(sys.temp_c) + '°C' : 'n/a'),
      kv('Player', pl.running ? 'Running' : 'Not running'),
      kv('This device', S.device ? S.device.name + ' (' + S.device.role + ')' : ''));
    var cards = [vitals, boxCard()];
    cards.push(modulesCard(full), audioCard(full), autostartCard(full), streamsCard(full));
    if (full) cards.push(scheduleCard(), networkCard(), oscCard(), dmxCard(), midiCard(), appearanceCard(), accessCard(), h('div', { class: 'card' }, h('h2', { text: 'Player' }),
      h('button', { class: 'btn', text: 'Restart player now', onclick: function () { act('POST', '/api/player/restart', {}, function () { say('Player restarting. The service brings it straight back.'); }); } })));
    cards.push(h('button', { class: 'btn', text: 'Forget this device', onclick: function () {
      if (!S.device) return;
      if (full) return say('Full-access devices are removed from the list above.', true);
      S.device = null; render();
    } }));
    return h('div', { class: 'screen' }, h('div', { class: 'top' }, h('h1', { text: 'System' })),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      h('div', { class: 'grid2' }, cards));
  }
  function gb(n) { return (n / 1073741824).toFixed(1) + ' GB'; }
  // The old Settings and Display tabs' information buttons, on one card.
  function boxCard() {
    var body = h('div', { class: 'list', id: 'boxbody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'boxcard' }, h('h2', { text: 'Box' }), body);
    api('GET', '/api/system').then(function (r) {
      if (!document.getElementById('boxcard')) return;
      body.textContent = '';
      if (!r.ok) return body.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' }));
      var d = r.data;
      body.appendChild(kv('nxlx.mastercontrol', d.version));
      body.appendChild(kv('Player', d.mpv || '?'));
      body.appendChild(kv('System', d.os + ' \u00b7 ' + d.kernel));
      if (d.disk) {
        body.appendChild(kv('Media storage', gb(d.disk.free) + ' free of ' + gb(d.disk.total)));
        body.appendChild(h('div', { class: 'progress', 'aria-hidden': 'true' }, (function () { var b = h('div', {}); b.style.width = Math.round(100 * d.disk.used / d.disk.total) + '%'; return b; })()));
      }
      if (d.output) body.appendChild(kv('Output now', d.output.width + ' x ' + d.output.height + (d.output.refresh ? ' at ' + d.output.refresh + ' Hz' : '')));
      d.screens.forEach(function (sc) {
        body.appendChild(kv(sc.connector, sc.connected ? 'connected' : 'nothing plugged in'));
        if (sc.connected && sc.modes.length) body.appendChild(h('div', { class: 'k mono', text: 'Modes: ' + sc.modes.join(', ') }));
      });
    });
    return card;
  }
  function kv(k, v) { return h('div', { class: 'row between' }, h('span', { text: k }), h('span', { class: 'k', text: String(v) })); }
  function modulesCard(full) {
    return h('div', { class: 'card' }, h('h2', { text: 'Modules' }),
      h('div', { class: 'list' }, S.modules.map(function (m) {
        var note = m.status === 'planned' ? 'Not built yet' : (!m.supported ? 'Not on this board' : m.type === 'core' ? 'Core' : m.channel);
        var b = h('button', { class: 'btn small' + (m.enabled ? ' on' : ''), text: m.enabled ? 'On' : 'Off', 'aria-pressed': m.enabled ? 'true' : 'false',
          disabled: !full || m.locked || m.status !== 'ready' || !m.supported,
          onclick: function () { act('POST', '/api/modules/' + m.id, { enabled: !m.enabled }, function (d) { S.modules = d.modules; render(); }); } });
        return h('div', { class: 'item' }, h('span', {}, m.name, h('br'), h('span', { class: 'k', text: m.version + ' · ' + note })), b);
      })));
  }
  // ---- DMX and MIDI ---------------------------------------------------
  var dmxForm = { universe: null, start: null, allow: null };  // survive redraws
  function moduleOn(id) { var m = S.modules.filter(function (x) { return x.id === id; })[0]; return !!(m && m.enabled); }
  function dmxCard() {
    var card = h('div', { class: 'card', id: 'dmxcard' }, h('h2', { text: 'DMX (Art-Net, sACN)' }));
    var body = h('div', { class: 'list', id: 'dmxbody' });
    card.appendChild(body);
    if (!moduleOn('control-dmx')) {
      body.appendChild(h('div', { class: 'k', id: 'dmxmsg', text: 'Off. Switch on "DMX over the network" under Modules above (beta).' }));
      return card;
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'dmxline', text: d.error ? 'Problem: ' + d.error :
        (d.listening ? 'Listening on UDP ' + d.port + ' (' + d.received + ' frames for this universe)' : 'Off') }));
      if (d.channels) body.appendChild(h('div', { class: 'k mono', id: 'dmxlevels', text: 'Channels ' + d.start + '-' + (d.start + 7) + ': ' + d.channels.join(' ') }));
      var proto = h('select', { class: 'text-input', id: 'dmxproto', 'aria-label': 'Protocol' },
        [['artnet', 'Art-Net'], ['sacn', 'sACN (E1.31)']].map(function (p) { return h('option', { value: p[0], text: p[1], selected: p[0] === d.protocol }); }));
      var uni = h('input', { class: 'text-input mono', id: 'dmxuni', type: 'number', 'aria-label': 'Universe', value: dmxForm.universe === null ? d.universe : dmxForm.universe });
      var start = h('input', { class: 'text-input mono', id: 'dmxstart', type: 'number', min: 1, max: 505, 'aria-label': 'Start channel', value: dmxForm.start === null ? d.start : dmxForm.start });
      var allow = h('input', { class: 'text-input mono', id: 'dmxallow', 'aria-label': 'Extra allowed networks, comma separated', placeholder: 'Extra networks, e.g. 192.168.50.0/24',
        value: dmxForm.allow === null ? d.allow.join(', ') : dmxForm.allow });
      uni.addEventListener('input', function () { dmxForm.universe = uni.value; });
      start.addEventListener('input', function () { dmxForm.start = start.value; });
      allow.addEventListener('input', function () { dmxForm.allow = allow.value; });
      function send(patch) {
        act('POST', '/api/dmx', patch, function (data) { dmxForm = { universe: null, start: null, allow: null }; say(''); draw(data); });
      }
      function fields() {
        return { protocol: proto.value, universe: parseInt(uni.value, 10), start: parseInt(start.value, 10),
          allow: allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean) };
      }
      body.appendChild(h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'dmxtoggle', text: d.enabled ? 'DMX is on. Turn off' : 'Turn DMX on',
        onclick: function () { var f = fields(); f.enabled = !d.enabled; send(f); } }));
      body.appendChild(proto); body.appendChild(h('label', { class: 'k', for: 'dmxuni', text: 'Universe' })); body.appendChild(uni);
      body.appendChild(h('label', { class: 'k', for: 'dmxstart', text: 'Start channel (uses 8 channels)' })); body.appendChild(start); body.appendChild(allow);
      body.appendChild(h('button', { class: 'btn small', id: 'dmxsave', text: 'Save', onclick: function () { send(fields()); } }));
      body.appendChild(h('div', { class: 'k', text: 'Off until you turn it on. Only private networks may send. The first frame only sets a starting point, and the box holds its last state if the signal stops.' }));
    }
    api('GET', '/api/dmx').then(function (r) {
      if (!document.getElementById('dmxcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'dmxmsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  var MIDI_ACTIONS = [['pad', 'Play a pad'], ['stop', 'Stop'], ['pause', 'Pause / resume'], ['blackout', 'Blackout on / off'], ['fadeout', 'Fade out'],
    ['reset', 'Reset mix'], ['opacity', 'Opacity (fader)'], ['size', 'Size (fader)'], ['position', 'Position X (fader)'], ['speed', 'Speed (fader)'],
    ['volume', 'Volume (fader)'], ['blackout_hold', 'Blackout while held up (fader)']];
  var midiForm = { action: 'opacity', bank: 0, index: 0 };  // survives redraws
  var midiTimer = null;
  function midiCard() {
    var card = h('div', { class: 'card', id: 'midicard' }, h('h2', { text: 'MIDI controllers' }));
    var body = h('div', { class: 'list', id: 'midibody' });
    card.appendChild(body);
    if (!moduleOn('control-midi')) {
      body.appendChild(h('div', { class: 'k', id: 'midimsg', text: 'Off. Switch on "MIDI controller (USB)" under Modules above (beta).' }));
      return card;
    }
    function describe(e) {
      var what = MIDI_ACTIONS.filter(function (a) { return a[0] === e.action; })[0];
      var ctl = (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'CC ' : 'program ') + e.number + (e.channel ? ' ch ' + e.channel : '');
      var pad = e.action === 'pad' ? ' ' + 'ABC'[e.bank] + (e.index + 1) : '';
      return (e.source === '*' ? 'any controller' : e.source) + ' · ' + ctl + ' → ' + (what ? what[1] : e.action) + pad;
    }
    function poll() {
      clearTimeout(midiTimer);
      midiTimer = setTimeout(function () {
        if (!document.getElementById('midicard')) return;
        api('GET', '/api/midi').then(function (r) {
          if (!r.ok || !document.getElementById('midicard')) return;
          var l = r.data.learn;
          if (l.captured) return save(l.captured);
          if (l.active) {   // only the countdown changes: do not rebuild the card under the user's finger
            var el = document.getElementById('midilearning');
            if (el) el.textContent = 'Move or press a control on any controller now (' + l.seconds_left + ' s)...';
            return poll();
          }
          draw(r.data); say('Learning stopped: nothing was moved or pressed.', true);
        });
      }, 600);
    }
    function save(c) {
      var entry = { source: c.source, kind: c.kind, channel: 0, number: c.number, action: midiForm.action };
      if (midiForm.action === 'pad') { entry.bank = midiForm.bank; entry.index = midiForm.index; }
      act('POST', '/api/midi/map', { add: entry }, function (data) { say('Mapped: ' + describe(entry)); draw(data); });
    }
    function draw(d) {
      body.textContent = '';
      var names = d.devices.map(function (x) { return x.name + (x.connected ? '' : ' (not reading)'); });
      body.appendChild(h('div', { class: 'k', id: 'midiline', text: !d.enabled ? 'Off' : (d.devices.length ? d.devices.length + ' controller' + (d.devices.length > 1 ? 's' : '') + ': ' + names.join(', ') + (d.last ? '. Last: ' + d.last : '') : 'On, waiting for a controller to be plugged in') }));
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'miditoggle', text: d.enabled ? 'MIDI is on. Turn off' : 'Turn MIDI on',
          onclick: function () { act('POST', '/api/midi', { enabled: !d.enabled }, function (data) { say(''); draw(data); }); } }),
        h('button', { class: 'btn small' + (d.builtin ? ' on' : ''), id: 'midibuiltin', 'aria-pressed': d.builtin ? 'true' : 'false',
          text: 'Built-in map: ' + (d.builtin ? 'on' : 'off'),
          onclick: function () { act('POST', '/api/midi', { builtin: !d.builtin }, function (data) { draw(data); }); } })));
      body.appendChild(h('div', { class: 'k', text: 'Your own mappings win over the built-in map (notes 36 to 71 are pads, CC 20 to 25 are levels; see MIDI.md).' }));
      body.appendChild(h('div', { class: 'k', text: 'Mappings' }));
      if (!d.map.length) body.appendChild(h('div', { class: 'k', id: 'midinomap', text: 'None yet. Choose an action below, tap Learn, then move or press a control.' }));
      d.map.forEach(function (e) {
        body.appendChild(h('div', { class: 'item midi-entry' }, h('span', { text: describe(e) }),
          h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + describe(e), onclick: function () {
            act('POST', '/api/midi/map', { remove: e.id }, function (data) { draw(data); });
          } })));
      });
      if (d.map.length) body.appendChild(h('button', { class: 'btn small', id: 'midiclear', text: 'Remove all mappings', onclick: function () {
        if (window.confirm('Remove all mappings?')) act('POST', '/api/midi/map', { clear: true }, function (data) { draw(data); });
      } }));
      if (!d.enabled) return;
      var action = h('select', { class: 'text-input', id: 'midiaction', 'aria-label': 'Action to assign' },
        MIDI_ACTIONS.map(function (a) { return h('option', { value: a[0], text: a[1], selected: a[0] === midiForm.action }); }));
      var bank = h('select', { class: 'text-input', id: 'midibank', 'aria-label': 'Bank', hidden: midiForm.action !== 'pad' },
        ['A', 'B', 'C'].map(function (n, i) { return h('option', { value: i, text: 'Bank ' + n, selected: i === midiForm.bank }); }));
      var index = h('select', { class: 'text-input', id: 'midiindex', 'aria-label': 'Pad', hidden: midiForm.action !== 'pad' },
        Array.apply(null, Array(12)).map(function (_, i) { return h('option', { value: i, text: 'Pad ' + (i + 1), selected: i === midiForm.index }); }));
      function remember() { midiForm = { action: action.value, bank: parseInt(bank.value, 10), index: parseInt(index.value, 10) }; bank.hidden = index.hidden = action.value !== 'pad'; }
      [action, bank, index].forEach(function (el) { el.addEventListener('change', remember); });
      body.appendChild(h('div', { class: 'k', text: 'Add a mapping' }));
      body.appendChild(action); body.appendChild(bank); body.appendChild(index);
      if (d.learn.active) {
        body.appendChild(h('div', { class: 'msg', id: 'midilearning', role: 'status', text: 'Move or press a control on any controller now (' + d.learn.seconds_left + ' s)...' }));
        body.appendChild(h('button', { class: 'btn small', id: 'midicancel', text: 'Cancel', onclick: function () {
          clearTimeout(midiTimer); act('POST', '/api/midi/learn', { start: false }, function (data) { draw(data); });
        } }));
        poll();
      } else {
        body.appendChild(h('button', { class: 'btn on small', id: 'midilearn', text: 'Learn a control', onclick: function () {
          remember();
          act('POST', '/api/midi/learn', { start: true }, function (data) { draw(data); });
        } }));
      }
    }
    api('GET', '/api/midi').then(function (r) {
      if (!document.getElementById('midicard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'midimsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- audio output -----------------------------------------------------
  function audioCard(full) {
    var body = h('div', { class: 'list', id: 'audiobody' });
    var card = h('div', { class: 'card', id: 'audiocard' }, h('h2', { text: 'Sound output' }), body);
    function label(d) { return d.description ? d.description + ' (' + d.name.replace(/^alsa\//, '') + ')' : d.name; }
    function draw(d) {
      body.textContent = '';
      var auto = d.devices.filter(function (x) { return x.name === d.automatic_is; })[0];
      var sel = h('select', { class: 'text-input', id: 'audiodev', 'aria-label': 'Sound output', disabled: !full },
        [h('option', { value: 'auto', text: 'Automatic: ' + (auto ? auto.description : 'the player\'s own choice'), selected: d.device === 'auto' })].concat(
          d.devices.filter(function (x) { return x.name !== 'auto'; }).map(function (x) { return h('option', { value: x.name, text: label(x), selected: x.name === d.device }); })));
      body.appendChild(h('div', { class: 'k', id: 'audioline', text: d.device === 'auto' ? 'Automatic: on a Pi this is the HDMI port with the screen on it.' : 'Fixed to the output below.' }));
      body.appendChild(sel);
      if (full) body.appendChild(h('button', { class: 'btn on small', id: 'audiosave', text: 'Save', onclick: function () {
        act('POST', '/api/audio', { device: sel.value }, function (data) { say(''); draw(data); });
      } }));
      if (can('live')) {
        body.appendChild(h('div', { class: 'k', text: 'Test tone (5 seconds, 440 Hz; stops what is playing)' }));
        body.appendChild(h('div', { class: 'row' }, [['left', 'Left'], ['both', 'Both'], ['right', 'Right']].map(function (c) {
          return h('button', { class: 'btn small grow', id: 'tone-' + c[0], text: c[1], onclick: function () { act('POST', '/api/testtone', { channel: c[0] }, function () { say('Playing a test tone: ' + c[1].toLowerCase()); poll(); }); } });
        })));
      }
    }
    api('GET', '/api/audio').then(function (r) {
      if (!document.getElementById('audiocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'audiomsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- autostart -------------------------------------------------------
  var autoForm = null;  // survives redraws: { mode, file, preset, loop, delay }
  function autostartCard(full) {
    var body = h('div', { class: 'list', id: 'autobody' });
    var card = h('div', { class: 'card', id: 'autocard' }, h('h2', { text: 'Autostart' }), body);
    var MODES = [['off', 'Off'], ['file', 'Play one clip'], ['all', 'Play every clip'], ['preset', 'Legacy start script']];
    function draw(d) {
      body.textContent = '';
      var c = autoForm || { mode: d.config.mode, file: d.config.file, preset: d.config.preset, loop: d.config.loop, delay: d.config.delay };
      var line = d.config.mode === 'off' ? 'Off: the box waits for you at power-up.' :
        'On: ' + MODES.filter(function (m) { return m[0] === d.config.mode; })[0][1] + (d.config.mode === 'file' ? ' (' + d.config.file + ')' : d.config.mode === 'preset' ? ' (' + d.config.preset + ')' : '') + ', after ' + d.config.delay + ' s.';
      body.appendChild(h('div', { class: 'k', id: 'autoline', text: line }));
      if (d.last) body.appendChild(h('div', { class: 'k', id: 'autolast', text: 'Last run ' + d.last.at + ': ' + (d.last.ok ? 'started' : 'failed, ' + d.last.message) }));
      if (!full) return;
      var mode = h('select', { class: 'text-input', id: 'automode', 'aria-label': 'What to play at power-up' },
        MODES.map(function (m) { return h('option', { value: m[0], text: m[1], selected: m[0] === c.mode }); }));
      var file = h('select', { class: 'text-input', id: 'autofile', 'aria-label': 'Clip', hidden: c.mode !== 'file' },
        S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === (c.file || S.media[0]) }); }));
      var preset = h('input', { class: 'text-input mono', id: 'autopreset', 'aria-label': 'Start script name', placeholder: 'startlessonce05', value: c.preset, hidden: c.mode !== 'preset', autocomplete: 'off' });
      var loop = h('select', { class: 'text-input', id: 'autoloop', 'aria-label': 'Loop', hidden: c.mode === 'off' || c.mode === 'preset' },
        [[true, 'Loop'], [false, 'Play once']].map(function (o) { return h('option', { value: String(o[0]), text: o[1], selected: o[0] === c.loop }); }));
      var delay = h('input', { class: 'text-input mono', id: 'autodelay', type: 'number', min: 0, max: 120, 'aria-label': 'Wait after power-up, seconds', value: c.delay, hidden: c.mode === 'off' });
      function remember() { autoForm = { mode: mode.value, file: file.value, preset: preset.value, loop: loop.value === 'true', delay: parseFloat(delay.value || '0') }; }
      function show() { file.hidden = mode.value !== 'file'; preset.hidden = mode.value !== 'preset'; loop.hidden = mode.value === 'off' || mode.value === 'preset'; delay.hidden = mode.value === 'off'; }
      [mode, file, preset, loop, delay].forEach(function (el) { el.addEventListener('input', remember); el.addEventListener('change', function () { remember(); show(); }); });
      body.appendChild(mode); body.appendChild(file); body.appendChild(preset); body.appendChild(loop);
      body.appendChild(h('label', { class: 'k', for: 'autodelay', text: 'Wait after power-up (seconds)', hidden: false })); body.appendChild(delay);
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on small', id: 'autosave', text: 'Save', onclick: function () {
          remember();
          act('POST', '/api/autostart', autoForm, function (data) { autoForm = null; say(''); draw(data); });
        } }),
        h('button', { class: 'btn small', id: 'autotest', text: 'Run it now', disabled: !can('live'), onclick: function () {
          act('POST', '/api/autostart/test', {}, function (data) { draw(data); });
        } })));
      body.appendChild(h('div', { class: 'k', text: 'Runs when the box starts, and again if the player is restarted after a crash. A Stop from the panel is not undone.' }));
    }
    api('GET', '/api/autostart').then(function (r) {
      if (!document.getElementById('autocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'automsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- streams (SRT, RTSP, RTMP) --------------------------------------
  var streamForm = { name: '', url: '' };  // survives redraws
  function streamsCard(full) {
    var body = h('div', { class: 'list', id: 'streambody' });
    var card = h('div', { class: 'card', id: 'streamcard' }, h('h2', { text: 'Streams' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'inputs-srt'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'streammsg', text: 'Off. Switch on "Streams: SRT, RTSP, RTMP" under Modules above (beta).' }));
      return card;
    }
    function draw(d) {
      body.textContent = '';
      if (!d.streams.length) body.appendChild(h('div', { class: 'k', id: 'streamempty', text: 'No streams saved yet.' }));
      d.streams.forEach(function (st) {
        body.appendChild(h('div', { class: 'item stream-entry' },
          h('span', {}, st.name, h('br'), h('span', { class: 'addr', text: st.url })),
          h('span', { class: 'row' },
            h('button', { class: 'btn small', text: 'Play', 'aria-label': 'Play ' + st.name, disabled: !can('live'),
              onclick: function () { act('POST', '/api/play', { stream: st.id }, function () { say('Playing ' + st.name); poll(); }); } }),
            full ? h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + st.name, onclick: function () {
              act('POST', '/api/streams', { action: 'remove', id: st.id }, draw);
            } }) : null)));
      });
      if (!full) return;
      var name = h('input', { class: 'text-input', id: 'streamname', 'aria-label': 'Stream name', placeholder: 'Name', maxlength: 40, value: streamForm.name });
      var url = h('input', { class: 'text-input mono', id: 'streamurl', 'aria-label': 'Stream address', placeholder: 'srt://192.168.1.20:9000', value: streamForm.url, autocomplete: 'off' });
      name.addEventListener('input', function () { streamForm.name = name.value; });
      url.addEventListener('input', function () { streamForm.url = url.value; });
      body.appendChild(h('div', { class: 'k', text: 'Add a stream: ' + d.schemes.join(', ') + '. A login inside the address is stored on the box and hidden here.' }));
      body.appendChild(name); body.appendChild(url);
      body.appendChild(h('button', { class: 'btn on small', id: 'streamadd', text: 'Save stream', onclick: function () {
        act('POST', '/api/streams', { action: 'add', name: streamForm.name, url: streamForm.url }, function (data) {
          streamForm.name = ''; streamForm.url = ''; say(''); draw(data);
        });
      } }));
    }
    api('GET', '/api/streams').then(function (r) {
      if (!document.getElementById('streamcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'streammsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- schedule -------------------------------------------------------
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var schedForm = { time: '18:00', days: [0, 1, 2, 3, 4, 5, 6], action: 'play', file: '', label: '' };  // survives redraws
  function scheduleCard() {
    var body = h('div', { class: 'list', id: 'schedbody' });
    var card = h('div', { class: 'card', id: 'schedcard' }, h('h2', { text: 'Schedule' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'scheduler'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'schedmsg', text: 'Off. Switch on "Weekly schedule" under Modules above (beta).' }));
      return card;
    }
    function save(cfg, done) {
      api('POST', '/api/schedule', { enabled: cfg.enabled, entries: cfg.entries }).then(function (r) {
        if (!r.ok) return say(r.data.error || 'Could not save the schedule', true);
        say(''); draw(r.data); if (done) done();
      });
    }
    function describe(e) {
      var what = e.action === 'play' ? 'Play ' + e.file : e.action === 'stop' ? 'Stop' : e.action === 'blackout' ? 'Blackout' : 'Show screen';
      return e.time + ' · ' + e.days.map(function (d) { return DAYS[d]; }).join(' ') + ' · ' + what;
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'schedclock', text: 'Box clock: ' + d.now + ' (' + d.timezone + '). Times use this clock; check it before a show.' }));
      body.appendChild(h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'schedtoggle', 'aria-pressed': d.enabled ? 'true' : 'false',
        text: d.enabled ? 'Schedule is on. Turn off' : 'Turn schedule on',
        onclick: function () { save({ enabled: !d.enabled, entries: d.entries }); } }));
      if (!d.entries.length) body.appendChild(h('div', { class: 'k', id: 'schedempty', text: 'No entries yet.' }));
      d.entries.forEach(function (e) {
        var last = d.last[e.id];
        body.appendChild(h('div', { class: 'item sched-entry' },
          h('span', {}, (e.label ? e.label + ': ' : '') + describe(e),
            last ? h('br') : null, last ? h('span', { class: 'k', text: 'Last run ' + last.at + (last.ok ? '' : ' failed: ' + last.message) }) : null),
          h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + describe(e), onclick: function () {
            save({ enabled: d.enabled, entries: d.entries.filter(function (x) { return x.id !== e.id; }) });
          } })));
      });
      var time = h('input', { class: 'text-input mono', id: 'schedtime', type: 'time', 'aria-label': 'Time', value: schedForm.time });
      time.addEventListener('input', function () { schedForm.time = time.value; });
      var days = h('div', { class: 'row wrap', id: 'scheddays' }, DAYS.map(function (name, i) {
        var on = schedForm.days.indexOf(i) >= 0;
        return h('button', { class: 'btn small' + (on ? ' on' : ''), text: name, 'aria-pressed': on ? 'true' : 'false', onclick: function () {
          var at = schedForm.days.indexOf(i);
          if (at >= 0) schedForm.days.splice(at, 1); else schedForm.days.push(i);
          draw(d);
        } });
      }));
      var action = h('select', { class: 'text-input', id: 'schedaction', 'aria-label': 'What to do' },
        [['play', 'Play a clip'], ['stop', 'Stop the clip'], ['blackout', 'Blackout'], ['show', 'Show screen']].map(function (a) {
          return h('option', { value: a[0], text: a[1], selected: a[0] === schedForm.action });
        }));
      var file = h('select', { class: 'text-input', id: 'schedfile', 'aria-label': 'Clip to play', hidden: schedForm.action !== 'play' },
        S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === schedForm.file }); }));
      if (!schedForm.file && S.media.length) schedForm.file = S.media[0];
      action.addEventListener('change', function () { schedForm.action = action.value; file.hidden = action.value !== 'play'; });
      file.addEventListener('change', function () { schedForm.file = file.value; });
      var label = h('input', { class: 'text-input', id: 'schedlabel', 'aria-label': 'Label (optional)', placeholder: 'Label (optional)', maxlength: 40, value: schedForm.label });
      label.addEventListener('input', function () { schedForm.label = label.value; });
      body.appendChild(h('div', { class: 'k', text: 'Add an entry' }));
      body.appendChild(time); body.appendChild(days); body.appendChild(action); body.appendChild(file); body.appendChild(label);
      body.appendChild(h('button', { class: 'btn on small', id: 'schedadd', text: 'Add entry', onclick: function () {
        if (!schedForm.days.length) return say('Choose at least one day.', true);
        var entry = { time: schedForm.time, days: schedForm.days.slice(), action: schedForm.action, label: schedForm.label };
        if (schedForm.action === 'play') { if (!schedForm.file) return say('Upload a clip first.', true); entry.file = schedForm.file; }
        save({ enabled: d.enabled, entries: d.entries.concat(entry) });
      } }));
    }
    api('GET', '/api/schedule').then(function (r) {
      if (!document.getElementById('schedcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'schedmsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- network (wired) ------------------------------------------------
  var netTimer = null;
  var netForm = { mode: 'dhcp', vals: {} };  // survives redraws of the System screen, so typing is never wiped
  var NET_FIELDS = ['netaddr', 'netprefix', 'netgw', 'netdns'];
  // Copy what is on screen into netForm just before anything is rebuilt. Relying on each field's input event
  // alone lost a value on a slow runner; reading the page at the moment of the redraw cannot miss one.
  function keepNetForm() {
    NET_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el && typeof el.value === 'string') { if (el.value) netForm.vals[id] = el.value; else delete netForm.vals[id]; }
    });
  }
  function clearNetForm() {
    netForm.vals = {};
    NET_FIELDS.forEach(function (id) { var el = document.getElementById(id); if (el) el.value = ''; });
  }
  var NET_MODES = [
    ['dhcp', 'Automatic (DHCP)', 'Take an address from a router.'],
    ['static', 'Fixed address', 'You choose the address. Use a range your other gear is on.'],
    ['linklocal', 'Direct cable', 'Laptop plugged straight into the box; no router. The box uses a 169.254.x.x address.'],
    ['share', 'Serve addresses', 'The box hands out addresses to whatever is plugged in.']
  ];
  function networkCard() {
    var body = h('div', { class: 'list', id: 'netbody' });
    var card = h('div', { class: 'card', id: 'netcard' }, h('h2', { text: 'Network (wired)' }), body);
    var mode = netForm.mode;
    var out = { iface: null, address: null, prefix: null, gateway: null, dns: null, secs: null, preview: null, msg: null };
    var mod = S.modules.filter(function (m) { return m.id === 'network'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'netmsg', text: 'Off. Switch on "Network settings (wired)" under Modules above (beta). Needs NetworkManager.' }));
      return card;
    }

    function refresh() {
      clearTimeout(netTimer);
      api('GET', '/api/network').then(function (r) {
        if (!document.getElementById('netcard')) return;
        if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'netmsg', text: r.data.error || 'Not available' })); return; }
        draw(r.data);
      });
    }
    function value(el) { return el ? el.value.trim() : ''; }
    function config() {
      var c = { iface: value(out.iface), mode: mode, revert_seconds: parseInt(value(out.secs) || '60', 10) };
      if (mode === 'static' || mode === 'share') {
        if (value(out.address)) c.address = value(out.address);
        if (value(out.prefix)) c.prefix = parseInt(value(out.prefix), 10);
      }
      if (mode === 'static') {
        if (value(out.gateway)) c.gateway = value(out.gateway);
        c.dns = value(out.dns).split(/[ ,]+/).filter(Boolean);
      }
      return c;
    }
    function draw(d) {
      keepNetForm();
      body.textContent = '';
      d.interfaces.forEach(function (i) {
        body.appendChild(h('div', { class: 'item' },
          h('span', {}, i.name, h('br'), h('span', { class: 'k', text: (i.kind === 'wired' ? 'wired' : 'wi-fi') + ' \u00b7 ' + (i.carrier ? 'connected' : 'no link') + (i.speed_mbps ? ' \u00b7 ' + i.speed_mbps + ' Mbit/s' : '') })),
          h('span', { class: 'mono', text: (i.addresses || []).join(', ') || '-' })));
      });
      if (!d.helper) body.appendChild(h('div', { class: 'k', id: 'netmsg', text: 'The network helper (pvj-netd) is not running: changes cannot be applied. You can still preview them.' }));
      if (d.reverting) { body.appendChild(h('div', { class: 'msg err', id: 'netreverting', role: 'alert', text: 'Restoring the previous network. If this page stops responding, reconnect to the box at its old address.' })); netTimer = setTimeout(refresh, 2000); }
      if (d.pending) return drawPending(d.pending);
      var wired = d.interfaces.filter(function (i) { return i.kind === 'wired'; });
      if (!wired.length) return body.appendChild(h('div', { class: 'k', text: 'No wired network port found.' }));
      out.iface = h('select', { class: 'text-input', id: 'netiface', 'aria-label': 'Network port' }, wired.map(function (i) { return h('option', { value: i.name, text: i.name }); }));
      var modes = h('div', { class: 'row wrap', id: 'netmodes' });
      var help = h('div', { class: 'k', id: 'nethelp' });
      var fields = h('div', { class: 'list', id: 'netfields' });
      function remember(el) {
        if (netForm.vals[el.id]) el.value = netForm.vals[el.id];
        el.addEventListener('input', function () { netForm.vals[el.id] = el.value; });
      }
      function drawFields() {
        fields.textContent = '';
        NET_MODES.forEach(function (m) { if (m[0] === mode) help.textContent = m[2]; });
        if (mode === 'static' || mode === 'share') {
          out.address = h('input', { class: 'text-input mono', id: 'netaddr', 'aria-label': 'Address', placeholder: mode === 'share' ? '10.42.0.1' : '192.168.1.50', inputmode: 'decimal' });
          out.prefix = h('input', { class: 'text-input mono', id: 'netprefix', 'aria-label': 'Prefix length', placeholder: '24 (means 255.255.255.0)', inputmode: 'numeric' });
          remember(out.address); remember(out.prefix);
          fields.appendChild(out.address); fields.appendChild(out.prefix);
        }
        if (mode === 'static') {
          out.gateway = h('input', { class: 'text-input mono', id: 'netgw', 'aria-label': 'Gateway (optional)', placeholder: 'Gateway (optional)', inputmode: 'decimal' });
          out.dns = h('input', { class: 'text-input mono', id: 'netdns', 'aria-label': 'DNS servers (optional)', placeholder: 'DNS servers (optional)' });
          remember(out.gateway); remember(out.dns);
          fields.appendChild(out.gateway); fields.appendChild(out.dns);
        }
      }
      function drawModes() {
        modes.textContent = '';
        NET_MODES.forEach(function (m) {
          modes.appendChild(h('button', { class: 'btn small' + (m[0] === mode ? ' on' : ''), text: m[1], 'aria-pressed': m[0] === mode ? 'true' : 'false',
            onclick: function () { mode = netForm.mode = m[0]; drawModes(); drawFields(); } }));
        });
      }
      out.secs = h('select', { class: 'text-input', id: 'netsecs', 'aria-label': 'Revert automatically after' },
        [30, 60, 120, 300].map(function (n) { return h('option', { value: n, text: 'Revert after ' + n + ' s unless confirmed', selected: n === 60 }); }));
      out.preview = h('pre', { class: 'mono', id: 'netplan', hidden: true });
      out.msg = h('div', { class: 'msg', id: 'netresult', role: 'status' });
      drawModes(); drawFields();
      body.appendChild(out.iface); body.appendChild(modes); body.appendChild(help); body.appendChild(fields); body.appendChild(out.secs);
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn small', id: 'netpreview', text: 'Preview commands', onclick: function () {
          api('POST', '/api/network/plan', config()).then(function (r) {
            out.preview.hidden = !r.ok; out.msg.className = 'msg' + (r.ok ? '' : ' err');
            out.msg.textContent = r.ok ? '' : (r.data.error || 'Invalid');
            if (r.ok) out.preview.textContent = r.data.commands.join('\n');
          });
        } }),
        h('button', { class: 'btn on small', id: 'netapply', text: 'Apply', onclick: function () {
          var c = config();
          api('POST', '/api/network/apply', c).then(function (r) {
            if (!r.ok) { out.msg.className = 'msg err'; out.msg.textContent = r.data.error || 'Could not apply'; return; }
            var where = (c.mode === 'static' || c.mode === 'share') ? ' If this page stops responding, open http://' + (c.address || '10.42.0.1') + ' and press Confirm before the timer runs out.'
              : ' If this page stops responding, find the box at its new address and press Confirm before the timer runs out.';
            S.netNote = 'Applied.' + where;
            clearNetForm();
            refresh();
          });
        } })));
      body.appendChild(out.preview); body.appendChild(out.msg);
      body.appendChild(h('div', { class: 'k', text: 'A change can cut this connection. It goes back by itself unless you confirm it, and also if the box restarts before you do.' }));
    }
    function drawPending(p) {
      body.appendChild(h('div', { class: 'card', id: 'netpending', role: 'alert' },
        h('div', { text: 'Waiting for your confirmation: ' + p.iface + ' \u2192 ' + p.mode }),
        h('div', { class: 'k', id: 'netleft', text: 'Reverts in ' + p.seconds_left + ' s' }),
        h('div', { class: 'k', text: S.netNote || '' }),
        h('div', { class: 'row' },
          h('button', { class: 'btn on', id: 'netconfirm', text: 'Confirm: keep this network', onclick: function () {
            api('POST', '/api/network/confirm', {}).then(function (r) { S.netNote = r.ok ? '' : (r.data.error || ''); refresh(); });
          } }),
          h('button', { class: 'btn', id: 'netrevert', text: 'Revert now', onclick: function () {
            api('POST', '/api/network/revert', {}).then(function () { S.netNote = ''; refresh(); });
          } }))));
      netTimer = setTimeout(refresh, 1000);
    }
    refresh();
    return card;
  }
  function oscCard() {
    var line = h('div', { class: 'k', id: 'oscline', text: 'Loading...' });
    var port = h('input', { class: 'text-input mono', type: 'number', min: 1024, max: 65535, 'aria-label': 'OSC port' });
    var allow = h('input', { class: 'text-input mono', 'aria-label': 'Extra allowed networks, comma separated', placeholder: 'Extra networks, e.g. 192.168.50.0/24' });
    var toggle = h('button', { class: 'btn', id: 'osctoggle', text: '...' });
    var current = null;
    function show(d) {
      current = d;
      line.textContent = d.error ? 'Problem: ' + d.error : (d.listening ? 'Listening on UDP ' + d.port + ' (' + d.received + ' messages received)' : 'Off');
      toggle.textContent = d.enabled ? 'OSC is on. Turn off' : 'Turn OSC on';
      toggle.className = 'btn' + (d.enabled ? ' on' : '');
      port.value = d.port;
      allow.value = d.allow.join(', ');
    }
    function push(patch) {
      act('POST', '/api/osc', patch, function (d) { say(''); show(d); });
    }
    api('GET', '/api/osc').then(function (r) { if (r.ok) show(r.data); else line.textContent = 'Not available'; });
    toggle.addEventListener('click', function () { if (current) push({ enabled: !current.enabled }); });
    var save = h('button', { class: 'btn small', text: 'Save port and networks', onclick: function () {
      var nets = allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
      push({ port: parseInt(port.value, 10), allow: nets });
    } });
    return h('div', { class: 'card' }, h('h2', { text: 'Control (OSC)' }),
      h('div', { text: 'Off by default. Only private networks may send. Shutdown and reboot are never available over OSC.' }),
      line, toggle, h('label', { class: 'k', for: 'oscport', text: 'UDP port' }), (port.id = 'oscport', port), allow, save);
  }
  function appearanceCard() {
    var t = S.theme || {};
    var apply = function (name, accent) {
      act('POST', '/api/theme', { name: name, accent: accent }, function (d) {
        S.theme = d.theme;
        document.querySelector('link[href^="/theme.css"]').setAttribute('href', '/theme.css?v=' + Date.now());
        render();
      });
    };
    return h('div', { class: 'card' }, h('h2', { text: 'Appearance' }),
      h('div', { class: 'row wrap' }, S.themes.map(function (th) {
        return h('button', { class: 'btn small' + (th.id === t.name ? ' on' : ''), text: th.name, onclick: function () { apply(th.id, t.accent); } });
      })),
      h('div', { class: 'k', text: 'Accent' }),
      h('div', { class: 'swatches' },
        h('button', { class: 'btn small', text: 'Default', onclick: function () { apply(t.name, null); } }),
        ACCENTS.map(function (c) {
          var sw = h('button', { class: 'swatch' + (t.accent === c ? ' cur' : ''), 'aria-label': 'Accent ' + c, onclick: function () { apply(t.name, c); } });
          sw.style.background = c;
          return sw;
        })));
  }
  var accessForm = { pin: false, view: true, live: false, seconds: 300 };  // survives redraws
  var accessTimer = null;
  function accessCard() {
    var card = h('div', { class: 'card', id: 'accesscard' }, h('h2', { text: 'Access' }));
    var devices = h('div', { class: 'list' }, S.devices.map(function (d) {
      return h('div', { class: 'item' }, h('span', { text: d.name }), h('span', { class: 'row' }, h('span', { class: 'k', text: d.role }),
        h('button', { class: 'btn small', text: 'Remove', onclick: function () {
          act('POST', '/api/devices/revoke', { id: d.id }, function () { S.devices = S.devices.filter(function (x) { return x.id !== d.id; }); if (S.device && d.id === S.device.id) { S.device = null; } render(); });
        } })));
    }));
    var live = h('div', { class: 'list', id: 'accesslive' });
    function roleName(r) { return r === 'view' ? 'Guest (watch only)' : 'Presenter (play and mix)'; }
    function clock(sec) { var m = Math.floor(sec / 60), s2 = sec % 60; return m + ':' + (s2 < 10 ? '0' : '') + s2; }
    function drawLive(d) {
      live.textContent = '';
      var scr = d.screen;
      live.appendChild(h('div', { class: 'k', id: 'accessscreenline', text: scr.showing ? 'On the display now: ' + scr.items.map(function (i) { return i === 'pin' ? 'full access PIN' : roleName(i).toLowerCase(); }).join(', ') + ', hides in ' + clock(scr.seconds_left) : 'Nothing on the display.' }));
      var boxes = [['pin', 'Full access PIN (owner only)'], ['view', 'Guest code and QR (watch only)'], ['live', 'Presenter code and QR (play and mix)']].map(function (it) {
        var cb = h('input', { type: 'checkbox', id: 'show-' + it[0], checked: accessForm[it[0]] });
        cb.addEventListener('change', function () { accessForm[it[0]] = cb.checked; });
        return h('label', { class: 'row', for: 'show-' + it[0] }, cb, h('span', { text: it[1] }));
      });
      var secs = h('select', { class: 'text-input', id: 'showsecs', 'aria-label': 'Show for' },
        [[60, '1 minute'], [300, '5 minutes'], [900, '15 minutes'], [3600, '1 hour']].map(function (o) { return h('option', { value: o[0], text: 'Show for ' + o[1], selected: o[0] === accessForm.seconds }); }));
      secs.addEventListener('change', function () { accessForm.seconds = parseInt(secs.value, 10); });
      live.appendChild(h('div', { class: 'k', text: 'Show on the display' }));
      boxes.forEach(function (b) { live.appendChild(b); });
      live.appendChild(secs);
      live.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on small', id: 'showaccess', text: scr.showing ? 'Show again' : 'Show on display', onclick: function () {
          var items = ['pin', 'view', 'live'].filter(function (i) { return accessForm[i]; });
          if (!items.length) return say('Choose what to show.', true);
          if (accessForm.pin && !window.confirm('Anyone who can see the display will see the full access PIN. Show it?')) return;
          act('POST', '/api/access/screen', { show: true, items: items, seconds: accessForm.seconds }, function (data) { say(''); drawLive(data); });
        } }),
        h('button', { class: 'btn small', id: 'hideaccess', text: 'Hide from display', disabled: !scr.showing, onclick: function () {
          act('POST', '/api/access/screen', { show: false }, function (data) { drawLive(data); });
        } })));
      live.appendChild(h('div', { class: 'k', text: 'Join codes (6 digits; they expire and work a limited number of times)' }));
      if (!d.codes.length) live.appendChild(h('div', { class: 'k', id: 'nocodes', text: 'None active.' }));
      d.codes.forEach(function (c) {
        live.appendChild(h('div', { class: 'item join-code', 'data-role': c.role },
          h('span', {}, roleName(c.role), h('br'), h('span', { class: 'mono big-code', text: c.code }), h('br'),
            h('span', { class: 'k', text: 'expires in ' + clock(c.seconds_left) + ' · ' + c.uses_left + ' uses left' })),
          h('img', { class: 'qr', alt: 'QR code for the ' + roleName(c.role).toLowerCase() + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + Date.now() }),
          h('button', { class: 'btn small', text: 'Cancel', onclick: function () { act('POST', '/api/access/cancel', { code: c.code }, drawLive); } })));
      });
      live.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn small', id: 'newguest', text: 'New guest code', onclick: function () { act('POST', '/api/access/code', { role: 'view', minutes: 60 }, drawLive); } }),
        h('button', { class: 'btn small', id: 'newpresenter', text: 'New presenter code', onclick: function () { act('POST', '/api/access/code', { role: 'live', minutes: 60 }, drawLive); } }),
        h('button', { class: 'btn small', id: 'printsheet', text: 'Print access sheet', onclick: function () { printSheet(d); } })));
      clearTimeout(accessTimer);
      if (scr.showing || d.codes.length) accessTimer = setTimeout(function () { if (document.getElementById('accesscard')) refresh(); }, 5000);
    }
    function refresh() {
      // The check is after the answer arrives: on the first call the card is not on the page yet.
      api('GET', '/api/access').then(function (r) {
        if (!document.getElementById('accesscard')) return;
        if (r.ok) drawLive(r.data);
        else { live.textContent = ''; live.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' })); }
      });
    }
    var link = h('input', { class: 'text-input mono', readonly: true, 'aria-label': 'Guest link', hidden: true });
    var linkQr = h('img', { class: 'qr', id: 'linkqr', alt: 'QR code for the guest link', hidden: true });
    var role = h('select', { class: 'text-input', 'aria-label': 'Access level' }, h('option', { value: 'view', text: 'View only' }), h('option', { value: 'live', text: 'Live (play and mix)' }));
    var pinOut = h('div', { class: 'mono', id: 'pinout' });
    card.appendChild(h('div', { class: 'k', text: 'Paired devices' }));
    card.appendChild(devices);
    card.appendChild(live);
    card.appendChild(h('div', { class: 'k', text: 'Guest link that does not expire (until you remove it above)' }));
    card.appendChild(role);
    card.appendChild(h('button', { class: 'btn', text: 'Create guest link', onclick: function () {
      act('POST', '/api/devices/invite', { name: 'Guest (' + role.value + ')', role: role.value, origin: location.origin }, function (d) {
        link.value = location.origin + '/#token=' + d.token; link.hidden = false; link.select();
        if (d.qr_svg) { linkQr.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(d.qr_svg); linkQr.hidden = false; }
        api('GET', '/api/devices').then(function (r) { if (r.ok) S.devices = r.data.devices; });
      });
    } }));
    card.appendChild(link);
    card.appendChild(linkQr);
    card.appendChild(h('button', { class: 'btn', text: 'New PIN', onclick: function () { act('POST', '/api/pin/rotate', {}, function (d) { pinOut.textContent = 'New PIN: ' + d.pin; }); } }));
    card.appendChild(h('button', { class: 'btn', id: 'unlockpair', text: 'Unblock joining', onclick: function () {
      act('POST', '/api/pin/unlock', {}, function () { pinOut.textContent = 'Joining is open again (the PIN is unchanged).'; });
    } }));
    card.appendChild(pinOut);
    refresh();
    return card;
  }
  // A page to print and pin up in a studio: the panel address as a QR code (no access in it), plus the current guest
  // and presenter codes if any. Printed from the browser; nothing leaves the box.
  function printSheet(d) {
    var sheet = h('div', { id: 'printsheet' },
      h('h1', { text: 'nxlx.mastercontrol' }),
      h('p', { text: 'Scan with your phone camera to open the control panel. You need a code from the person running the show, or the PIN on the box.' }),
      h('div', { class: 'sheet-row' },
        h('figure', {}, h('img', { class: 'qr-big', alt: 'QR code for the control panel', src: '/api/qr.svg?for=panel' }), h('figcaption', { text: location.origin + '/' }))),
      d.codes.length ? h('div', { class: 'sheet-row' }, d.codes.map(function (c) {
        return h('figure', {}, h('img', { class: 'qr-big', alt: 'QR code for the ' + c.role + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + Date.now() }),
          h('figcaption', { text: (c.role === 'view' ? 'Guest (watch only)' : 'Presenter (play and mix)') + ': ' + c.code + ' (expires)' }));
      })) : null,
      h('p', { class: 'k', text: 'Codes shown here expire. The panel address above does not.' }));
    document.body.appendChild(sheet);
    document.body.classList.add('printing');
    var done = function () { document.body.classList.remove('printing'); if (sheet.parentNode) sheet.parentNode.removeChild(sheet); window.removeEventListener('afterprint', done); };
    window.addEventListener('afterprint', done);
    var imgs = sheet.querySelectorAll('img'), left = imgs.length;
    var go = function () { window.print(); setTimeout(done, 1000); };
    if (!left) return go();
    Array.prototype.forEach.call(imgs, function (im) { var fin = function () { if (--left === 0) go(); }; im.addEventListener('load', fin); im.addEventListener('error', fin); });
  }

  // ---- shell ----------------------------------------------------------
  function render() {
    clearTimeout(netTimer);
    clearTimeout(midiTimer);
    clearTimeout(accessTimer);
    keepNetForm();
    app.textContent = '';
    if (!S.device) { app.appendChild(connect()); return; }
    var screens = { live: live, mix: mix, media: media, system: system };
    var tabs = h('nav', { class: 'tabs', 'aria-label': 'Sections' }, [['live', 'Live'], ['mix', 'Mix'], ['media', 'Media'], ['system', 'System']].map(function (t) {
      return h('button', { class: 'btn' + (S.tab === t[0] ? ' on' : ''), text: t[1], 'aria-current': S.tab === t[0] ? 'page' : false,
        onclick: function () { S.tab = t[0]; S.msg = ''; loadAll().then(render); } });
    }));
    app.appendChild(h('div', { class: 'shell' }, screens[S.tab](), tabs));
    if (S.sheet) app.appendChild(sheet());
    patchLive();
  }
  function start() { loadAll().then(function () { if (S.device) render(); else render(); }); }

  // A guest link carries its token in the URL fragment, which is never sent to the server in a request line.
  function boot() {
    // A scanned QR code carries a join code or the box's PIN in the address fragment (never sent to any server);
    // keep it for the connect screen and take it out of the address bar and history at once.
    var scanned = /^#(code|pin)=([0-9]{4,6})$/.exec(location.hash);
    if (scanned) { S.scanned = { kind: scanned[1], value: scanned[2] }; history.replaceState(null, '', location.pathname); }
    var m = /^#token=([A-Za-z0-9_-]+)$/.exec(location.hash);
    var first = m ? api('POST', '/api/session', { token: m[1] }).then(function () { history.replaceState(null, '', location.pathname); }) : Promise.resolve();
    first.then(function () { return api('GET', '/api/status'); }).then(function (r) {
      if (r.ok) { S.device = r.data.device; S.status = r.data; return loadAll().then(render); }
      render();
    });
    setInterval(poll, 1000);
  }
  boot();
})();
