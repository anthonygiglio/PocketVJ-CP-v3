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
      if (r[2].ok) S.media = r[2].data.files;
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
      h('div', { class: 'top' }, h('h1', { text: 'PocketVJ' }), h('div', { class: 'pill k', id: 'pill' })),
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
    np.textContent = pl.running && pl.path ? base(pl.path) : (pl.running ? 'Player idle' : 'Player not running');
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
  function media() {
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Media' }), h('button', { class: 'btn small', text: 'Refresh', onclick: function () { api('GET', '/api/media').then(function (r) { if (r.ok) { S.media = r.data.files; render(); } }); } })),
      h('div', { class: 'card' }, h('div', { class: 'list' }, S.media.length ? S.media.map(function (f) {
        return h('div', { class: 'item' }, h('span', { text: f }),
          h('button', { class: 'btn small', text: 'Play', disabled: !can('live'), onclick: function () { act('POST', '/api/play', { file: f }, function () { say('Playing ' + f); poll(); }); } }));
      }) : h('div', { class: 'k', text: 'No clips yet. Copy files into the media folder or plug in a USB drive.' }))),
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
    cards.push(modulesCard(full));
    if (full) cards.push(appearanceCard(), accessCard(), h('div', { class: 'card' }, h('h2', { text: 'Player' }),
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
