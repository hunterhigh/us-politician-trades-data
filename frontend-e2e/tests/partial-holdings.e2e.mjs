// Runs on the normal v3-rendered fixed-input page, with no page-side data edits.
import assert from 'node:assert/strict';
import {existsSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const source=process.env.PARTIAL_HOLDINGS_HTML;
assert.ok(source && existsSync(source),'PARTIAL_HOLDINGS_HTML required');
const executablePath=[process.env.CHROME_PATH,'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));
const browser=await chromium.launch({executablePath,headless:true});
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(source).href);
  await page.evaluate(()=>openPerson('house:F000246'));
  const panel=page.locator('[data-open-holdings-panel]');
  await panel.waitFor({state:'visible'});
  assert.equal(await panel.locator('[data-holding-id]').count(),107);
  assert.equal(await panel.locator('[data-holding-id]:visible').count(),107);
  assert.equal(await panel.locator('[data-unresolved-holding-id]').count(),1);
  const text=await panel.innerText();
  assert.ok(text.includes('$6,854,107')&&text.includes('$21,385,000'),text);
  assert.ok(text.includes('部分报告，另 1 项价值未定'),text);
  assert.ok(text.includes('Employee Retirement System of TEXAS')&&text.includes('Undetermined'),text);
  assert.ok(text.includes('不是完整总资产')&&text.includes('不与旧报告混算'),text);
  assert.deepEqual(errors,[]);
  const receipt={visible_holdings:107,unresolved_values:1,finite_low:6854107,finite_high:21385000,
    partial_warning:true,unknown_literal_preserved:true,script_errors:errors};
  if(process.env.PARTIAL_HOLDINGS_RECEIPT)writeFileSync(process.env.PARTIAL_HOLDINGS_RECEIPT,JSON.stringify(receipt,null,2));
  console.log(JSON.stringify(receipt));
} finally {await browser.close();}
