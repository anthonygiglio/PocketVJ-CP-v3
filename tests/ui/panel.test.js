// SPDX-FileCopyrightText: 2026 NXLX and contributors
// SPDX-License-Identifier: Apache-2.0
// Browser test: drives the real panel against tests/ui/harness.py.
// Run: node tests/ui/panel.test.js   (needs playwright and a Chromium)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const assert = require('assert');

const shots = process.env.SHOTS || '';
const pyBin = process.env.PYTHON || 'python3';

function startServer() {
  return new Promise((resolve, reject) => {
    const p = spawn(pyBin, [path.join(__dirname, 'harness.py')], { cwd: path.join(__dirname, '..', '..'), stdio: ['ignore', 'pipe', 'inherit'] });
    let buf = '';
    p.stdout.on('data', (d) => { buf += d; const line = buf.split('\n')[0]; if (buf.includes('\n')) resolve({ p, info: JSON.parse(line) }); });
    p.on('exit', (c) => reject(new Error('harness exited ' + c)));
  });
}

(async () => {
  const { p: server, info } = await startServer();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  let failed = false;
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const problems = [];
    // The 401 before pairing, the 403 for the wrong PIN and the 400 for a refused upload are provoked on purpose.
    const expected = /status of (400|401|403)/;
    page.on('console', (m) => { if (['error', 'warning'].includes(m.type()) && !expected.test(m.text())) problems.push(m.text()); });
    page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
    const base = 'http://127.0.0.1:' + info.port;
    await page.goto(base + '/');

    // Connect screen
    await page.waitForSelector('.pin');
    if (shots) await page.screenshot({ path: path.join(shots, '1-connect.png') });
    // wrong PIN first
    const wrong = info.pin === '0000' ? '1111' : '0000';
    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, wrong[i]);
    await page.click('text=Pair this device');
    await page.waitForFunction(() => /Wrong PIN/.test(document.getElementById('msg').textContent));
    // right PIN
    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, info.pin[i]);
    await page.click('text=Pair this device');
    await page.waitForSelector('.pads');

    // Live: assign a pad, then play it
    await page.click('text=Edit pads');
    await page.click('.pad >> nth=0');
    await page.click('.sheet >> text=intro.mkv');
    await page.click('text=Done editing');
    await page.waitForSelector('.pad:not(.empty)');
    await page.click('.pad >> nth=0');
    await page.waitForFunction(() => /intro/.test(document.getElementById('np').textContent), null, { timeout: 8000 });
    await page.waitForSelector('.pad.on');
    if (shots) await page.screenshot({ path: path.join(shots, '2-live.png') });
    await page.click('#black');
    await page.waitForFunction(() => document.getElementById('black').textContent === 'Show');
    await page.click('#black');
    await page.waitForFunction(() => document.getElementById('black').textContent === 'Blackout');

    // Media: upload a file, see it listed, rename it, delete it
    await page.click('nav >> text=Media');
    await page.waitForSelector('#uploadbtn');
    await page.setInputFiles('#filepick', { name: 'from-phone.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(300000, 7) });
    await page.waitForSelector('.item:has-text("from-phone.mp4") >> text=Rename', { timeout: 8000 });
    page.once('dialog', (d) => d.accept('renamed-on-phone.mp4'));
    await page.click('.item:has-text("from-phone.mp4") >> text=Rename');
    await page.waitForSelector('.item:has-text("renamed-on-phone.mp4")');
    page.once('dialog', (d) => d.accept());
    await page.click('.item:has-text("renamed-on-phone.mp4") >> text=Delete');
    await page.waitForFunction(() => !/renamed-on-phone/.test(document.body.textContent));
    await page.setInputFiles('#filepick', { name: 'virus.exe', mimeType: 'application/octet-stream', buffer: Buffer.alloc(100, 1) });
    await page.waitForFunction(() => /only video and image files/.test(document.getElementById('uploads').textContent), null, { timeout: 8000 });

    // Mix: drag a slider and check the throttle keeps request count sane
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    let controlCalls = 0;
    page.on('request', (r) => { if (r.url().endsWith('/api/control') && r.method() === 'POST') controlCalls++; });
    await page.evaluate(() => {
      const el = document.getElementById('mo');
      for (let v = 100; v >= 20; v--) { el.value = v; el.dispatchEvent(new Event('input')); }
      el.dispatchEvent(new Event('change'));
    });
    await page.waitForTimeout(400);
    assert(controlCalls > 0 && controlCalls < 10, 'expected throttled slider requests, got ' + controlCalls);
    if (shots) await page.screenshot({ path: path.join(shots, '3-mix.png') });
    await page.click('text=90°');
    await page.click('text=Reset mix');

    // System: modules, appearance, guest link
    await page.click('nav >> text=System');
    await page.waitForSelector('text=Modules');
    assert(await page.isVisible('text=NDI'), 'NDI module listed');
    assert(await page.isVisible('text=Not built yet'), 'planned modules are labelled');
    // Network: switch the module on, preview, apply, watch the countdown, confirm
    await page.click('.item:has-text("Network settings") >> button');
    await page.waitForSelector('#netiface');
    await page.waitForSelector('#netcard >> text=192.168.1.9/24', { timeout: 8000 });  // the address arrives after the card first draws
    await page.click('#netmodes >> text=Fixed address');
    await page.fill('#netaddr', '192.168.50.20');
    await page.fill('#netprefix', '24');
    await page.fill('#netgw', '192.168.50.1');
    // A redraw of the screen must not wipe what was typed
    const oldField = await page.$('#netaddr');
    await page.click('nav >> text=System');
    await page.waitForFunction((el) => !el.isConnected, oldField);
    await page.waitForSelector('#netaddr');
    assert.strictEqual(await page.inputValue('#netaddr'), '192.168.50.20', 'typed address survives a redraw');
    assert.strictEqual(await page.inputValue('#netgw'), '192.168.50.1', 'typed gateway survives a redraw');
    await page.click('#netpreview');
    await page.waitForFunction(() => /ipv4\.addresses 192\.168\.50\.20\/24/.test(document.getElementById('netplan').textContent));
    await page.fill('#netaddr', '8.8.8.8; reboot');
    if (shots) await page.screenshot({ path: path.join(shots, '6-network.png'), fullPage: true });
    await page.click('#netapply');
    await page.waitForFunction(() => /address/i.test(document.getElementById('netresult').textContent) && document.getElementById('netresult').className.includes('err'));
    await page.fill('#netaddr', '192.168.50.20');
    await page.click('#netapply');
    await page.waitForSelector('#netpending');
    await page.waitForFunction(() => /Reverts in \d+ s/.test(document.getElementById('netleft').textContent));
    await page.click('#netconfirm');
    await page.waitForSelector('#netiface');
    await page.click('#netmodes >> text=Direct cable');
    await page.click('#netapply');
    await page.waitForSelector('#netpending');
    await page.click('#netrevert');
    await page.waitForSelector('#netiface');
    await page.waitForSelector('#oscline:has-text("Off")');
    await page.click('#osctoggle');
    await page.waitForFunction(() => /Listening on UDP/.test(document.getElementById('oscline').textContent));
    await page.click('#osctoggle');
    await page.waitForFunction(() => document.getElementById('oscline').textContent === 'Off');
    await page.click('button:has-text("Night red")');
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 0)');
    await page.click('text=Create guest link');
    await page.waitForFunction(() => { const i = document.querySelector('input[aria-label="Guest link"]'); return i && !i.hidden && /#token=/.test(i.value); });
    const guestLink = await page.inputValue('input[aria-label="Guest link"]');
    if (shots) await page.screenshot({ path: path.join(shots, '4-system.png'), fullPage: true });

    // Guest (view only) via the link in a fresh context
    const guestCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const guest = await guestCtx.newPage();
    guest.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push('guest: ' + m.text()); });
    await guest.goto(guestLink.replace(/^https?:\/\/[^/]+/, base));
    await guest.waitForSelector('.pads');
    assert(await guest.isDisabled('#black'), 'view-only guest cannot blackout');
    assert(!(await guest.isVisible('text=Edit pads')), 'view-only guest cannot edit pads');
    assert.strictEqual(await guest.evaluate(() => location.hash), '', 'token removed from the URL');

    // Desktop width
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    if (shots) await page.screenshot({ path: path.join(shots, '5-desktop.png') });

    const csp = problems.filter((t) => /Content Security Policy|Refused to/i.test(t));
    assert.deepStrictEqual(csp, [], 'CSP violations: ' + csp.join('; '));
    assert.deepStrictEqual(problems, [], 'console problems: ' + problems.join('; '));
    console.log('panel browser test: OK');
  } catch (e) {
    failed = true;
    console.error('FAILED:', e.message, (e.stack || '').split('\n').filter(function (l) { return /panel.test.js/.test(l); }).slice(0, 2).join(' | '));
  } finally {
    await browser.close();
    server.kill();
  }
  process.exit(failed ? 1 : 0);
})();
