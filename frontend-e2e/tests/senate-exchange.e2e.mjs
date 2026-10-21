import assert from 'node:assert/strict';
import {existsSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright-core');
const source=process.env.SENATE_EXCHANGE_HTML;
assert.ok(source&&existsSync(source));
const executablePath=[process.env.CHROME_PATH,'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));
const browser=await chromium.launch({executablePath,headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(source).href,{waitUntil:'domcontentloaded'});
  const id='senate-ptr:6ecb6584a127dd783559dfbb';
  const tx=await page.evaluate(id=>DATA.transactions.find(t=>t.id===id),id);
  assert.equal(tx.ticker,null);assert.equal(tx.ticker_mapping_basis,null);
  assert.equal(tx.transaction_type,'exchange');assert.equal(tx.owner,'Spouse');
  assert.equal(tx.underlying_return_since_trade,null);
  await page.evaluate(id=>openTransaction(id),id);
  const text=await page.locator('#drawerContent').innerText();
  for(const value of ['unmapped ticker','Berry Global Group','Amcor plc','Spouse','$100,001','$250,000','2025-04-30'])assert.ok(text.includes(value),value);
  assert.equal(await page.locator('#drawerContent [data-ticker-link="AMCR"]').count(),0);
  assert.equal(await page.locator('[data-cross-report-transaction-id]').count(),0);
  assert.deepEqual(errors,[]);
  const receipt={kind:'fixed_official_two_ended_exchange',transaction_id:id,ticker:null,
    source_asset_sides_visible:true,assigned_to_receiver_ticker:false,missing_return:null,script_errors:errors};
  if(process.env.SENATE_EXCHANGE_RECEIPT)writeFileSync(process.env.SENATE_EXCHANGE_RECEIPT,JSON.stringify(receipt,null,2));
  console.log(JSON.stringify(receipt));
}finally{await browser.close();}
