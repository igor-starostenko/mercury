/* Mercury — shared UI components.
 *
 * Pages embed the shared chrome via:
 *   <div id="site-header"></div> ... <div id="modal-root"></div>
 * and call renderChrome('home'|'year'|'month') before their first paint.
 * After any data mutation (upload / classify / categorize) components call
 * window.onDataChanged, which each page sets to its own re-render.
 */

let CATS = []; // standard category names, filled by each page's init

/* ------------------------------------------------------------- page chrome */
function renderChrome(active) {
  $id('site-header').innerHTML = `
    <header>
      <div class="brand">
        <div class="mark">M</div>
        <div><h1>Mercury</h1><div class="sub" id="range-sub"></div></div>
      </div>
      <nav>
        <a href="/" class="${active === 'home' ? 'active' : ''}">Overview</a>
        <a href="/year" class="${active === 'year' ? 'active' : ''}">Year</a>
        <a href="/month" class="${active === 'month' ? 'active' : ''}">Month</a>
      </nav>
      <div class="header-actions">
        <button onclick="pickFiles()">Import CSV files</button>
        <button onclick="pickFolder()">Import folder</button>
        <button class="primary" onclick="runClassify()">Classify unknown (LLM)</button>
        <input type="file" id="filePick" accept=".csv,.txt" multiple hidden onchange="uploadFiles(this.files)">
        <input type="file" id="folderPick" webkitdirectory hidden onchange="uploadFiles(this.files)">
      </div>
    </header>`;
  $id('modal-root').innerHTML = `
    <div class="modal-backdrop" id="uploadModal" onclick="if(event.target===this)closeUpload()">
      <div class="modal">
        <h2>Import summary</h2>
        <div id="uploadTableWrap"></div>
        <div class="modal-foot">
          <span id="uploadTotals"></span>
          <button class="primary" onclick="closeUpload()">OK</button>
        </div>
      </div>
    </div>`;
}

function setRangeSub(text) {
  const el = $id('range-sub');
  if (el) el.textContent = text;
}

/* -------------------------------------------------------------------- upload */
function pickFiles() { const i = $id('filePick'); i.value = ''; i.click(); }
function pickFolder() { const i = $id('folderPick'); i.value = ''; i.click(); }

async function uploadFiles(list) {
  const files = [...(list || [])].filter(f => /\.(csv|txt)$/i.test(f.name));
  if (!files.length) { toast('No CSV files in that selection', 4000); return; }
  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  toast(`Importing ${files.length} file${files.length > 1 ? 's' : ''}…`, 120000);
  try {
    const res = await api('/api/upload', { method: 'POST', body: fd });
    showUpload(res);
  } catch (e) {
    toast('Import failed: ' + e.message, 8000);
  }
  if (window.onDataChanged) setTimeout(onDataChanged, 400);
}

function showUpload(res) {
  let added = 0, dups = 0, skipped = [];
  const rows = res.files.map(f => {
    const d = f.error ? 0 : f.rows - f.new;
    if (f.error) skipped.push(f.name); else { added += f.new; dups += d; }
    const name = f.name.length > 36 ? '…' + f.name.slice(-35) : f.name;
    return `<tr><td title="${esc(f.name)}">${esc(name)}</td><td>${f.source ? esc(f.source) : '—'}</td>
      <td class="num">${f.error ? '—' : f.rows}</td><td class="num">${f.error ? '—' : f.new}</td>
      <td class="num">${f.error ? '—' : d}</td>
      <td>${f.error ? `<span class="err">${esc(f.error)}</span>` : ''}</td></tr>`;
  }).join('');
  $id('uploadTableWrap').innerHTML =
    '<table><thead><tr><th>File</th><th>Source</th><th class="num">Rows</th><th class="num">Added</th>' +
    '<th class="num">Duplicates</th><th>Error</th></tr></thead><tbody>' + rows + '</tbody></table>';
  $id('uploadTotals').textContent =
    `${added} added · ${dups} duplicate${dups === 1 ? '' : 's'} · ${res.total} total` +
    (skipped.length ? ` · skipped: ${skipped.join(', ')}` : '');
  $id('uploadModal').style.display = 'flex';
}
function closeUpload() {
  const m = $id('uploadModal');
  if (m) m.style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeUpload(); });

/* ------------------------------------------------------------------- classify */
async function runClassify() {
  toast('Classifying…');
  try {
    const res = await api('/api/classify', { method: 'POST' });
    toast(`Classified ${res.classified} of ${res.of}`);
  } catch (e) {
    toast('Classify failed: ' + e.message, 6000);
  }
  if (window.onDataChanged) setTimeout(onDataChanged, 400);
}

/* -------------------------------------------------------------- flow chart */
/* Grouped bar chart: spend (red) vs income (green) per label. */
function makeFlowChart(canvas, labels, spend, income, onBar) {
  // Re-renders (onDataChanged) reuse the same canvas: destroy the old
  // chart first, or Chart.js throws "Canvas is already in use" and the
  // whole re-render aborts before the later panels update.
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'Spend', data: spend,
        backgroundColor: 'rgba(255,107,107,.78)', hoverBackgroundColor: '#ff6b6b',
        borderRadius: 4, maxBarThickness: 38 },
      { label: 'Income', data: income,
        backgroundColor: 'rgba(56,193,114,.85)', hoverBackgroundColor: '#38c172',
        borderRadius: 4, maxBarThickness: 38 },
    ]},
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', align: 'end',
          labels: { color: '#8a90a2', usePointStyle: true, boxWidth: 8, boxHeight: 8 } },
        tooltip: { callbacks: { label: c => ' ' + c.dataset.label + ': ' + fmtMoney(c.raw) } },
      },
      scales: {
        x: { ticks: { color: '#8a90a2' }, grid: { display: false } },
        y: { beginAtZero: true,
          ticks: { color: '#8a90a2', callback: v => fmtAxis(v) },
          grid: { color: '#202634' } },
      },
    },
  });
  if (onBar) chart.options.onClick = (evt, els) => { if (els.length) onBar(els[0].index); };
  return chart;
}

/* ------------------------------------------------------------ category table */
/* Rendered into a container:
 *   container: element (use a .panel.tight wrapper)
 *   catData:   [{category, spend, n}] from /api/categories
 *   scope:     {} | {year} | {month} — query scope for drill-downs
 */
async function renderCategoryTable(container, catData, scope) {
  if (!catData.length) {
    container.innerHTML = '<p class="empty">No spending in this scope.</p>';
    return;
  }
  const total = catData.reduce((a, d) => a + d.spend, 0) || 1;
  container.innerHTML = `
    <table class="cat-table">
      <thead><tr>
        <th style="width:28px"></th><th>Category</th><th class="num">Txns</th>
        <th class="num">Spent</th><th style="width:24%">Share</th>
      </tr></thead>
      <tbody>${catData.map((d, i) => {
        const color = CAT_COLORS[d.category] || '#57606f';
        const pct = (d.spend / total * 100).toFixed(1);
        return `
        <tr class="drill-row" data-i="${i}" data-cat="${esc(d.category)}">
          <td><span class="chev">▶</span></td>
          <td><span class="dot" style="background:${color}"></span>${esc(d.category)}</td>
          <td class="num">${d.n}</td>
          <td class="num">${fmtMoney(d.spend)}</td>
          <td><div class="share-bar"><i style="width:${pct}%;background:${color}"></i></div></td>
        </tr>
        <tr class="detail-row" id="det-${i}" style="display:none">
          <td colspan="5"><div class="detail-wrap" data-cat="${esc(d.category)}"></div></td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
  container.querySelectorAll('.drill-row').forEach(row => {
    row.addEventListener('click', async () => {
      const detail = container.querySelector('#det-' + row.dataset.i);
      const wrap = detail.querySelector('.detail-wrap');
      if (detail.style.display !== 'none') {
        detail.style.display = 'none'; row.classList.remove('open');
        return;
      }
      detail.style.display = ''; row.classList.add('open');
      if (!wrap.dataset.loaded) {
        wrap.innerHTML = '<p class="empty" style="padding:14px">Loading…</p>';
        const q = new URLSearchParams(scope);
        q.set('category', row.dataset.cat);
        q.set('limit', '500');
        try {
          const txns = await api('/api/transactions?' + q.toString());
          wrap.innerHTML =
            '<div class="detail-head"><span>' + txns.length +
            ' transaction' + (txns.length === 1 ? '' : 's') +
            '</span><span>newest first</span></div>' + txnTableHTML(txns);
          wrap.dataset.loaded = '1';
          wireCategorySelects(wrap);
        } catch (e) {
          wrap.innerHTML = '<p class="empty" style="padding:14px">' + esc(e.message) + '</p>';
        }
      }
    });
  });
}

/* ----------------------------------------------------------- transaction table */
/* HTML for a transactions table. Rows: {date, source, description, category, amount, id}. */
function txnTableHTML(txns, opts = {}) {
  if (!txns.length) return '<p class="empty">No transactions.</p>';
  const editable = opts.editable !== false;
  return `
    <table>
      <thead><tr>
        <th>Date</th><th>Source</th><th>Description</th>
        ${editable ? '<th>Category</th>' : ''}
        <th class="num">Amount</th>
      </tr></thead>
      <tbody>${txns.map(t => `
        <tr>
          <td>${esc(t.date)}</td>
          <td>${esc(t.source)}</td>
          <td class="desc" title="${esc(t.description)}">${esc(t.description)}</td>
          ${editable ? `<td><select class="cat-select" data-id="${t.id}">${catOptions(t.category)}</select></td>` : ''}
          <td class="num ${t.amount < 0 ? 'neg' : 'pos'}">${fmtMoney(t.amount)}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

function catOptions(sel) {
  const list = [...CATS];
  if (sel && !list.includes(sel)) list.push(sel);
  return list.map(c => `<option${c === sel ? ' selected' : ''}>${esc(c)}</option>`).join('');
}

/* Attach category-assignment handlers to selects inside root. */
function wireCategorySelects(root) {
  root.querySelectorAll('select.cat-select').forEach(sel => {
    sel.addEventListener('change', async () => {
      try {
        await api('/api/categorize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: [+sel.dataset.id], category: sel.value }),
        });
        toast('Category updated');
        if (window.onDataChanged) setTimeout(onDataChanged, 400);
      } catch (e) {
        toast('Update failed: ' + e.message, 5000);
      }
    });
  });
}

/* ------------------------------------------------------------- custom rules */
/* Keyword-in-description -> category bulk override. Data: {rules, would_change}. */
function renderRules(data) {
  const el = $id('rulesPanel');
  if (!el) return;
  const list = data.rules.length
    ? '<table><thead><tr>' +
      '<th>Keyword</th><th>→ Category</th><th class="num">Matches</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      data.rules.map(r => `
        <tr>
          <td class="desc" title="${esc(r.pattern)}">${esc(r.pattern)}</td>
          <td><span class="dot" style="background:${CAT_COLORS[r.category] || '#57606f'}"></span>${esc(r.category)}</td>
          <td class="num">${r.matches}</td>
          <td class="num"><button class="rule-del" title="Delete rule" onclick="deleteRule(${r.id})">×</button></td>
        </tr>`).join('') +
      '</tbody></table>'
    : '<p class="empty" style="padding:14px">No rules yet — add one above.</p>';
  el.innerHTML = `
    <form class="rule-form" onsubmit="addRule(event)">
      <input id="rulePattern" type="text" maxlength="200" required
             placeholder="Keyword, e.g. 'walmart'">
      <select id="ruleCategory" required>
        ${CATS.map(c => `<option>${esc(c)}</option>`).join('')}
      </select>
      <button type="submit">Add rule</button>
    </form>
    ${list}
    <div class="rule-apply">
      <button class="primary" ${data.rules.length ? '' : 'disabled'}
              onclick="applyRules()">Apply rules to all transactions${data.would_change ? ` — ${data.would_change} will change` : ''}</button>
      <span class="hint">Apply overwrites the current category of every matching row (CSV, LLM or manual).</span>
    </div>`;
}

async function addRule(e) {
  e.preventDefault();
  const pattern = $id('rulePattern').value.trim();
  const category = $id('ruleCategory').value;
  try {
    await api('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, category }),
    });
    $id('rulePattern').value = '';
    toast('Rule added');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Could not add rule: ' + err.message, 5000);
  }
}

async function deleteRule(id) {
  try {
    await api('/api/rules/' + id, { method: 'DELETE' });
    toast('Rule deleted');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Delete failed: ' + err.message, 5000);
  }
}

async function applyRules() {
  toast('Applying rules…');
  try {
    const res = await api('/api/rules/apply', { method: 'POST' });
    toast(`Rules applied — ${res.updated} transaction${res.updated === 1 ? '' : 's'} updated`);
  } catch (err) {
    toast('Apply failed: ' + err.message, 6000);
  }
  if (window.onDataChanged) setTimeout(onDataChanged, 400);
}
