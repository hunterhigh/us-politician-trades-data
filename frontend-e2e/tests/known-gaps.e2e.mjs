import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const root = resolve(import.meta.dirname, '../..');
const temporary = mkdtempSync(join(tmpdir(), 'unison-known-gaps-'));
let browser;
try {
  const python = (process.platform === 'win32' ? ['python','py'] : ['python3','python'])
    .find(command => spawnSync(command, ['--version']).status === 0);
  assert.ok(python);
  const code = `import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'backend/src'),str(root/'backend/tests')]
from test_known_gaps import fixture
from test_public_repo import FakeTransport
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.legacy import load
_,_,bundle=fixture()
snapshot=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch().snapshot
data=load('process_snapshot',version='v3').build_snapshot(snapshot)
Path(sys.argv[2]).write_text(load('render_dashboard',version='v3').render_html(data),encoding='utf-8')`;
  const html = join(temporary, 'report.html');
  const generated = spawnSync(python, [...(python === 'py' ? ['-3'] : []), '-X','utf8','-c',code,root,html], {cwd:root,encoding:'utf8'});
  assert.equal(generated.status,0,generated.stdout+generated.stderr);
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.CHROME_PATH,
    '/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium','/usr/bin/chromium-browser',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean).find(existsSync);
  assert.ok(executablePath);
  browser = await chromium.launch({headless:true,executablePath});
  const page = await browser.newPage({viewport:{width:1360,height:900}});
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^https?:/,route=>route.abort());
  await page.goto(pathToFileURL(html).href,{waitUntil:'domcontentloaded'});
  const footer = page.locator('footer [data-known-gaps="report"]');
  assert.equal(await footer.count(),1);
  assert.match(await footer.innerText(),/3 quarantined rows.*row count unknown for 1 reports/s);
  await footer.locator('details').first().locator('summary').click();
  assert.match(await footer.innerText(),/transaction_date_invalid: 1 reports/);
  await page.evaluate(()=>openPerson(DATA.people[0].id));
  const person = page.locator('[data-known-gaps="person"]');
  assert.equal(await person.count(),1);
  assert.equal(await person.isVisible(),true);
  assert.match(await person.innerText(),/source inventory at this snapshot/);
  await page.evaluate(()=>renderPersonPage());
  assert.equal(await person.count(),1);
  assert.deepEqual(errors,[]);
  if (process.env.KNOWN_GAPS_ARTIFACT_DIR) {
    mkdirSync(process.env.KNOWN_GAPS_ARTIFACT_DIR,{recursive:true});
    await person.screenshot({path:join(process.env.KNOWN_GAPS_ARTIFACT_DIR,'person-gaps.png')});
  }
  console.log(JSON.stringify({known_gaps:'passed',footer:true,person:true,unknown_counts_preserved:true,page_errors:errors}));
} finally {
  if (browser) await browser.close();
  rmSync(temporary,{recursive:true,force:true});
}
