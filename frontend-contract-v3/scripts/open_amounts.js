// Approved rule: all disclosures count; only bounded rows enter amount ceilings.
function approvedIsOpen(row) {
  return row.amount_high === null && typeof row.amount_low_exclusive === 'boolean';
}
function approvedAmountValue(row, side) {
  return approvedIsOpen(row) ? 0 : Number(row['amount_' + side] || 0);
}
function approvedAmountRange(row) {
  if (!approvedIsOpen(row)) return `${money(row.amount_low)}–${money(row.amount_high)}`;
  return `${row.amount_low_exclusive ? 'Over ' : 'At least '}${money(row.amount_low)} · upper limit not disclosed`;
}
function approvedTotals(rows) {
  return {low: rows.reduce((n,t) => n + approvedAmountValue(t,'low'),0),
          high: rows.reduce((n,t) => n + approvedAmountValue(t,'high'),0),
          boundedCount: rows.filter(t => !approvedIsOpen(t)).length,
          openRows: rows.filter(approvedIsOpen)};
}
function approvedAggregateRange(aggregate, compact = false) {
  const format = compact ? compactMoney : money;
  const bounded = aggregate.boundedCount === 0 ? 'No bounded amounts' : `${format(aggregate.low)}–${format(aggregate.high)}`;
  const opened = aggregate.openRows || [];
  if (!opened.length) return bounded;
  return `${bounded} (bounded only); ${opened.length} open-ended disclosure${opened.length === 1 ? '' : 's'} listed separately`;
}
function windowSummary(txs) {
  const lags = txs.map(t=>t.disclosure_lag_days).sort((a,b)=>a-b);
  return {people:new Set(txs.map(t=>t.person_id)).size,
          buys:txs.filter(t=>t.transaction_type==='purchase').length,
          sells:txs.filter(t=>t.transaction_type==='sale').length,
          ...approvedTotals(txs),median:lags.length?lags[Math.floor(lags.length/2)]:0};
}
function directionAggregate(txs, direction) {
  const items = txs.filter(t=>t.transaction_type===direction);
  if (!items.length) return null;
  const latest = latestOf(items);
  return {count:items.length,people:new Set(items.map(t=>t.person_id)).size,
          ...approvedTotals(items),tradeDate:latest.transaction_date,filedAt:latest.filed_at};
}
function securitiesByAmount(txs, personId, direction) {
  const groups = new Map();
  txs.forEach(t=>{
    if(t.person_id!==personId||t.transaction_type!==direction||!t.ticker)return;
    if(!groups.has(t.ticker))groups.set(t.ticker,[]);
    groups.get(t.ticker).push(t);
  });
  return [...groups].map(([ticker,items])=>({...directionAggregate(items,direction),ticker}))
    .filter(row=>row.boundedCount>0)
    .sort((a,b)=>b.high-a.high||b.low-a.low||b.boundedCount-a.boundedCount||a.ticker.localeCompare(b.ticker));
}
function buyConsensus(txs, limit) {
  const groups = new Map();
  txs.forEach(t=>{
    if(t.transaction_type!=='purchase'||!t.ticker)return;
    if(!groups.has(t.ticker))groups.set(t.ticker,[]);
    groups.get(t.ticker).push(t);
  });
  return [...groups].map(([ticker,items])=>{
    const people = new Map();
    items.forEach(t=>{if(!people.has(t.person_id))people.set(t.person_id,[]);people.get(t.person_id).push(t);});
    const buyers = [...people].map(([personId,rows])=>({personId,...directionAggregate(rows,'purchase')}))
      .filter(row=>row.boundedCount>0)
      .sort((a,b)=>b.high-a.high||b.low-a.low||b.boundedCount-a.boundedCount||a.personId.localeCompare(b.personId));
    return {ticker,company:items[0].asset_name,buyers,buyerCount:people.size,agg:directionAggregate(items,'purchase')};
  }).filter(row=>row.agg.boundedCount>0)
    .sort((a,b)=>b.agg.high-a.agg.high||b.agg.low-a.agg.low||b.agg.boundedCount-a.agg.boundedCount||a.ticker.localeCompare(b.ticker))
    .slice(0,limit);
}
function approvedOpenPanel(container, key, rows) {
  if (!container) return;
  const old = container.querySelector(`[data-open-amount-panel="${key}"]`);
  if(old)old.remove();
  const opened = rows.filter(approvedIsOpen);
  if(!opened.length)return;
  const panel=document.createElement('section');
  panel.className='person-card';panel.dataset.openAmountPanel=key;
  panel.style.cssText='padding:16px;margin:16px 0;overflow-wrap:anywhere';
  const heading=document.createElement('h3');heading.textContent='Open-ended disclosed amounts';panel.append(heading);
  const note=document.createElement('p');
  note.textContent=`${opened.length} disclosures included in transaction counts. Bounded amount totals and amount-ceiling rankings exclude these records; no upper limits are assumed.`;
  panel.append(note);
  const list=document.createElement('ul');
  opened.slice().sort((a,b)=>b.transaction_date.localeCompare(a.transaction_date)||a.id.localeCompare(b.id)).forEach(row=>{
    const item=document.createElement('li');item.dataset.openAmountId=row.id;
    const text=document.createElement('span');
    text.textContent=`${PEOPLE[row.person_id]?.display_name || row.person_id} · ${row.asset_name} · ${row.transaction_type} · ${row.transaction_date} · ${approvedAmountRange(row)} `;
    const source=document.createElement('a');source.href=row.source_url;source.target='_blank';source.rel='noopener noreferrer';source.textContent='Official filing';
    item.append(text,source);list.append(item);
  });panel.append(list);container.append(panel);
}
function render() {
  renderApprovedBase();
  DASHBOARD_WINDOWS.forEach(days=>approvedOpenPanel(document.querySelector(`#window-${days}`),`window-${days}`,windowTransactions(days)));
}
function renderPersonPage() {
  renderApprovedPersonBase();
  if(!activePersonId)return;
  let rows=windowTransactions(activeWindow).filter(t=>t.person_id===activePersonId);
  if(!rows.length)rows=DATA.transactions.filter(t=>t.person_id===activePersonId);
  if(personDirection!=='all')rows=rows.filter(t=>t.transaction_type===personDirection);
  approvedOpenPanel(document.querySelector('#personPage .person-main')||document.querySelector('#personPage'),'person',rows);
  approvedHoldingsPanel();
}
function approvedHoldingsPanel() {
  const container=document.querySelector('#personPage .person-main')||document.querySelector('#personPage');
  if(!container)return;
  container.querySelector('[data-open-holdings-panel]')?.remove();
  const all=DATA.reported_holdings.filter(h=>h.person_id===activePersonId);
  const period=all.map(h=>h.report_period_end).sort().at(-1);
  const latest=all.filter(h=>h.report_period_end===period);
  const isOpen=h=>h.value_high===null&&typeof h.value_low_exclusive==='boolean';
  const opened=latest.filter(isOpen), bounded=latest.filter(h=>!isOpen(h));
  if(!latest.length)return;
  const coverage=(DATA.meta.holding_report_coverage||[]).find(c=>c.person_id===activePersonId&&c.report_period_end===period);
  const panel=document.createElement('section');panel.className='person-card';
  panel.dataset.openHoldingsPanel='true';panel.style.cssText='padding:16px;margin:16px 0';
  const heading=document.createElement('h3');heading.textContent='Reported holdings';panel.append(heading);
  const note=document.createElement('p');
  const boundedText=bounded.length?`${bounded.length} bounded values total ${money(bounded.reduce((s,h)=>s+h.value_low,0))}–${money(bounded.reduce((s,h)=>s+h.value_high,0))}`:'No bounded holding values disclosed';
  note.textContent=`${latest.length} holdings · report period ${period}. ${boundedText}. ${opened.length} open-ended holding values listed separately; no upper limits are assumed.`;
  panel.append(note);
  if(coverage){
    const warning=document.createElement('p');warning.dataset.partialHoldingsCoverage='true';
    warning.textContent=`部分报告，另 ${coverage.unresolved_rows.length} 项价值未定。以上仅为已披露有限区间合计，不是完整总资产；不与旧报告混算。`;
    panel.append(warning);
    coverage.unresolved_rows.forEach(row=>{
      const unknown=document.createElement('p');unknown.dataset.unresolvedHoldingId=row.extraction_id;
      unknown.textContent=`${row.asset_name} · ${row.value_raw}（价值未定，未计入持仓金额）`;
      panel.append(unknown);
    });
  }
  const addRows=(parent,rows)=>rows.forEach(h=>{
    const item=document.createElement('p');item.dataset.holdingId=h.id;
    const value=isOpen(h)?`${h.value_low_exclusive?'Over':'At least'} ${money(h.value_low)} · upper limit not disclosed`:`${money(h.value_low)}–${money(h.value_high)}`;
    item.textContent=`${h.asset_name} · ${h.owner} · ${value} `;
    const source=document.createElement('a');source.href=h.source_url;source.target='_blank';source.rel='noopener noreferrer';source.textContent='Official filing';item.append(source);parent.append(item);
  });
  addRows(panel,opened);
  const details=document.createElement('details'),summary=document.createElement('summary');
  details.open=!opened.length;
  summary.textContent=`${bounded.length} holdings with bounded disclosed values`;details.append(summary);
  addRows(details,bounded);panel.append(details);container.append(panel);
}
function renderStockPage() {
  renderApprovedStockBase();
  if(!activeStockTicker)return;
  let rows=windowTransactions(activeWindow).filter(t=>t.ticker===activeStockTicker);
  if(stockDirection!=='all')rows=rows.filter(t=>t.transaction_type===stockDirection);
  approvedOpenPanel(document.querySelector('#stockPage'),'stock',rows);
}
