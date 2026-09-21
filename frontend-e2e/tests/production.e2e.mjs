import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

function browserExecutable() {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  const found = candidates.find(existsSync);
  if (!found) throw new Error('No supported system Chromium executable was found');
  return found;
}

const input = process.env.PRODUCTION_DASHBOARD_HTML;
if (!input || !existsSync(input)) throw new Error('PRODUCTION_DASHBOARD_HTML is required');
const source = readFileSync(input, 'utf8');
assert.match(source, /OFFICIAL DISCLOSURE DATA/);
const embedded = source.match(/const DATA = (\{.*\});\r?\nconst IS_DEMO =/s);
assert.ok(embedded, 'Rendered dashboard does not contain the frozen DATA payload');
const data = JSON.parse(embedded[1]);
assert.equal(data.meta?.is_demo, false);
for (const key of ['people', 'transactions', 'reported_holdings', 'security_market_data', 'source_health']) {
  assert.ok(Array.isArray(data[key]) && data[key].length > 0, `${key} must be a non-empty array`);
}
const marketTickers = new Set(data.security_market_data.map(row => row.ticker));
const marketTicker = data.transactions.find(row => row.ticker && marketTickers.has(row.ticker))?.ticker;
assert.ok(marketTicker, 'At least one disclosed ticker must have a market series');

const browser = await chromium.launch({
  executablePath: browserExecutable(),
  headless: true,
  args: ['--allow-file-access-from-files'],
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  await page.goto(pathToFileURL(path.resolve(input)).href, { waitUntil: 'load', timeout: 120_000 });
  assert.match(await page.title(), /White House Stock Tracker/);
  assert.equal(await page.locator('[data-window-block]').count(), 2);
  assert.ok(await page.locator('[data-person-link]').count() > 0);
  assert.ok(await page.locator('[data-ticker-link]').count() > 0);

  await page.locator('[data-window-view="timeline"]').first().click();
  assert.ok(await page.locator('#window-30 .timeline-row, #window-90 .timeline-row').count() > 0);
  const firstTimeline = page.locator('#window-30 .timeline-row, #window-90 .timeline-row').first();
  await firstTimeline.evaluate(element => element.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  assert.equal(await page.locator('#drawer').getAttribute('aria-hidden'), 'false');
  assert.match(await page.locator('#drawerContent').textContent(), /OFFICIAL DISCLOSURE/);
  assert.ok(await page.locator('#drawerContent a[href^="https://"]').count() > 0);
  await page.keyboard.press('Escape');

  await page.locator('#window-30 .timeline-row:visible [data-person-link]:visible, #window-90 .timeline-row:visible [data-person-link]:visible').first().click();
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'false');
  const tickerLink = page.locator('#personPage [data-ticker-link]').first();
  assert.ok(await tickerLink.count() > 0);
  await tickerLink.click();
  assert.equal(await page.locator('#stockPage').getAttribute('aria-hidden'), 'false');
  assert.ok((await page.locator('.stock-ticker').textContent())?.trim());

  await page.locator('#stockBack').click();
  await page.locator('#personBack').click();
  await page.locator(`[data-ticker-link="${marketTicker}"]:visible`).first().click();
  assert.equal(await page.locator('#stockPage').getAttribute('aria-hidden'), 'false');
  assert.equal((await page.locator('.stock-ticker').textContent())?.trim(), marketTicker);
  assert.equal(await page.locator('.stock-price-chart').count(), 1);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    status: 'passed',
    people: data.people.length,
    transactions: data.transactions.length,
    reported_holdings: data.reported_holdings.length,
    market: data.security_market_data.length,
    sources: data.source_health.length,
    browser: browserExecutable(),
  }));
} finally {
  await browser.close();
}
