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
const previousRoot = process.env.PREVIOUS_MAIN_ROOT;
let newTransactions = [];
if (previousRoot) {
  const manifest = JSON.parse(readFileSync(path.join(previousRoot, 'manifest.json'), 'utf8'));
  const board = JSON.parse(readFileSync(path.join(previousRoot, 'board', `${manifest.board}.json`), 'utf8'));
  const oldIds = new Set(board.transactions.map(row => row.id));
  newTransactions = data.transactions.filter(row => !oldIds.has(row.id));
}
const money = value => '$' + new Intl.NumberFormat('en-US').format(value);
const amountRange = row => `${money(row.amount_low)}–${money(row.amount_high)}`;
const dateLabel = value => {
  const [, year, month, day] = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/) || [];
  return year ? `${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][Number(month)-1]} ${Number(day)}` : String(value);
};
const fact = async (page, label) => page.locator('#drawer .fact').filter({ has: page.locator('span', { hasText: label }) }).locator('b').textContent();

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
    const actualOrder = await page.locator(`#timeline${days} .timeline-row`).evaluateAll(
      rows => rows.map(row => row.dataset.tx));
    const expectedIds = new Set(expected.trades);
    const expectedOrder = data.transactions.filter(row => expectedIds.has(row.id))
      .sort((a, b) => new Date(b.filed_at) - new Date(a.filed_at)).map(row => row.id);
    assert.deepEqual(actualOrder, expectedOrder, `${days}-day timeline must retain filing-date order`);
    const hot = await page.locator(`#hotStocks${days} .hot-stock-row`).evaluateAll(rows => rows.map(row => ({
      ticker: row.querySelector('.hot-stock-ticker')?.textContent,
      returnText: row.querySelector('.hot-stock-return')?.firstChild?.textContent?.trim(),
    })));
    const moveByTicker = new Map((data.market_moves?.[String(days)] || []).map(row => [row.ticker, row]));
    const hotTickers = new Set(hot.map(row => row.ticker));
    for (const symbol of moveByTicker.keys()) {
      assert.ok(hotTickers.has(symbol), `${days}-day ${symbol} market move must appear in the hot-stock table`);
    }
    for (const row of hot) {
      const move = moveByTicker.get(row.ticker);
      if (!move) continue; // Multiple filers can make a ticker visible without a market move.
      const value = move.return_since_first_trade;
      const expectedReturn = value == null || !Number.isFinite(Number(value))
        ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(1)}%`;
      assert.equal(row.returnText, expectedReturn, `${days}-day ${row.ticker} market move`);
    }
  }
  const allowed = new Set(data.processing?.disclosure_source_ids || []);
  const latestSources = new Map();
  for (const row of data.source_health.filter(item => allowed.has(item.source_id) && item.source_url)) {
    const stamp = row.last_successful_sync_at || row.data_cutoff_at || row.last_checked_at || '';
    const old = latestSources.get(row.source_id);
    const oldStamp = old && (old.last_successful_sync_at || old.data_cutoff_at || old.last_checked_at || '');
    if (!old || stamp > oldStamp) latestSources.set(row.source_id, row);
  }
  const sourceCards = await page.locator('#sourceLinks .source-link').evaluateAll(links => links.map(link => ({
    href: link.getAttribute('href'), text: link.textContent,
  })));
  assert.equal(sourceCards.length, latestSources.size);
  for (const source of latestSources.values()) {
    const card = sourceCards.find(item => item.href === source.source_url);
    assert.ok(card, `${source.source_id} official link must be reachable`);
    assert.ok(card.text.includes(`${source.status === 'ok' ? 'Synced' : 'Delayed'} · ${dateLabel(source.last_successful_sync_at || source.data_cutoff_at || source.last_checked_at)}`),
      `${source.source_id} must show its own successful-sync/cutoff/check time`);
  }

  await page.locator('#window30TabTimeline').click();
  assert.ok(await page.locator('#timeline30 .timeline-row').count() > 0);
  const original90Count = await page.locator('#timeline90 .timeline-row').count();
  await page.locator('#timelineSearch30').fill('not-a-real-security');
  assert.equal(await page.locator('#timeline30 .timeline-row').count(), 0);
  assert.equal(await page.locator('#timeline90 .timeline-row').count(), original90Count,
    '30-day search must not change the 90-day block');
  await page.locator('#timelineSearch30').fill('');
  await page.locator('#window-30 [data-tx-filter="purchase"]').click();
  const purchases30 = windowIds(30).trades.filter(id =>
    data.transactions.find(row => row.id === id)?.transaction_type === 'purchase');
  assert.equal(await page.locator('#timeline30 .timeline-row').count(), purchases30.length);
  await page.locator('#window-30 [data-tx-filter="all"]').click();
  // Click the row itself. A coordinate click can hit its nested person/ticker
  // button, whose navigation intentionally takes precedence over the drawer.
  await page.locator('#timeline30 .timeline-row').first().evaluate(row =>
    row.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  assert.equal(await page.locator('#drawer').getAttribute('aria-hidden'), 'false');
  const firstId = await page.locator('#timeline30 .timeline-row').first().getAttribute('data-tx');
  const first = data.transactions.find(row => row.id === firstId);
  assert.equal((await fact(page, 'Disclosed amount range'))?.trim(), amountRange(first));
  assert.equal((await fact(page, 'Trade date'))?.trim(), first.transaction_date);
  assert.equal((await fact(page, 'Filed'))?.trim(), first.filed_at.replace('T', ' '));
  assert.ok((await page.locator('#drawerContent').textContent())?.includes(first.asset_name));
  assert.ok((await page.locator('#drawerContent').textContent())?.includes(first.owner === 'Self' ? "in the filer's own name" : `held by ${first.owner}`));
  assert.equal(await page.locator('#drawerContent a').filter({ hasText: 'View the official record' }).getAttribute('href'), first.source_url);
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
  const market = data.security_market_data.find(row => row.ticker === ticker);
  assert.ok(market.price_history.length >= 2);
  assert.equal(await page.locator('.stock-price-chart .price-line').count(), 1);
  assert.equal(await page.locator('.stock-price-chart .latest-dot').count(), 1);
  assert.ok((await page.locator('.stock-price-chart').getAttribute('aria-label'))?.includes(`${ticker} `));
  assert.ok((await page.locator('.stock-price-notes').textContent())?.includes(`Prices through ${market.as_of_date}`));
  await page.locator('#stockBack').click();

  const noTicker = data.transactions.find(row => !row.ticker);
  assert.ok(noTicker, 'a missing ticker case is required for the null-state gate');
  await page.evaluate(id => openTransaction(id), noTicker.id);
  assert.match(await page.locator('#drawerContent').textContent(), /unmapped ticker/);
  await page.keyboard.press('Escape');
  const noMarket = data.transactions.find(row => row.ticker &&
    !data.security_market_data.some(marketRow => marketRow.ticker === row.ticker));
  if (noMarket) {
    await page.evaluate(symbol => openTicker(symbol), noMarket.ticker);
    assert.equal(await page.locator('.stock-price-chart').count(), 0);
    assert.match(await page.locator('.stock-price-empty').textContent(), /no price series/i);
    assert.match(await page.locator('.stock-metric-price').textContent(), /—/);
    await page.locator('#stockBack').click();
  }

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
  for (const row of prehistory.slice(0, 7)) {
    await page.evaluate(id => openTransaction(id), row.id);
    const displayedReturn = page.locator('#drawer .fact').filter({ hasText: 'Security since trade' }).locator('b');
    const tradeReturn = await displayedReturn.textContent();
    assert.equal(tradeReturn?.trim(), '—');
    assert.equal(await displayedReturn.getAttribute('class'), '', `${row.id}: missing return must have no gain/loss color`);
    await page.keyboard.press('Escape');
  }
  const recentIds = new Set(windowIds(90).trades);
  for (const row of newTransactions) {
    await page.evaluate(id => openTransaction(id), row.id);
    assert.equal((await fact(page, 'Disclosed amount range'))?.trim(), amountRange(row), `${row.id}: new row amount`);
    assert.equal((await fact(page, 'Trade date'))?.trim(), row.transaction_date, `${row.id}: new row trade date`);
    assert.equal(await page.locator('#drawerContent a').filter({ hasText: 'View the official record' }).getAttribute('href'),
      row.source_url, `${row.id}: new row evidence URL`);
    await page.keyboard.press('Escape');
    if (recentIds.has(row.id)) {
      assert.equal(await page.locator(`#timeline90 [data-tx="${row.id}"]`).count(), 1,
        `${row.id}: recent new row must appear in the 90-day timeline`);
      await page.evaluate(id => openPerson(id), row.person_id);
      assert.equal(await page.locator(`[data-person-tx="${row.id}"]`).count(), 1,
        `${row.id}: recent new row must appear on the person page`);
      await page.locator('#personBack').click();
    }
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', template_sha256: TEMPLATE_SHA256,
    cutoff: data.meta.data_cutoff_at, transactions: data.transactions.length,
    new_transactions_checked: newTransactions.length, previous_board_compared: Boolean(previousRoot) }));
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
