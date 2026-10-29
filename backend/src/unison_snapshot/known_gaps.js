(() => {
  const labels = {house_clerk:'House Clerk',senate_efd:'Senate eFD',oge:'OGE / White House'};
  function panel(sources, person = false, reportCount = null) {
    const section = document.createElement('section');
    section.dataset.knownGaps = person ? 'person' : 'report';
    section.style.cssText = 'margin:16px 0;padding:14px;border:1px solid var(--line,#ddd);font-size:12px;line-height:1.6';
    const title = document.createElement('strong');
    const counted = person && Number.isSafeInteger(reportCount) && reportCount > 0;
    title.textContent = counted ? `该人有 ${reportCount} 份披露未解析` : person ? 'Known gaps in this disclosure source' : 'Known data gaps';
    if (counted) section.dataset.personUnparsedCount = String(reportCount);
    section.append(title);
    const note = document.createElement('p');
    note.textContent = counted ? '含部分解析、隔离或尚未评估的披露；仅计已可靠绑定的报告。未绑定报告仍按来源整体统计。下方为来源整体边界，不是该人的缺失行数。' : 'Counts describe the source inventory at this snapshot’s cutoff. Coverage can be partial; missing row counts remain unknown.';
    section.append(note);
    for (const source of sources) {
      const item = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `${labels[source.source_id]} · ${source.scope_id}: ${source.quarantined_row_count} quarantined rows, ${source.partial_report_count} partial reports, ${source.quarantined_report_count} quarantined reports; row count unknown for ${source.unknown_row_count_report_count} reports`;
      item.append(summary);
      const scope = document.createElement('p');
      scope.textContent = `${source.report_count} inventory records · ${source.request_required_report_count} require a source request · ${source.unassessed_report_count} unassessed. ${source.affected_person_ids.length} people identified from published report bindings; ${source.unidentified_affected_report_count} affected reports lack a person binding. ${source.filed_date_range.length ? `Observed filing dates ${source.filed_date_range.join(' – ')}.` : 'Filing date range unavailable.'}`;
      item.append(scope);
      if (source.affected_person_ids.length) {
        const people = document.createElement('p');
        people.textContent = 'People: ' + source.affected_person_ids.map(id => typeof PEOPLE !== 'undefined' && PEOPLE[id]?.display_name || id).join(', ');
        item.append(people);
      }
      const list = document.createElement('ul');
      for (const [reason, count] of Object.entries(source.reason_report_counts)) {
        const line = document.createElement('li');line.textContent = `${reason}: ${count} reports`;list.append(line);
      }
      for (const limitation of source.limitations) {
        const line = document.createElement('li');line.textContent = limitation;list.append(line);
      }
      item.append(list);section.append(item);
    }
    return section;
  }
  const sources = PUBLISHED_KNOWN_GAPS.sources;
  (document.querySelector('footer') || document.body).append(panel(sources));
  function personPanel() {
    document.querySelector('[data-known-gaps="person"]')?.remove();
    const personId = typeof activePersonId !== 'undefined' && activePersonId;
    if (!personId) return;
    const person = typeof PEOPLE !== 'undefined' && PEOPLE[personId];
    const selected = sources.filter(s => s.affected_person_ids.includes(personId) || s.source_id === person?.disclosure_authority);
    const container = document.querySelector('#personPage .person-main') || document.querySelector('#personPageContent') || document.querySelector('#personPage');
    if (container && selected.length) {
      const count = PUBLISHED_KNOWN_GAPS.person_unparsed_report_counts?.[personId];
      const section = panel(selected, true, count);
      if (Number.isSafeInteger(count) && count > 0) container.prepend(section);
      else container.append(section);
    }
  }
  if (typeof renderPersonPage === 'function') {
    const original = renderPersonPage;
    renderPersonPage = function(...args) { const result = original.apply(this, args);personPanel();return result; };
  }
  personPanel();
})();
