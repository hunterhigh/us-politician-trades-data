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
assert.match(readFileSync(input, 'utf8'), /OFFICIAL DISCLOSURE DATA/);

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
  await page.locator('[data-person-link]').first().click();
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'false');
  const tickerLink = page.locator('#personPage [data-ticker-link]').first();
  if (await tickerLink.count()) {
    await tickerLink.click();
    assert.equal(await page.locator('#stockPage').getAttribute('aria-hidden'), 'false');
    assert.ok((await page.locator('.stock-ticker').textContent())?.trim());
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', browser: browserExecutable() }));
} finally {
  await browser.close();
}
