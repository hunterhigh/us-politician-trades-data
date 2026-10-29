// Inspect normal rendered disclosures. Expected values are frozen before rendering.
import assert from 'node:assert/strict';
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const source=process.env.OGE_ANNUAL_HTML, expectedPath=process.env.OGE_ANNUAL_EXPECTED;
assert.ok(source&&existsSync(source),'OGE_ANNUAL_HTML required');
assert.ok(expectedPath&&existsSync(expectedPath),'OGE_ANNUAL_EXPECTED required');
const expected=JSON.parse(readFileSync(expectedPath,'utf8'));
assert.equal(expected.source_id,'oge');
assert.ok(expected.person_id?.startsWith('oge:'));
assert.ok(Array.isArray(expected.annual_transaction_ids)&&expected.annual_transaction_ids.length);
assert.ok(Array.isArray(expected.published_holding_ids)&&expected.published_holding_ids.length);
assert.ok(Array.isArray(expected.unknown_value_literals)&&expected.unknown_value_literals.length);
const match=readFileSync(source,'utf8').match(/const DATA = (\{.*\});\r?\nconst IS_DEMO =/s);
assert.ok(match,'Normal rendered DATA required');
const data=JSON.parse(match[1]);
const executablePath=[process.env.CHROME_PATH,'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));
const browser=await chromium.launch({executablePath,headless:true});
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(source).href);
  for(const id of expected.annual_transaction_ids){
    const facts=data.transactions.filter(t=>t.id===id);
    assert.equal(facts.length,1);
    assert.equal(facts[0].source_id,'oge');
    const relation=(data.meta.transaction_disclosure_relations||[]).find(r=>r.transaction_id===id);
    assert.ok(relation,'OGE annual relation must survive normal rendering');
    await page.evaluate(value=>openTransaction(value),id);
    const note=page.locator('[data-cross-report-transaction-id]');
    assert.equal(await note.count(),1);
    assert.equal(await note.getAttribute('data-cross-report-transaction-id'),id);
    assert.equal(await note.getAttribute('data-disclosure-relationship-status'),relation.status);
    const text=await note.innerText();
    assert.ok(text.includes('独立年报披露')&&text.includes('按披露行计数'),text);
    await page.evaluate(()=>closeDrawer());
  }
  const normal=data.transactions.find(t=>!(data.meta.transaction_disclosure_relations||[])
    .some(r=>r.transaction_id===t.id));
  if(normal){
    await page.evaluate(id=>openTransaction(id),normal.id);
    assert.equal(await page.locator('[data-cross-report-transaction-id]').count(),0);
    await page.evaluate(()=>closeDrawer());
  }
  await page.evaluate(id=>openPerson(id),expected.person_id);
  const panel=page.locator('[data-open-holdings-panel]');
  await panel.waitFor({state:'visible'});
  assert.deepEqual((await panel.locator('[data-holding-id]').evaluateAll(nodes=>
    nodes.map(n=>n.getAttribute('data-holding-id')))).sort(),[...expected.published_holding_ids].sort());
  assert.equal(await panel.locator('[data-unresolved-holding-id]').count(),expected.unknown_value_literals.length);
  const text=await panel.innerText();
  for(const literal of expected.unknown_value_literals)assert.ok(text.includes(literal),text);
  for(const amount of expected.finite_total_labels)assert.ok(text.includes(amount),text);
  assert.ok(text.includes('部分报告')&&text.includes('不是完整总资产')&&text.includes('不与旧报告混算'),text);
  assert.deepEqual(errors,[]);
  const receipt={kind:expected.kind,annual_transaction_ids:expected.annual_transaction_ids,
    visible_holding_ids:expected.published_holding_ids,unknown_values:expected.unknown_value_literals.length,
    normal_ptr_without_annual_note:Boolean(normal),script_errors:errors};
  if(process.env.OGE_ANNUAL_RECEIPT)writeFileSync(process.env.OGE_ANNUAL_RECEIPT,JSON.stringify(receipt,null,2));
  console.log(JSON.stringify(receipt));
} finally {await browser.close();}
