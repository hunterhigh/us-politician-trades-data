import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';

// Read-only interaction audit for the user-designated HTML baseline.
const html = process.argv[2] ?? 'C:\\Users\\admin\\Downloads\\politician-disclosures (3).html';
const expectedSha256 = 'D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4';
const chrome = process.env.CHROME_PATH ?? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const htmlBytes = readFileSync(html);
const actualSha256 = createHash('sha256').update(htmlBytes).digest('hex').toUpperCase();
assert.equal(actualSha256, expectedSha256, 'HTML baseline hash changed; stop and re-audit before using this test');

const profile = mkdtempSync(path.join(tmpdir(), 'whitehouse-latest-html-'));
const browser = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--allow-file-access-from-files', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  pathToFileURL(path.resolve(html)).href,
], { stdio: 'ignore', windowsHide: true });
let socket;

try {
  const activePort = path.join(profile, 'DevToolsActivePort');
  let port;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { port = Number(readFileSync(activePort, 'utf8').split(/\r?\n/)[0]); break; }
    catch { if (browser.exitCode !== null) throw new Error(`Chrome exited with ${browser.exitCode}`); await delay(100); }
  }
  assert.ok(port, 'Chrome did not expose its temporary DevTools port');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const pageTarget = targets.find(target => target.type === 'page' && target.url.startsWith('file:'));
  assert.ok(pageTarget, 'Chrome did not open the requested local HTML page');
  socket = new WebSocket(pageTarget.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id); pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    }
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await command('Page.enable'); await command('Runtime.enable');
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate('document.readyState === "complete" && !!document.querySelector("#cutoff")')) break;
    await delay(100);
  }
  await delay(300);
  assert.equal(await evaluate('document.title'), 'Unison · Dashboard');
  const initial = await evaluate(`({
    blocks: document.querySelectorAll('[data-window-block]').length,
    hotTabs: document.querySelectorAll('[data-window-view="hot-stocks"].active').length,
    marketRows: DATA.security_market_data.length,
    nullReturns: DATA.transactions.filter(t => t.underlying_return_since_trade == null).length,
    noTicker: DATA.transactions.filter(t => !t.ticker).length,
    noInstrument: DATA.transactions.filter(t => !t.instrument_type).length,
    missingSamples: Object.fromEntries(['ticker', 'instrument_type', 'underlying_return_since_trade'].map(field => {
      const tx = windowTransactions(30).find(item => field === 'ticker' ? !item.ticker : item[field] == null || item[field] === '');
      return [field, tx ? {id: tx.id, asset_name: tx.asset_name, person: PEOPLE[tx.person_id].display_name} : null];
    })),
    marketRowKeys: Object.keys(DATA.security_market_data[0] || {})
  })`);
  assert.equal(initial.blocks, 2, 'expected separate 30-day and 90-day dashboard blocks');
  assert.equal(initial.hotTabs, 2, 'both blocks should initially show hot stocks');

  await evaluate(`document.querySelector('#window30TabTimeline').click()`);
  assert.equal(await evaluate('document.querySelectorAll("[data-window-view=timeline].active").length'), 2);
  assert.equal(await evaluate('document.querySelector("#timeline30").children.length > 0'), true);
  await evaluate(`document.querySelector('#timelineSearch30').value = 'not-a-real-security'; document.querySelector('#timelineSearch30').dispatchEvent(new Event('input', {bubbles:true}))`);
  assert.equal(await evaluate('document.querySelectorAll("#timeline30 .timeline-row").length'), 0, 'search should filter the 30-day block');
  assert.match(await evaluate('document.querySelector("#timelineCount30").textContent'), /0 OF/);
  assert.equal(await evaluate('document.querySelectorAll("#timeline90 .timeline-row").length > 0'), true, '30-day search must not alter the 90-day block');
  await evaluate(`document.querySelector('#timelineSearch30').value = ''; document.querySelector('#timelineSearch30').dispatchEvent(new Event('input', {bubbles:true}))`);

  for (const [field, sample] of Object.entries(initial.missingSamples)) {
    assert.ok(sample, `expected a 30-day sample with missing ${field}`);
    await evaluate(`document.querySelector('#timelineSearch30').value = ${JSON.stringify(sample.asset_name)}; document.querySelector('#timelineSearch30').dispatchEvent(new Event('input', {bubbles:true}))`);
    const row = `document.querySelector('#timeline30 [data-tx="${sample.id}"]')`;
    assert.equal(await evaluate(`${row} !== null`), true, `${field} sample should remain visible`);
    if (field === 'ticker') assert.match(await evaluate(`${row}.querySelector('.security').innerText`), /^—/);
    if (field === 'instrument_type') assert.match(await evaluate(`${row}.querySelector('.security').innerText`), /Type not stated/);
    if (field === 'underlying_return_since_trade') assert.match(await evaluate(`${row}.querySelector('.return').innerText`), /^—/);
  }
  await evaluate(`document.querySelector('#timelineSearch30').value = ''; document.querySelector('#timelineSearch30').dispatchEvent(new Event('input', {bubbles:true}))`);

  await evaluate(`document.querySelector('#timeline30 .timeline-row').click()`);
  assert.equal(await evaluate('document.querySelector("#drawer").getAttribute("aria-hidden")'), 'false', 'transaction row should open disclosure drawer');
  assert.equal(await evaluate('document.querySelector("#drawerContent").innerText.includes("null") || document.querySelector("#drawerContent").innerText.includes("undefined")'), false, 'drawer should not expose JavaScript null/undefined placeholders');
  await evaluate(`document.querySelector('#drawerClose').click()`);
  await evaluate(`([...document.querySelectorAll('#timeline30 .timeline-row')].find(row => row.querySelector('[data-ticker-link]'))).querySelector('[data-person-link]').click()`);
  assert.equal(await evaluate('document.querySelector("#personPage").getAttribute("aria-hidden")'), 'false', 'person link should open profile');
  const personText = await evaluate('document.querySelector("#personPageContent").innerText');
  assert.equal(/\b(null|undefined)\b/.test(personText), false, 'person page should not expose JavaScript null/undefined placeholders');
  await evaluate(`document.querySelector('#personPage [data-ticker-link]').click()`);
  assert.equal(await evaluate('document.querySelector("#stockPage").getAttribute("aria-hidden")'), 'false', 'ticker link should open security page');
  const stockText = await evaluate('document.querySelector("#stockPageContent").innerText');
  assert.equal(/\b(null|undefined)\b/.test(stockText), false, 'security page should not expose JavaScript null/undefined placeholders');
  await evaluate(`document.querySelector('#stockBack').click()`);
  await evaluate(`document.querySelector('#personBack').click()`);

  await evaluate(`document.querySelector('[data-window-anchor="90"]').click()`);
  await delay(150);
  assert.equal(await evaluate('document.querySelectorAll("[data-window-anchor=\\\"90\\\"].active").length'), 1, '90-day anchor should become active');
  assert.equal(await evaluate('window.scrollY > 0'), true, '90-day anchor should move the dashboard to the second window');

  console.log(JSON.stringify({ status: 'passed', html, sha256: actualSha256, chrome, observed: initial }));
} finally {
  try { socket?.close(); } catch {}
  browser.kill();
  await delay(1200);
  for (let attempt = 0; attempt < 10; attempt++) {
    try { rmSync(profile, { recursive: true, force: true }); break; }
    catch (error) { if (attempt === 9) throw error; await delay(300); }
  }
}
