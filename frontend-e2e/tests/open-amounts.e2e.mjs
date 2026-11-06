import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
// The normal offline browser suite builds its own synthetic fixture; an explicit
// HTML path remains available for a separately generated fixed-case check.
const root = resolve(import.meta.dirname, '../..');
const temporary = process.env.OPEN_AMOUNTS_HTML ? null : mkdtempSync(join(tmpdir(), 'unison-open-amounts-'));
const input = process.env.OPEN_AMOUNTS_HTML || join(temporary, 'dashboard.html');
let browser;
try {
  if (temporary) {
    const candidates = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
    const python = candidates.find(command => spawnSync(command, ['--version']).status === 0);
    assert.ok(python, 'Python 3 is required for the approved amount fixture');
    const code = `import sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'backend/tests'))
sys.path.insert(0,str(root/'backend/src'))
from test_disclosure_amounts import fixture
from unison_snapshot.legacy import load
processor=load('process_snapshot',version='v2')
renderer=load('render_dashboard',version='v2')
Path(sys.argv[2]).write_text(renderer.render_html(processor.build_snapshot(fixture())),encoding='utf-8')`;
    const generated = spawnSync(python, [...(python === 'py' ? ['-3'] : []), '-X', 'utf8', '-c', code, root, input], {cwd: root, encoding: 'utf8'});
    assert.equal(generated.status, 0, `Approved fixture failed: ${generated.stdout}\n${generated.stderr}`);
  }
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.CHROME_PATH,
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean).find(existsSync);
  assert.ok(executablePath, 'No supported system Chromium executable was found');
  browser = await chromium.launch({headless: true, executablePath});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(/^https?:/, route => route.abort());
  await page.goto(pathToFileURL(resolve(input)).href, { waitUntil: 'domcontentloaded' });
  const checks = await page.evaluate(() => ({
    transactions: DATA.transactions.length,
    holdings: DATA.reported_holdings.length,
    unboundedTransactions: DATA.transactions.some(r => r.amount_high == null),
    unboundedHoldings: DATA.reported_holdings.some(r => r.value_high == null),
    exclusions: DATA.meta.known_gaps.excluded_open_amounts,
    text: document.querySelector('[data-known-gaps="report"]').textContent,
    openPanels: document.querySelectorAll('[data-open-amount-panel], [data-open-holdings-panel]').length
  }));
  assert.equal(checks.transactions, 1);
  assert.equal(checks.holdings, 1);
  assert.equal(checks.unboundedTransactions, false);
  assert.equal(checks.unboundedHoldings, false);
  assert.equal(checks.openPanels, 0);
  assert.deepEqual(checks.exclusions.transactions[0].record_ids, ['open-transaction']);
  assert.deepEqual(checks.exclusions.reported_holdings[0].record_ids, ['open-holding']);
  assert.match(checks.text, /Over \$50,000,000/);
  assert.match(checks.text, /Over \$1,000,000/);
  assert.deepEqual(errors, []);
  console.log('Finite publication arrays and separate original-threshold known gaps passed in unchanged Skill.');
} finally {
  await browser?.close();
  if (temporary) rmSync(temporary, {recursive: true, force: true});
}
