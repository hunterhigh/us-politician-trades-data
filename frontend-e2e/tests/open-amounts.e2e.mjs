import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
assert.ok(process.env.OPEN_AMOUNTS_HTML, 'OPEN_AMOUNTS_HTML is required');
const browser = await chromium.launch({ headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^https?:/, route => route.abort());
  await page.goto(pathToFileURL(resolve(process.env.OPEN_AMOUNTS_HTML)).href, { waitUntil: 'domcontentloaded' });
  const checks = await page.evaluate(() => {
    const summary = windowSummary(DATA.transactions);
    const personId = DATA.people[0].id;
    const opened = DATA.transactions.find(approvedIsOpen);
    const ranked = securitiesByAmount([...DATA.transactions, { ...opened, id: 'rank-only-check', ticker: 'OPENONLY' }], personId, 'purchase');
    return { count: summary.buys + summary.sells, low: summary.low, high: summary.high,
      openCount: summary.openRows.length, ranked: ranked.map(r => r.ticker),
      upper: opened.amount_high, returnValue: opened.underlying_return_since_trade,
      panels: [...document.querySelectorAll('[data-open-amount-panel]')].map(e => e.dataset.openAmountPanel) };
  });
  assert.deepEqual(checks, { count: 2, low: 1000001, high: 5000000, openCount: 1,
    ranked: ['EXM'], upper: null, returnValue: null, panels: ['window-30', 'window-90'] });
  await page.evaluate(() => openPerson(DATA.people[0].id));
  assert.match(await page.locator('[data-open-holdings-panel]').innerText(), /2 holdings.*1 bounded values total \$1,001–\$15,000/s);
  assert.match(await page.locator('[data-open-holdings-panel]').innerText(), /Spouse · Over \$1,000,000 · upper limit not disclosed/);
  await page.locator('[data-open-holdings-panel] summary').click();
  assert.equal(await page.locator('[data-open-holdings-panel] [data-holding-id]').count(), 2);
  await page.evaluate(() => openTicker('EXM'));
  assert.match(await page.locator('[data-open-amount-panel="stock"]').innerText(), /Over \$50,000,000/);
  await page.evaluate(() => openTransaction('open-transaction'));
  assert.match(await page.locator('#drawer').innerText(), /Over \$50,000,000 · upper limit not disclosed/);
  assert.deepEqual(errors, []);
  console.log('Approved open transaction/holding amounts: counts, separate sums, rankings, person, security, drawer and null returns passed.');
} finally {
  await browser.close();
}
