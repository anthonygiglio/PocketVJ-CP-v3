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
    var name = h('input', { class: 'text-input', value: 'My phone', 'aria-label': 'Name for this device', maxlength: 40 });
    var button = h('button', { class: 'btn on big', text: 'Pair this device' });
    button.addEventListener('click', function () {
      var pin = pinValue();
      if (pin.length !== 4) return say('Enter the 4 digit PIN shown on the box.', true);
      button.disabled = true;
      api('POST', '/api/pair', { pin: pin, name: name.value || 'device' }).then(function (r) {
        button.disabled = false;
        if (!r.ok) {
          var wait = r.data.retry_after ? ' Try again in ' + r.data.retry_after + ' seconds.' : '';
          return say((r.status === 403 ? 'Wrong PIN.' : (r.data.error || 'Could not pair.')) + wait, true);
        }
        S.device = r.data.device;
        S.token = r.data.token;
        start();
      });
    });
    return h('div', { class: 'shell' },
      h('div', { class: 'screen' },
        h('h1', { text: 'Connect to your box' }),
        h('p', { text: "Join the box's network, then enter the 4 digit PIN shown on the projector test screen or in the terminal. No internet needed." }),
        h('div', { class: 'k', text: 'PIN' }),
        h('div', { class: 'pin-row' }, pins),
        h('label', { class: 'k', for: 'devname', text: 'Device name' }),
        (name.id = 'devname', name),
        button,
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
        h('div', { class: 'progress', 'aria-hidden': 'true' }, h('div', { id: 'bar' })),
        h('div', { class: 'k', id: 'time' })),
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
        h('button', { class: 'btn big grow', id: 'black', text: 'Blackout', disabled: !canLive, onclick: function () {
          var on = !(S.status && S.status.mix && S.status.mix.blackout);
          act('POST', '/api/blackout', { on: on }, poll);
        } })));
  }
  function patchLive() {
    var st = S.status || {}, pl = st.player || {}, sys = st.system || {};
    var np = document.getElementById('np');
    if (!np) return;
    np.textContent = pl.running && pl.path ? (pl.stream || base(pl.path)) : (pl.running ? 'Player idle' : 'Player not running');
    var bar = document.getElementById('bar');
    var frac = pl.duration > 0 && pl.position >= 0 ? Math.min(1, pl.position / pl.duration) : 0;
    bar.style.width = Math.round(frac * 100) + '%';
    document.getElementById('time').textContent = clock(pl.position) + ' / ' + clock(pl.duration);
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
  function sheet() {
    var s = S.sheet;
    var close = function () { S.sheet = null; render(); };
    var pick = function (file) {
      var label = file ? file.replace(/\.[^.]+$/, '').slice(0, 40) : '';
      act('POST', '/api/pads', { bank: s.bank, index: s.index, label: label, file: file }, function (d) { S.banks = d.banks; close(); });
    };
    return h('div', { class: 'picker', onclick: function (e) { if (e.target.className === 'picker') close(); } },
      h('div', { class: 'sheet', role: 'dialog', 'aria-label': 'Choose a clip for this pad' },
        h('h2', { text: 'Pad ' + (s.index + 1) }),
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
        slider('mv', 'Speed', 25, 200, 5, Math.round((pl.speed || 1) * 100), function (v) { return (v / 100).toFixed(2) + 'x'; },
          function (v) { ctl('speed')(v / 100); })),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Transition between clips' }),
        choice([{ label: 'Cut', value: 'cut' }, { label: 'Dip to black', value: 'dip' }, { label: 'Crossfade (soon)', value: 'x', disabled: true }],
          m.transition, function (v) { setMix({ transition: v }); }),
        h('div', { class: 'k', text: 'Duration' }),
        choice([0.5, 1, 2, 5].map(function (d) { return { label: d + 's', value: d }; }), m.duration, function (v) { setMix({ duration: v }); })),
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

  // ---- media ----------------------------------------------------------
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
      accept: 'video/*,image/*,.mkv,.mov,.mp4,.avi,.webm,.m4v,.mpg,.mpeg,.ts,.wmv' });
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
          full ? h('button', { class: 'btn small', text: 'Rename', onclick: function () {
            var to = window.prompt('New name', d.name);
            if (to && to !== d.name) act('POST', '/api/media/rename', { name: d.name, new: to }, refreshMedia);
          } }) : null,
          full ? h('button', { class: 'btn small', text: 'Delete', onclick: function () {
            if (window.confirm('Delete ' + d.name + '?')) act('POST', '/api/media/delete', { name: d.name }, refreshMedia);
          } }) : null));
    });
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Media' }), h('button', { class: 'btn small', text: 'Refresh', onclick: refreshMedia })),
      full ? h('div', { class: 'card' },
        h('div', { class: 'k', id: 'freeline', text: (info.free !== undefined ? megabytes(info.free) + ' free' : '') + (info.max_upload ? ' \u00b7 largest file ' + megabytes(info.max_upload) : '') }),
        picker, h('button', { class: 'btn on', id: 'uploadbtn', text: 'Upload clips', onclick: function () { picker.click(); } }), uploads) : null,
      h('div', { class: 'card' }, h('div', { class: 'list' }, items.length ? items : h('div', { class: 'k', text: 'No clips yet. Upload some, or plug in a USB drive.' }))),
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
    var cards = [vitals];
    cards.push(modulesCard(full), autostartCard(full), streamsCard(full));
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
  function midiCard() {
    var card = h('div', { class: 'card', id: 'midicard' }, h('h2', { text: 'MIDI controller' }));
    var body = h('div', { class: 'list', id: 'midibody' });
    card.appendChild(body);
    if (!moduleOn('control-midi')) {
      body.appendChild(h('div', { class: 'k', id: 'midimsg', text: 'Off. Switch on "MIDI controller (USB)" under Modules above (beta).' }));
      return card;
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'midiline', text: !d.enabled ? 'Off' : (d.connected ? 'Connected' + (d.last ? '. Last message: ' + d.last : '') : 'Waiting for the controller') }));
      var dev = h('select', { class: 'text-input', id: 'midi-device', 'aria-label': 'MIDI device' },
        [h('option', { value: '', text: d.devices.length ? 'Choose a device' : 'No MIDI devices found' })].concat(d.devices.map(function (p) {
          return h('option', { value: p, text: p, selected: p === d.device });
        })));
      var chan = h('select', { class: 'text-input', id: 'midichan', 'aria-label': 'MIDI channel' },
        [h('option', { value: 0, text: 'All channels', selected: d.channel === 0 })].concat(Array.apply(null, Array(16)).map(function (_, i) {
          return h('option', { value: i + 1, text: 'Channel ' + (i + 1), selected: d.channel === i + 1 });
        })));
      function send(patch) { act('POST', '/api/midi', patch, function (data) { say(''); draw(data); }); }
      function fields() { return { device: dev.value, channel: parseInt(chan.value, 10) }; }
      body.appendChild(h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'miditoggle', text: d.enabled ? 'MIDI is on. Turn off' : 'Turn MIDI on',
        onclick: function () { var f = fields(); f.enabled = !d.enabled; send(f); } }));
      body.appendChild(dev); body.appendChild(chan);
      body.appendChild(h('button', { class: 'btn small', id: 'midisave', text: 'Save', onclick: function () { send(fields()); } }));
      body.appendChild(h('div', { class: 'k', text: 'Notes 36 to 71 play pads 1 to 36. See MIDI.md for the rest.' }));
    }
    api('GET', '/api/midi').then(function (r) {
      if (!document.getElementById('midicard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'midimsg', text: r.data.error || 'Not available' })); return; }
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
            netForm.vals = {};
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
  function accessCard() {
    var link = h('input', { class: 'text-input mono', readonly: true, 'aria-label': 'Guest link', hidden: true });
    var role = h('select', { class: 'text-input', 'aria-label': 'Access level' }, h('option', { value: 'view', text: 'View only' }), h('option', { value: 'live', text: 'Live (play and mix)' }));
    var pinOut = h('div', { class: 'mono', id: 'pinout' });
    return h('div', { class: 'card' }, h('h2', { text: 'Access' }),
      h('div', { class: 'k', text: 'Paired devices' }),
      h('div', { class: 'list' }, S.devices.map(function (d) {
        return h('div', { class: 'item' }, h('span', { text: d.name }), h('span', { class: 'row' }, h('span', { class: 'k', text: d.role }),
          h('button', { class: 'btn small', text: 'Remove', onclick: function () {
            act('POST', '/api/devices/revoke', { id: d.id }, function () { S.devices = S.devices.filter(function (x) { return x.id !== d.id; }); if (S.device && d.id === S.device.id) { S.device = null; } render(); });
          } })));
      })),
      h('div', { class: 'k', text: 'Guest link' }), role,
      h('button', { class: 'btn', text: 'Create guest link', onclick: function () {
        act('POST', '/api/devices/invite', { name: 'Guest (' + role.value + ')', role: role.value }, function (d) {
          link.value = location.origin + '/#token=' + d.token; link.hidden = false; link.select();
          api('GET', '/api/devices').then(function (r) { if (r.ok) S.devices = r.data.devices; });
        });
      } }), link,
      h('button', { class: 'btn', text: 'New PIN', onclick: function () { act('POST', '/api/pin/rotate', {}, function (d) { pinOut.textContent = 'New PIN: ' + d.pin; }); } }), pinOut);
  }

  // ---- shell ----------------------------------------------------------
  function render() {
    clearTimeout(netTimer);
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
