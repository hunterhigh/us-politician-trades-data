import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const root = resolve(import.meta.dirname, '../..');
const temporary = mkdtempSync(join(tmpdir(), 'ticker-matching-'));
let browser;
try {
  const generated = spawnSync(process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), ['-X', 'utf8', '-c', `
import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'backend/src'),str(root/'frontend-e2e')]
from fixtures.public_repo import production_payload, FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.legacy import load
payload=production_payload()
payload['transactions'][0].update(ticker=None,ticker_mapping_basis=None)
bundle=build(payload,generated_at='2026-09-18T00:01:00Z',allow_production=True)
snapshot=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch().snapshot
assert snapshot['transactions']==payload['transactions']
data=load('process_snapshot',version='v2').build_snapshot(snapshot)
Path(sys.argv[2],'dashboard.html').write_text(load('render_dashboard',version='v2').render_html(data),encoding='utf-8')
`, root, temporary], { cwd: root, encoding: 'utf8' });
  assert.equal(generated.status, 0, generated.stdout + generated.stderr);
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.CHROME_PATH,
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean).find(existsSync);
  assert.ok(executablePath);
  browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(pathToFileURL(join(temporary, 'dashboard.html')).href);
  const counts = await page.evaluate(() => ({ total: DATA.transactions.length,
    unmatched: DATA.transactions.filter(row => !row.ticker).length,
    matched: DATA.transactions.filter(row => row.ticker).length }));
  assert.deepEqual(counts, { total: 2, unmatched: 1, matched: 1 });
  assert.ok(await page.locator('[data-ticker-status="unmatched"]').count() > 0);
  await page.evaluate(() => openPerson(DATA.transactions.find(row => !row.ticker).person_id));
  const unmatched = page.locator('.person-security[data-ticker-status="unmatched"]');
  assert.equal(await unmatched.count(), 1);
  assert.match(await unmatched.innerText(), /未匹配/);
  assert.equal(await page.locator('[data-ticker-link="null"]').count(), 0);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ticker_matching: 'passed', ...counts, page_errors: errors }));
} finally {
  if (browser) await browser.close();
  rmSync(temporary, { recursive: true, force: true });
}
