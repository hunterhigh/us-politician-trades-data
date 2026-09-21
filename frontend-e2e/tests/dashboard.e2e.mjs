import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const root = path.resolve(import.meta.dirname, '..', '..');
const fixtureDir = mkdtempSync(path.join(tmpdir(), 'unison-frontend-e2e-'));

function pythonCommand() {
  const candidates = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
  for (const command of candidates) {
    const probe = spawnSync(command, ['--version'], { encoding: 'utf8' });
    if (probe.status === 0) return { command, prefix: command === 'py' ? ['-3'] : [] };
  }
  throw new Error('Python 3 is required for the fixed-commit fixture');
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
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  const found = candidates.find(existsSync);
  if (!found) throw new Error('No supported system Chromium executable was found');
  return found;
}

async function visible(page, selector) {
  return page.locator(selector).evaluate(element => {
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none' && element.getClientRects().length > 0;
  });
}

const python = pythonCommand();
const build = spawnSync(
  python.command,
  [...python.prefix, path.join(root, 'frontend-e2e', 'build_fixture.py'), '--output', fixtureDir],
  { cwd: root, encoding: 'utf8' },
);
if (build.status !== 0) {
  throw new Error(`Fixture build failed\n${build.stdout}\n${build.stderr}`);
}

const fixture = JSON.parse(readFileSync(path.join(fixtureDir, 'fixture.json'), 'utf8'));
assert.match(fixture.commit, /^[0-9a-f]{40}$/);
assert.equal(fixture.is_demo, true);
assert.match(fixture.expected_errors.unknown_person, /not found/);
assert.match(fixture.expected_errors.unknown_ticker, /HTTP 404|No published tickers record/);
assert.match(fixture.expected_errors.corrupt_content, /hash/i);
const queries = JSON.parse(readFileSync(fixture.query_results, 'utf8'));
assert.equal(queries.person.status, 'ok');
assert.equal(queries.ticker.match_summary.transaction_record_count, 2);
assert.equal(queries.no_matches.status, 'no_matches');

const browser = await chromium.launch({
  executablePath: browserExecutable(),
  headless: true,
  args: ['--allow-file-access-from-files'],
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const runtimeErrors = [];
  page.on('pageerror', error => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') runtimeErrors.push(`console: ${message.text()}`);
  });
  await page.goto(pathToFileURL(fixture.modes.dashboard).href, { waitUntil: 'load' });

  assert.match(await page.title(), /White House Stock Tracker/);
  assert.equal(await page.locator('[data-window-block]').count(), 2);
  assert.equal(await page.locator('[data-window-view="hot-stocks"].active').count(), 2);

  await page.locator('[data-window-view="timeline"]').first().click();
  assert.equal(await page.locator('[data-window-view="timeline"].active').count(), 2);
  assert.equal(await visible(page, '#window-30 [data-window-panel="timeline"]'), true);

  const thirty = page.locator('#window-30');
  const ninety = page.locator('#window-90');
  const ninetyBefore = await ninety.locator('.timeline-row').count();
  await thirty.locator('.timeline-search input').fill('not-a-real-security');
  assert.equal(await thirty.locator('.timeline-row').count(), 0);
  assert.match(await thirty.locator('[id^="timelineCount"]').textContent(), /0 OF/);
  assert.equal(await ninety.locator('.timeline-row').count(), ninetyBefore);
  await thirty.locator('.timeline-search input').fill('ZZDEMO');
  assert.ok(await thirty.locator('.timeline-row').count() > 0);

  await thirty.locator('[data-window-panel="timeline"] [data-person-link]').first().click();
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'false');
  assert.match(await page.locator('.person-name').textContent(), /Demo Person/);
  await page.locator('#personPage [data-ticker-link="ZZDEMO"]').first().click();
  assert.equal(await page.locator('#stockPage').getAttribute('aria-hidden'), 'false');
  assert.match(await page.locator('.stock-ticker').textContent(), /ZZDEMO/);
  assert.match(await page.locator('#stockBack').textContent(), /返回 Demo Person/);
  await page.locator('#stockBack').click();
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'false');

  await page.locator('#personBack').click();
  assert.equal(await page.locator('#personPage').getAttribute('aria-hidden'), 'true');
  await thirty.locator('.timeline-search input').fill('');
  await thirty.locator('.timeline-row').first().evaluate(element => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  assert.equal(await page.locator('#drawer').getAttribute('aria-hidden'), 'false');
  assert.match(await page.locator('#drawerContent').textContent(), /SIMULATED DISCLOSURE/);
  assert.match(await page.locator('#drawerContent').textContent(), /证据状态/);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#drawer').getAttribute('aria-hidden'), 'true');

  await page.locator('[data-window-anchor="90"]').click();
  await page.waitForTimeout(100);
  assert.ok(await page.evaluate(() => window.scrollY > 0));
  assert.equal(await page.locator('[data-window-anchor="90"].active').count(), 1);

  for (const [mode, file] of Object.entries(fixture.modes)) {
    await page.goto(pathToFileURL(file).href, { waitUntil: 'load' });
    assert.equal(await page.locator('#cutoff').count(), 1, `${mode} did not render`);
  }
  assert.deepEqual(runtimeErrors, []);
  console.log(JSON.stringify({
    status: 'passed',
    fixed_commit: fixture.commit,
    modes: Object.keys(fixture.modes),
    browser: browserExecutable(),
  }));
} finally {
  await browser.close();
  rmSync(fixtureDir, { recursive: true, force: true });
}
