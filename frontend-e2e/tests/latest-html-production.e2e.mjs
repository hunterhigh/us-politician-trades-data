import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

// Extracted byte-for-byte outside the DATA assignment from the user-designated
// 2026-09-29 HTML (source SHA-256 d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4).
const TEMPLATE_SHA256 = 'fdaee02db163a3f15285842e66a1047ae3fd8d62e4c110a28fa562f51b878501';
const PLACEHOLDER = '__LATEST_HTML_DATA_JSON__';
const root = path.dirname(fileURLToPath(import.meta.url));
const templatePath = path.resolve(root, '../fixtures/latest-html-template.html');
const template = readFileSync(templatePath);
assert.equal(createHash('sha256').update(template).digest('hex'), TEMPLATE_SHA256);
const templateText = template.toString('utf8');
assert.equal(templateText.split(PLACEHOLDER).length, 2, 'Template must have one DATA slot');

const input = process.env.PRODUCTION_DASHBOARD_HTML;
if (!input || !existsSync(input)) throw new Error('PRODUCTION_DASHBOARD_HTML is required');
const rendered = readFileSync(input, 'utf8');
const match = rendered.match(/const DATA = (\{.*\});\r?\nconst IS_DEMO =/s);
assert.ok(match, 'Production processor must emit its DATA payload');
const data = JSON.parse(match[1]);
assert.notEqual(data.meta.is_demo, true, 'Published DATA must not be demo data');
for (const key of ['people', 'transactions', 'reported_holdings', 'security_market_data', 'source_health']) {
  assert.ok(Array.isArray(data[key]), `${key} must be an array`);
}

function browserExecutable() {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  const executable = candidates.find(existsSync);
  if (!executable) throw new Error('No supported system Chromium executable was found');
  return executable;
}

const work = mkdtempSync(path.join(tmpdir(), 'latest-html-'));
const html = path.join(work, 'dashboard.html');
// Keep the producer's exact serialized bytes (including its escaping) when
// exercising the designated page code. Parsing above is only for assertions.
writeFileSync(html, templateText.replace(PLACEHOLDER, match[1]), 'utf8');
const browser = await chromium.launch({
  executablePath: browserExecutable(), headless: true,
  args: ['--allow-file-access-from-files'],
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(pathToFileURL(html).href, { waitUntil: 'load', timeout: 120_000 });
  assert.match(await page.title(), /Unison.*Dashboard/);
  assert.equal(await page.locator('[data-window-block]').count(), 2);
  assert.ok(await page.locator('#priorityGrid .priority-card').count() > 0);
  assert.ok(await page.locator('#sourceLinks').textContent());
  assert.match(await page.locator('#cutoff').textContent(), /Data cutoff/);

  const cutoffDay = data.meta.data_cutoff_at.slice(0, 10);
  const windowIds = days => {
    const start = new Date(`${cutoffDay}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - (days - 1));
    const first = start.toISOString().slice(0, 10);
    return {
      trades: data.transactions.filter(row => row.transaction_date >= first &&
        row.transaction_date <= cutoffDay).map(row => row.id).sort(),
      disclosures: data.transactions.filter(row => row.filed_at.slice(0, 10) >= first &&
        row.filed_at.slice(0, 10) <= cutoffDay).map(row => row.id).sort(),
    };
  };
  for (const days of [30, 90]) {
    const expected = windowIds(days);
    const visible = await page.locator(`#timeline${days} .timeline-row`).evaluateAll(
      rows => rows.map(row => row.dataset.tx).sort());
    assert.deepEqual(visible, expected.trades, `${days}-day timeline must use transaction_date`);
    const disclosed = await page.evaluate(days => windowDisclosures(days).map(row => row.id).sort(), days);
    assert.deepEqual(disclosed, expected.disclosures,
      `${days}-day disclosure watch must use filed_at`);
  }
  const officialSources = data.source_health.filter(row =>
    ['house_clerk', 'senate_efd', 'oge'].includes(row.source_id));
  assert.equal(await page.locator('#sourceLinks .source-link').count(), officialSources.length);
  const sourceText = await page.locator('#sourceLinks').textContent();
  for (const source of officialSources) {
    assert.ok(sourceText?.includes(source.source), `${source.source_id} must be visible`);
    assert.ok(sourceText?.includes(source.status === 'ok' ? 'Synced' : 'Delayed'),
      `${source.source_id} health must retain status meaning`);
  }

  await page.locator('#window30TabTimeline').click();
  assert.ok(await page.locator('#timeline30 .timeline-row').count() > 0);
  await page.locator('#timeline30 .timeline-row').first().click();
  assert.equal(await page.locator('#drawer').getAttribute('aria-hidden'), 'false');
  await page.keyboard.press('Escape');

  const personId = data.transactions[0]?.person_id;
  assert.ok(personId);
  await page.evaluate(id => openPerson(id), personId);
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'false');
  await page.locator('#personBack').click();

  const ticker = data.transactions.find(row => row.ticker &&
    data.security_market_data.some(market => market.ticker === row.ticker))?.ticker;
  assert.ok(ticker, 'A published ticker with market data is required');
  await page.evaluate(symbol => openTicker(symbol), ticker);
  assert.equal(await page.locator('#stockPage').getAttribute('aria-hidden'), 'false');
  assert.equal((await page.locator('.stock-ticker').textContent())?.trim(), ticker);
  await page.locator('#stockBack').click();

  if (data.meta.data_cutoff_at >= '2026-10-05T23:59:59Z') {
    const expected = new Map([
      ['house-ptr:0a2b32bd0652355b8f3c5778', ["20035553", 'house:P000197', null]],
      ['house-ptr:19a0eff5b8a03278db3585c5', ["20035558", 'house:R000619', 'GS']],
      ['house-ptr:d2baf604411e36ed95a307cb', ["20035558", 'house:R000619', 'WFC']],
      ['house-ptr:edbfd95b3a6eda4bc27cf9a0', ["20035558", 'house:R000619', 'MSFT']],
    ]);
    for (const row of data.transactions) {
      if (expected.has(row.id)) {
        assert.deepEqual([row.filing_id, row.person_id, row.ticker], expected.get(row.id));
        expected.delete(row.id);
      }
    }
    assert.equal(expected.size, 0, 'October 5 House index PTR facts must reach the page');
    await page.evaluate(() => openPerson('house:P000197'));
    assert.match(await page.locator('#personPageContent').textContent(), /Pelosi/);
    await page.locator('#personBack').click();
    await page.evaluate(() => openPerson('house:R000619'));
    const rulliPage = await page.locator('#personPageContent').textContent();
    assert.match(rulliPage, /Rulli/);
    if (data.meta.data_cutoff_at <= '2026-10-27T23:59:59Z') {
      assert.match(rulliPage, /MSFT/);
    }
  }
  const marketByTicker = new Map(data.security_market_data.map(row => [row.ticker, row]));
  const prehistory = [];
  for (const row of data.transactions) {
    const first = marketByTicker.get(row.ticker)?.price_history?.[0]?.date;
    if (!first) continue;
    if (row.transaction_date.slice(0, 10) < first) {
      assert.equal(row.underlying_return_since_trade, null,
        `${row.id}: trade return before verified price history must be null`);
      prehistory.push(row);
    }
    if (row.filed_at.slice(0, 10) < first) {
      assert.equal(row.underlying_return_since_filing, null,
        `${row.id}: filing return before verified price history must be null`);
    }
  }
  for (const row of prehistory.slice(0, 3)) {
    await page.evaluate(id => openTransaction(id), row.id);
    const tradeReturn = await page.locator('#drawer .fact').filter({ hasText: 'Security since trade' }).locator('b').textContent();
    assert.equal(tradeReturn?.trim(), '—');
    await page.keyboard.press('Escape');
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', template_sha256: TEMPLATE_SHA256,
    cutoff: data.meta.data_cutoff_at, transactions: data.transactions.length }));
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
