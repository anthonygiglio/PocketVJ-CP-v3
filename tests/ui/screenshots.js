// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Takes the cropped screenshots of the panel that live in docs/images/ui.
// Run: SHOTS=docs/images/ui node tests/ui/screenshots.js   (needs playwright and a Chromium, like panel.test.js)
// It drives the same harness as the browser test (a real panel, a headless mpv, a fake network helper), so
// what you see is what the code produces. Devices, network addresses and clips shown are the harness's.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const out = process.env.SHOTS || path.join(__dirname, '..', '..', 'docs', 'images', 'ui');
const pyBin = process.env.PYTHON || 'python3';
fs.mkdirSync(out, { recursive: true });

function startServer() {
  return new Promise((resolve, reject) => {
    const p = spawn(pyBin, [path.join(__dirname, 'harness.py')], { cwd: path.join(__dirname, '..', '..'), stdio: ['ignore', 'pipe', 'inherit'] });
    let buf = '';
    p.stdout.on('data', (d) => { buf += d; if (buf.includes('\n')) resolve({ p, info: JSON.parse(buf.split('\n')[0]) }); });
    p.on('exit', (c) => reject(new Error('harness exited ' + c)));
  });
}

(async () => {
  const { p: server, info } = await startServer();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const base = 'http://127.0.0.1:' + info.port;
  const failures = [];
  const saved = [];
  // One bad shot must not lose the others.
  async function shot(name, fn) {
    try { await fn(path.join(out, name + '.png')); saved.push(name); } catch (e) { failures.push(name + ': ' + e.message.split('\n')[0]); }
  }
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const api = (method, url, body) => page.evaluate(async ([m, u, b]) => {
      const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: m === 'POST' ? JSON.stringify(b || {}) : undefined });
      return r.json().catch(() => ({}));
    }, [method, url, body]);
    const card = (title) => page.locator('.card', { has: page.locator('h2', { hasText: new RegExp('^' + title) }) }).first();
    const shell = () => page.locator('.shell').first();

    await page.goto(base + '/');
    await page.waitForSelector('.pin');
    await shot('connect', (f) => shell().screenshot({ path: f }));

    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, info.pin[i]);
    await page.fill('#devname', 'Anthony\'s phone');
    await page.click('text=Pair this device');
    await page.waitForSelector('.pads');

    // A believable show: labelled pads across a bank, one playing.
    // Extra clips (not playable, only for the lists) so every pad has its own file.
    for (const name of ['loop-a.mkv', 'loop-b.mkv', 'sting.mkv', 'outro.mkv']) {
      await page.evaluate(async (n) => {
        const bytes = new Uint8Array(300000 + n.length * 40000).fill(7);
        await fetch('/api/media/upload?name=' + encodeURIComponent(n), { method: 'POST', credentials: 'same-origin',
          headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/octet-stream' }, body: bytes });
      }, name);
    }
    const labels = [['Intro', 'intro.mkv'], ['Tunnel', 'tunnel.mkv'], ['Loop A', 'loop-a.mkv'], ['Loop B', 'loop-b.mkv'], ['Sting', 'sting.mkv'], ['Outro', 'outro.mkv']];
    for (let i = 0; i < labels.length; i++) await api('POST', '/api/pads', { bank: 0, index: i, label: labels[i][0], file: labels[i][1] });
    await api('POST', '/api/play', { pad: [0, 0] });
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live', (f) => shell().screenshot({ path: f }));

    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    await shot('mix', (f) => shell().screenshot({ path: f }));

    await page.click('nav >> text=Media');
    await page.waitForSelector('#uploadbtn');
    await page.waitForTimeout(400);
    // Crop to the content: the list is short and the screen is tall.
    await shot('media', async (f) => {
      const bottom = await page.evaluate(() => { const c = document.querySelectorAll('.screen .card'); return Math.ceil(c[c.length - 1].getBoundingClientRect().bottom); });
      await page.screenshot({ path: f, clip: { x: 0, y: 0, width: 390, height: bottom + 16 } });
    });

    // Turn the beta modules on and give them something to show.
    for (const id of ['scheduler', 'control-dmx', 'control-midi', 'inputs-srt', 'network']) await api('POST', '/api/modules/' + id, { enabled: true });
    await api('POST', '/api/schedule', { enabled: true, entries: [
      { label: 'Doors', time: '18:30', days: [4, 5], action: 'play', file: 'intro.mkv', loop: true },
      { label: 'Close', time: '23:30', days: [0, 1, 2, 3, 4, 5, 6], action: 'blackout' }] });
    await api('POST', '/api/streams', { action: 'add', name: 'Stage camera', url: 'rtsp://admin:secret@192.168.0.40/live' });
    await api('POST', '/api/streams', { action: 'add', name: 'Laptop (SRT)', url: 'srt://192.168.0.20:9000' });
    await page.click('nav >> text=System');
    await page.waitForSelector('#schedclock');
    await page.waitForSelector('.stream-entry');
    await page.waitForSelector('#dmxline');
    await page.waitForSelector('#midiline');
    await page.waitForSelector('#netiface');
    await page.waitForTimeout(800);

    await page.evaluate(() => { document.querySelector('.tabs').style.setProperty('display', 'none', 'important'); });   // the fixed tab bar would cover the bottom of tall cards
    await shot('system-vitals', (f) => card('Vitals').screenshot({ path: f }));
    await shot('system-modules', (f) => card('Modules').screenshot({ path: f }));
    await shot('schedule', (f) => card('Schedule').screenshot({ path: f }));
    await shot('streams', (f) => card('Streams').screenshot({ path: f }));
    await shot('dmx', (f) => card('DMX').screenshot({ path: f }));
    await shot('midi', (f) => card('MIDI').screenshot({ path: f }));
    await shot('network', (f) => card('Network').screenshot({ path: f }));
    await shot('control-osc', (f) => card('Control \\(OSC\\)').screenshot({ path: f }));
    await shot('appearance', (f) => card('Appearance').screenshot({ path: f }));
    await shot('access', (f) => card('Access').screenshot({ path: f }));

    await page.evaluate(() => { document.querySelector('.tabs').style.removeProperty('display'); });

    // Another theme, and the wide layout.
    await api('POST', '/api/theme', { name: 'night-red', accent: null });
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live-night-red', (f) => shell().screenshot({ path: f }));
    await api('POST', '/api/theme', { name: 'dark-stage', accent: null });

    await page.setViewportSize({ width: 1180, height: 760 });
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live-desktop', (f) => page.screenshot({ path: f }));
  } catch (e) {
    failures.push('run: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
    server.kill();
  }
  console.log('saved: ' + saved.join(', '));
  if (failures.length) console.error('FAILED shots:\n' + failures.join('\n'));
  process.exit(saved.length >= 10 && failures.length === 0 ? 0 : 1);
})();
