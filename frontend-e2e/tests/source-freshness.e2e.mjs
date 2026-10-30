import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const root=resolve(import.meta.dirname,'../..');
const temporary=mkdtempSync(join(tmpdir(),'source-freshness-'));
let browser;
try {
  const python=process.env.PYTHON || (process.platform==='win32'?'python':'python3');
  const generated=spawnSync(python,['-X','utf8','-c',`import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'backend/src'),str(root/'backend/tests')]
from test_source_freshness import freshness_fixture
from test_public_repo import FakeTransport
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.legacy import load
for stale in (True,False):
 _,bundle=freshness_fixture(stale)
 snapshot=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch().snapshot
 data=load('process_snapshot',version='v3').build_snapshot(snapshot)
 Path(sys.argv[2],str(stale)+'.html').write_text(load('render_dashboard',version='v3').render_html(data),encoding='utf-8')`,root,temporary],{cwd:root,encoding:'utf8'});
  assert.equal(generated.status,0,generated.stdout+generated.stderr);
  const executablePath=[process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,process.env.CHROME_PATH,
    '/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean).find(existsSync);
  assert.ok(executablePath);
  browser=await chromium.launch({headless:true,executablePath});
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^https?:/,route=>route.abort());
  await page.goto(pathToFileURL(join(temporary,'True.html')).href,{waitUntil:'domcontentloaded'});
  const footer=page.locator('footer [data-source-freshness="report"]');
  assert.equal(await footer.count(),1);
  assert.match(await footer.innerText(),/Senate 数据截至 2026-09-18，官方站点暂时无法访问/);
  for(const days of [30,90]) {
    assert.match(await page.locator(`#window-${days} [data-source-freshness]`).innerText(),/Senate 部分不完整/);
  }
  const ids=await page.evaluate(()=>({senate:DATA.transactions.find(r=>r.source_id==='senate_efd').person_id,
    house:DATA.transactions.find(r=>r.source_id==='house_clerk').person_id}));
  await page.evaluate(id=>openPerson(id),ids.senate);
  const person=page.locator('[data-source-freshness="person"]');
  assert.equal(await person.isVisible(),true);assert.match(await person.innerText(),/2026-09-18/);
  await page.evaluate(()=>renderPersonPage());assert.equal(await person.count(),1);
  await page.evaluate(id=>openPerson(id),ids.house);assert.equal(await person.count(),0);
  await page.goto(pathToFileURL(join(temporary,'False.html')).href,{waitUntil:'domcontentloaded'});
  assert.equal(await page.locator('[data-source-freshness]').count(),0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({source_freshness:'passed',footer:true,senate_person:true,
    house_person_unchanged:true,windows:[30,90],automatic_recovery:true}));
} finally {if(browser)await browser.close();rmSync(temporary,{recursive:true,force:true});}
