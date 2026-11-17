/* Mercury — shared UI components.
 *
 * Pages embed the shared chrome via:
 *   <div id="site-header"></div> ... <div id="modal-root"></div>
 * and call renderChrome('home'|'categories'|'year'|'month') before their first paint.
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
      <div class="brand-text"><h1>Mercury</h1><div class="sub" id="range-sub"></div></div>
      </div>
      <nav>
        <a href="/" class="${active === 'home' ? 'active' : ''}">Overview</a>
        <a href="/categories" class="${active === 'categories' ? 'active' : ''}">Categories</a>
        <a href="/year" class="${active === 'year' ? 'active' : ''}">Year</a>
        <a href="/month" class="${active === 'month' ? 'active' : ''}">Month</a>
      </nav>
      <div class="header-actions">
        <button onclick="pickFiles()">Import CSV files</button>
        <button onclick="pickFolder()">Import folder</button>
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
  if (!el) return;
  el.textContent = text;
  el.title = text; // full text on hover (sub may be ellipsized)
}

/* Header sub: scoped totals only. The date range lives in the in-page
 * .page-range label, so the header keeps one stable shape on every page. */
function totalsSub(s, fallback) {
  const t = s && s.n_transactions, a = s ? s.n_accounts : 0;
  return t
    ? `${t} transaction${t === 1 ? '' : 's'} · ${a} account${a === 1 ? '' : 's'}`
    : (fallback || '');
}

/* In-page scoped date range (top of page content): "first → last". */
function setPageRange(first, last) {
  const el = $id('page-range');
  if (!el) return;
  el.textContent = first && last ? `${first} → ${last}` : '';
}

/* -------------------------------------------------------------------- upload */
let PENDING_FILES = new Map(); // filename -> File, kept so files that need a
                               // bank pick can be re-sent after the user chooses
function pickFiles() { const i = $id('filePick'); i.value = ''; i.click(); }
function pickFolder() { const i = $id('folderPick'); i.value = ''; i.click(); }

async function uploadFiles(list) {
  const files = [...(list || [])].filter(f => /\.(csv|txt)$/i.test(f.name));
  if (!files.length) { toast('No CSV files in that selection', 4000); return; }
  PENDING_FILES = new Map(files.map(f => [f.name, f]));
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
    const src = f.source
      ? esc(f.source) + (f.note && f.note !== 'filename'
          ? ` <span class="hint">(${esc(f.note)})</span>` : '')
      : '—';
    return `<tr><td title="${esc(f.name)}">${esc(name)}</td><td>${src}</td>
      <td class="num">${f.error ? '—' : f.rows}</td><td class="num">${f.error ? '—' : f.new}</td>
      <td class="num">${f.error ? '—' : d}</td>
      <td>${f.error ? `<span class="err">${esc(f.error)}</span>` : ''}</td></tr>`;
  }).join('');
  const table =
    '<table><thead><tr><th>File</th><th>Source</th><th class="num">Rows</th><th class="num">Added</th>' +
    '<th class="num">Duplicates</th><th>Error</th></tr></thead><tbody>' + rows + '</tbody></table>';
  const needSrc = res.files.filter(f => f.needs_source);
  let picker = '';
  if (needSrc.length) {
    const pickRows = needSrc.map(f => {
      const name = f.name.length > 34 ? '…' + f.name.slice(-33) : f.name;
      const banks = (f.banks && f.banks.length) ? f.banks :
        ['Wells Fargo', 'US Bank', 'Apple Card', 'Chase'];
      return `<tr><td title="${esc(f.name)}">${esc(name)}</td>
        <td><select class="bank-pick" data-name="${esc(f.name)}">
          <option value="">Choose bank…</option>
          ${banks.map(b => `<option>${esc(b)}</option>`).join('')}
        </select></td></tr>`;
    }).join('');
    picker = `<div class="bank-ask">
      <p>These files have no recognizable bank name — pick which bank they
      belong to:</p>
      <table><tbody>${pickRows}</tbody></table>
      <div class="bank-ask-foot">
        <button class="primary" onclick="importWithBanks()">Import with bank</button>
      </div></div>`;
  }
  $id('uploadTableWrap').innerHTML = picker + table;
  $id('uploadTotals').textContent =
    `${added} added · ${dups} duplicate${dups === 1 ? '' : 's'} · ${res.total} total` +
    (skipped.length ? ` · skipped: ${skipped.join(', ')}` : '');
  $id('uploadModal').style.display = 'flex';
}

/* Re-send the files waiting on a bank pick, with the chosen banks. */
async function importWithBanks() {
  const sels = [...document.querySelectorAll('.bank-pick')];
  const missing = sels.filter(s => !s.value).map(s => s.dataset.name);
  if (missing.length) {
    toast('Pick a bank for: ' + missing.join(', '), 6000);
    return;
  }
  const map = {};
  const fd = new FormData();
  for (const s of sels) {
    const f = PENDING_FILES.get(s.dataset.name);
    if (!f) continue;
    map[s.dataset.name] = s.value;
    fd.append('files', f);
  }
  if (!fd.getAll('files').length) {
    toast('Original files no longer in memory — pick them again', 6000);
    return;
  }
  fd.append('source_map', JSON.stringify(map));
  toast('Importing…', 30000);
  try {
    showUpload(await api('/api/upload', { method: 'POST', body: fd }));
  } catch (e) {
    toast('Import failed: ' + e.message, 8000);
  }
  if (window.onDataChanged) setTimeout(onDataChanged, 400);
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
        ${editable ? '<th style="width:34px"></th>' : ''}
      </tr></thead>
      <tbody>${txns.map(t => `
        <tr class="${t.ignored ? 'txn-ignored' : ''}">
          <td>${esc(t.date)}</td>
          <td>${esc(t.source)}</td>
          <td class="desc" title="${esc(t.description)}">${esc(t.description)}</td>
          ${editable ? `<td><select class="cat-select" data-id="${t.id}">${catOptions(t.category)}</select>${t.ignored ? '<span class="ignored-tag">ignored — not counted in totals</span>' : ''}</td>` : ''}
          <td class="num ${t.amount < 0 ? 'neg' : 'pos'}">${fmtMoney(t.amount)}</td>
          ${editable ? `<td class="num"><button class="txn-del" data-id="${t.id}" title="Delete (soft — restorable)" onclick="deleteTxn(${t.id}, this)">×</button></td>` : ''}
        </tr>`).join('')}
      </tbody>
    </table>`;
}

/* Soft-delete a transaction: hidden everywhere, imported row untouched,
 * restorable via POST /api/transactions/{id}/restore. */
async function deleteTxn(id, btn) {
  if (!confirm('Delete this transaction?\n(Soft delete — the import stays in the database and it can be restored.)')) return;
  if (btn) { btn.closest('tr').style.opacity = '.35'; }
  try {
    await api('/api/transactions/' + id, { method: 'DELETE' });
    toast('Transaction deleted');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (e) {
    toast('Delete failed: ' + e.message, 5000);
    if (btn) btn.closest('tr').style.opacity = '';
  }
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
/* Keyword-in-description -> category, applied at read time. Data: {rules}. */
function renderRules(data) {
  const el = $id('rulesPanel');
  if (!el) return;
  const list = data.rules.length
    ? '<table><thead><tr>' +
      '<th>Keyword</th><th>→ Category</th><th class="num">Matches</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      data.rules.map(r => `
        <tr class="${r.category === 'Ignored' ? 'rule-ignored' : ''}">
          <td class="desc" title="${esc(r.pattern)}">${esc(r.pattern)}</td>
          <td>${r.category === 'Ignored'
            ? '<span class="badge badge-ignored" title="Matching rows are excluded from all totals but stay visible">⊘ Ignored</span>'
            : `<span class="dot" style="background:${CAT_COLORS[r.category] || '#57606f'}"></span>${esc(r.category)}`}</td>
          <td class="num">${r.matches}</td>
          <td class="num"><button class="rule-del" title="Delete rule" onclick="deleteRule(${r.id})">×</button></td>
        </tr>`).join('') +
      '</tbody></table>'
    : '<p class="empty" style="padding:14px">No rules yet — add one above.</p>';
  el.innerHTML = `
    <form class="rule-form" onsubmit="addRule(event)">
      <input id="rulePattern" type="text" maxlength="200" required
             placeholder="Keyword, e.g. 'walmart' or 'internal transfer'">
      <select id="ruleCategory" required>
        ${CATS.map(c => `<option>${esc(c)}</option>`).join('')}
      </select>
      <button type="submit">Add rule</button>
    </form>
    ${list}`;
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
