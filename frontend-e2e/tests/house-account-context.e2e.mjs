// Check source-account names from the independently verified candidate receipt.
import assert from 'node:assert/strict';
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const source=process.env.HOUSE_ACCOUNT_HTML,expectedPath=process.env.HOUSE_ACCOUNT_EXPECTATIONS;
assert.ok(source&&existsSync(source)&&expectedPath&&existsSync(expectedPath));
const expected=JSON.parse(readFileSync(expectedPath,'utf8')).asset_name_differences;
assert.equal(expected.length,13);
const executablePath=[process.env.CHROME_PATH,'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));
const browser=await chromium.launch({executablePath,headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(source).href,{waitUntil:'domcontentloaded'});
  for(const row of expected){
    assert.equal(await page.evaluate(id=>DATA.transactions.find(t=>t.id===id)?.asset_name,row.id),row.asset_name_after);
    await page.evaluate(id=>openTransaction(id),row.id);
    assert.ok((await page.locator('#drawerContent').innerText()).includes(row.asset_name_after),row.id);
    assert.equal(await page.locator('[data-cross-report-transaction-id]').count(),0);
    await page.evaluate(()=>closeDrawer());
  }
  assert.deepEqual(errors,[]);
  const result={kind:'fixed_native_source_account_display',source_account_titles_checked:expected.length,
    original_ids_retained:true,ordinary_ptr_has_no_annual_relation_note:true,script_errors:errors};
  if(process.env.HOUSE_ACCOUNT_RECEIPT)writeFileSync(process.env.HOUSE_ACCOUNT_RECEIPT,JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
}finally{await browser.close();}
