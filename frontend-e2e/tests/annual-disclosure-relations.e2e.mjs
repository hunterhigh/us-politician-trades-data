// Read actual rendered DATA; never insert or modify browser-side disclosures.
import assert from 'node:assert/strict';
import {existsSync,writeFileSync,readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const source=process.env.ANNUAL_RELATIONS_HTML;
assert.ok(source&&existsSync(source),'ANNUAL_RELATIONS_HTML required');
const text=readFileSync(source,'utf8');
const match=text.match(/const DATA = (\{.*\});\r?\nconst IS_DEMO =/s);
assert.ok(match,'Rendered DATA required');
const data=JSON.parse(match[1]);
const relations=data.meta.transaction_disclosure_relations||[];
const executablePath=[process.env.CHROME_PATH,'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));
const browser=await chromium.launch({executablePath,headless:true});
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(source).href);
  const checked=[];
  // Check every disclosure relation and reuse the drawer to detect stale notes.
  assert.ok(relations.length,'At least one annual disclosure relation required');
  for(const relation of relations){
    assert.equal(data.transactions.filter(t=>t.id===relation.transaction_id).length,1);
    await page.evaluate(id=>openTransaction(id),relation.transaction_id);
    const note=page.locator('[data-cross-report-transaction-id]');
    assert.equal(await note.count(),1);
    assert.equal(await note.getAttribute('data-cross-report-transaction-id'),relation.transaction_id);
    assert.equal(await note.getAttribute('data-disclosure-relationship-status'),relation.status);
    assert.ok((await note.innerText()).includes('独立年报披露'));
    assert.ok((await note.innerText()).includes('按披露行计数'));
    assert.ok((await note.innerText()).includes(relation.status==='possible_ptr_overlap'
      ?`可能关联 ${relation.possible_ptr_transaction_ids.length} 条PTR披露`:'跨报关系未确认'));
    checked.push({transaction_id:relation.transaction_id,status:relation.status});
    await page.evaluate(()=>closeDrawer());
  }
  const normal=data.transactions.find(t=>!relations.some(r=>r.transaction_id===t.id));
  if(normal){
    await page.evaluate(id=>openTransaction(id),normal.id);
    assert.equal(await page.locator('[data-cross-report-transaction-id]').count(),0);
  }
  assert.deepEqual(errors,[]);
  const receipt={kind:process.env.ANNUAL_RELATIONS_KIND||'rendered_contract_check',
    annual_relation_count:relations.length,statuses_checked:checked,normal_ptr_unchanged:Boolean(normal),script_errors:errors};
  if(process.env.ANNUAL_RELATIONS_RECEIPT)writeFileSync(process.env.ANNUAL_RELATIONS_RECEIPT,JSON.stringify(receipt,null,2));
  console.log(JSON.stringify(receipt));
} finally {await browser.close();}
