(() => {
  const source = PUBLISHED_SENATE_FRESHNESS;
  const date = source.data_cutoff_at.slice(0, 10);
  const notice = `Senate 数据截至 ${date}` + (source.unavailable
    ? '，官方站点暂时无法访问' : '，来源尚未补齐最新数据');
  function panel(scope) {
    const section = document.createElement('section');
    section.dataset.sourceFreshness = scope;
    section.dataset.sourceId = 'senate_efd';
    section.style.cssText = 'margin:16px 0;padding:14px;border:1px solid var(--line,#ddd);font-size:12px;line-height:1.6';
    const title = document.createElement('strong');title.textContent = notice;
    const note = document.createElement('p');
    note.textContent = '30/90 天窗口等统计照常计算，Senate 部分不完整。各来源使用各自的真实截止时间。';
    section.append(title, note);return section;
  }
  (document.querySelector('footer') || document.body).append(panel('report'));
  function windowPanels() {
    document.querySelectorAll('[id^="window-"]').forEach(container => {
      if (!/^window-\d+$/.test(container.id)) return;
      container.querySelector('[data-source-freshness]')?.remove();
      container.append(panel(container.id));
    });
  }
  if (typeof render === 'function') {
    const original = render;
    render = function(...args) { const result = original.apply(this, args);windowPanels();return result; };
  }
  function personPanel() {
    document.querySelector('[data-source-freshness="person"]')?.remove();
    const id = typeof activePersonId !== 'undefined' && activePersonId;
    if (!id) return;
    const person = typeof PEOPLE !== 'undefined' && PEOPLE[id];
    const involved = person?.disclosure_authority === 'senate_efd' ||
      [...DATA.transactions, ...DATA.reported_holdings].some(row => row.person_id === id && row.source_id === 'senate_efd');
    if (!involved) return;
    const container = document.querySelector('#personPage .person-main') ||
      document.querySelector('#personPageContent') || document.querySelector('#personPage');
    if (container) container.prepend(panel('person'));
  }
  if (typeof renderPersonPage === 'function') {
    const original = renderPersonPage;
    renderPersonPage = function(...args) { const result = original.apply(this, args);personPanel();return result; };
  }
  personPanel();windowPanels();
})();
