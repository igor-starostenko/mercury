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
 * .page-range label, so the header keeps one stable shape on every page.
 * Transactions only — the account count moved out of the header per
 * sr1us (the per-account table on the Overview page already shows it). */
function totalsSub(s, fallback) {
  const t = s && s.n_transactions;
  return t
    ? `${t} transaction${t === 1 ? '' : 's'}`
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
/* Two views on every cash-flow bar chart, switchable per chart:
 *   'category' — stacked by category (default): one segment per category,
 *                spend and income each in their own stack, so the
 *                per-category trend is readable across months/weeks.
 *   'totals'   — grouped Spend vs Income bars (the original behavior).
 * The choice is persisted in localStorage per chart (modeKey), so it
 * survives data re-renders and page reloads.
 */
const FLOW_MAX_CATEGORIES = 8; // top N categories as segments; the tail
                               // merges into one "All other" segment
const ALL_OTHER = 'All other';

function flowMode(key) {
  try {
    return (localStorage.getItem('mercury.flowMode.' + key) === 'totals')
      ? 'totals' : 'category';
  } catch (e) { return 'category'; }
}

function setFlowMode(key, mode) {
  try { localStorage.setItem('mercury.flowMode.' + key, mode); } catch (e) {}
}

/* Wire a .seg toggle (static HTML) to a chart's mode. Called once per
 * page; `redraw` re-draws the chart from the page's cached config. */
function wireFlowToggle(segId, redraw) {
  const seg = $id(segId);
  if (!seg) return;
  const key = seg.dataset.key;
  const paint = () => {
    const mode = flowMode(key);
    seg.querySelectorAll('button').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === mode));
  };
  seg.querySelectorAll('button').forEach(b => {
    b.addEventListener('click', () => {
      if (b.dataset.mode === flowMode(key)) return;
      setFlowMode(key, b.dataset.mode);
      paint();
      redraw();
    });
  });
  paint();
}

/* Category color: the standard palette first, then a stable hash for
 * custom categories (rule pickers allow free names) so stacked
 * segments don't all land on the grey fallback. */
function catColor(name) {
  if (CAT_COLORS[name]) return CAT_COLORS[name];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return 'hsl(' + h + ', 48%, 58%)';
}

function withAlpha(color, a) {
  if (color[0] === '#') {
    const n = parseInt(color.slice(1), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' +
           (n & 255) + ',' + a + ')';
  }
  return color; // hashed hsl fallback: no easy alpha conversion
}

/* Stacked-view plan: order categories by total volume across ALL
 * buckets (deterministic, so the stack never reorders between buckets)
 * and fold the tail beyond FLOW_MAX_CATEGORIES into "All other". */
function flowStackPlan(byCategory) {
  const agg = new Map();
  for (const r of byCategory || []) {
    const a = agg.get(r.category) || { spend: 0, income: 0 };
    a.spend += r.spend || 0;
    a.income += r.income || 0;
    agg.set(r.category, a);
  }
  const all = [...agg.entries()].sort((x, y) =>
    (y[1].spend + y[1].income) - (x[1].spend + x[1].income));
  const keep = all.slice(0, FLOW_MAX_CATEGORIES).map(e => e[0]);
  const merged = all.slice(FLOW_MAX_CATEGORIES).map(e => e[0]);
  return { keep, merged };
}

/* cfg: {
 *   labels:     display labels for the x axis,
 *   keys:       bucket keys aligned with labels ('YYYY-MM', or the ISO
 *               week Monday 'YYYY-MM-DD' on the month page),
 *   spend:      zero-filled total spend per bucket (totals view + the
 *               stacked view's tooltip footer),
 *   income:     zero-filled total income per bucket,
 *   byCategory: [{key, category, spend, income}] rows for the stacked
 *               view (no row for a (bucket, category) without activity),
 *   modeKey:    localStorage key for the view toggle,
 *   onBar:      optional (barIndex) => void
 * } */
function makeFlowChart(canvas, cfg) {
  // Re-renders (onDataChanged) reuse the same canvas: destroy the old
  // chart first, or Chart.js throws "Canvas is already in use" and the
  // whole re-render aborts before the later panels update.
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  const options = {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', align: 'end',
        labels: { color: '#8a90a2', usePointStyle: true, boxWidth: 8, boxHeight: 8,
          // Stacked mode: a category active in BOTH spend and income gets
          // two same-label datasets (one per stack) — show one legend
          // entry per name (toggle still hits the first dataset).
          generateLabels: chart => {
            const items = Chart.defaults.plugins.legend.labels.generateLabels(chart);
            const seen = new Set();
            return items.filter(it => seen.has(it.text) ? false : (seen.add(it.text), true));
          } } },
      tooltip: { callbacks: { label: c => ' ' + c.dataset.label + ': ' + fmtMoney(c.raw) } },
    },
    scales: {
      x: { ticks: { color: '#8a90a2' }, grid: { display: false } },
      y: { beginAtZero: true,
        ticks: { color: '#8a90a2', callback: v => fmtAxis(v) },
        grid: { color: '#202634' } },
    },
  };
  const stacked = flowMode(cfg.modeKey) === 'category' &&
    (cfg.byCategory || []).length > 0;
  let datasets;
  if (!stacked) {
    datasets = [
      { label: 'Spend', data: cfg.spend,
        backgroundColor: 'rgba(255,107,107,.78)', hoverBackgroundColor: '#ff6b6b',
        borderRadius: 4, maxBarThickness: 38 },
      { label: 'Income', data: cfg.income,
        backgroundColor: 'rgba(56,193,114,.85)', hoverBackgroundColor: '#38c172',
        borderRadius: 4, maxBarThickness: 38 },
    ];
  } else {
    // Per-dataset `stack` only groups bars; the SCALES must also be
    // stacked or the segments render side-by-side (grouped), not piled.
    options.scales.x.stacked = true;
    options.scales.y.stacked = true;
    const plan = flowStackPlan(cfg.byCategory);
    // The merge label must never collide with a real category name —
    // "Other" is one of the standard categories.
    const names = [...plan.keep, ...(plan.merged.length ? [ALL_OTHER] : [])];
    // One dataset per (category, flow) that has any activity; spend and
    // income sit in separate stacks so each starts at zero.
    const series = (name, side) => cfg.keys.map(k => {
      let v = 0;
      for (const r of cfg.byCategory) {
        if (r.key !== k) continue;
        if (name === ALL_OTHER ? !plan.merged.includes(r.category)
                             : r.category !== name) continue;
        v += r[side] || 0;
      }
      return Math.round(v * 100) / 100;
    });
    datasets = [];
    for (const name of names) {
      const color = name === ALL_OTHER ? '#57606f' : catColor(name);
      for (const side of ['spend', 'income']) {
        const data = series(name, side);
        if (data.every(v => v === 0)) continue;
        datasets.push({
          label: name, data, stack: side,
          backgroundColor: withAlpha(color, .82),
          hoverBackgroundColor: color,
          maxBarThickness: 60,
        });
      }
    }
    // Per-bucket grand totals in the tooltip footer.
    options.plugins.tooltip.callbacks.footer = items =>
      'Spend: ' + fmtMoney(cfg.spend[items[0].dataIndex]) +
      '   ·   Income: ' + fmtMoney(cfg.income[items[0].dataIndex]);
  }
  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels: cfg.labels, datasets },
    options,
  });
  if (cfg.onBar) chart.options.onClick = (evt, els) => { if (els.length) cfg.onBar(els[0].index); };
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
          wrap._txns = txns;
          wrap._sortState = { key: 'date', dir: 'desc' };
          wrap._detailRender = () => {
            const st = wrap._sortState;
            wrap.innerHTML =
              '<div class="detail-head"><span>' + txns.length +
              ' transaction' + (txns.length === 1 ? '' : 's') +
              '</span><span>' + txnSortLabel(st) + '</span></div>' +
              txnTableHTML(sortTxns(txns, st), { sort: st });
            wireCategorySelects(wrap);
          };
          wrap._detailRender();
          wireTxnSort(wrap);
          wrap.dataset.loaded = '1';
        } catch (e) {
          wrap.innerHTML = '<p class="empty" style="padding:14px">' + esc(e.message) + '</p>';
        }
      }
    });
  });
}

/* ----------------------------------------------------------- transaction table */
/* HTML for a transactions table. Rows: {date, source, description, category, amount, id}.
 * Date/Description/Amount headers are clickable (opts.sortable, default true);
 * opts.sort ({key, dir}) renders the active indicator. All scoped rows are
 * already loaded, so sorting is client-side — no API round-trip per click.
 * Per-container state lives on the element itself (_txns, _sortState). */
const TXN_SORTS = {
  date: {
    def: 'desc', // newest first, the pre-existing default
    cmp: (a, b) => a.date.localeCompare(b.date),
    labels: { asc: 'oldest first', desc: 'newest first' },
  },
  description: {
    def: 'asc',
    cmp: (a, b) => a.description.localeCompare(b.description, undefined, { numeric: true }),
    labels: { asc: 'A–Z', desc: 'Z–A' },
  },
  amount: {
    def: 'desc', // largest first
    cmp: (a, b) => a.amount - b.amount,
    labels: { asc: 'smallest first', desc: 'largest first' },
  },
};
function sortTxns(txns, state) {
  const rule = TXN_SORTS[state.key] || TXN_SORTS.date;
  const mul = state.dir === 'asc' ? 1 : -1;
  return txns.slice().sort((a, b) => rule.cmp(a, b) * mul || a.id - b.id);
}
function txnSortLabel(state) {
  return (TXN_SORTS[state.key] || TXN_SORTS.date).labels[state.dir];
}
/* Sortable <th> shared by every transaction table (main lists, drill-downs,
 * Categories page). st {key, dir} renders the active arrow indicator. */
function txnSortTh(key, label, extra, st, sortable) {
  if (sortable === false) return extra ? `<th class="${extra}">${label}</th>` : `<th>${label}</th>`;
  const active = st && st.key === key;
  const arrow = active ? (st.dir === 'asc' ? '▲' : '▼') : '';
  return `<th class="${extra ? extra + ' ' : ''}sortable" data-sortkey="${key}" title="Sort by ${label.toLowerCase()} — click again to flip direction">${label}<span class="sort-ind">${arrow}</span></th>`;
}
function txnTableHTML(txns, opts = {}) {
  if (!txns.length) return '<p class="empty">No transactions.</p>';
  const editable = opts.editable !== false;
  const sortable = opts.sortable !== false;
  const st = opts.sort;
  const qre = opts.search ? txnSearchRegex(opts.search) : null;
  const th = (key, label, extra) => txnSortTh(key, label, extra, st, sortable);
  return `
    <table>
      <thead><tr>
        ${th('date', 'Date')}<th>Source</th>${th('description', 'Description')}
        ${editable ? '<th>Category</th>' : ''}
        ${th('amount', 'Amount', 'num')}
        ${editable ? '<th style="width:34px"></th>' : ''}
      </tr></thead>
      <tbody>${txns.map(t => `
        <tr class="${t.ignored ? 'txn-ignored' : ''}">
          <td>${esc(t.date)}</td>
          <td>${esc(t.source)}</td>
          <td class="desc" title="${esc(t.description)}">${highlightDesc(t.description, qre)}</td>
          ${editable ? `<td><select class="cat-select" data-id="${t.id}">${catOptions(t.category)}</select>${t.ignored ? '<span class="ignored-tag">ignored — not counted in totals</span>' : ''}${setByBadge(t)}${ruleNoteHTML(t)}</td>` : ''}
          <td class="num ${t.amount < 0 ? 'neg' : 'pos'}">${fmtMoney(t.amount)}</td>
          ${editable ? `<td class="num"><button class="txn-del" data-id="${t.id}" title="Delete (soft — restorable)" onclick="deleteTxn(${t.id}, this)">×</button></td>` : ''}
        </tr>`).join('')}
      </tbody>
    </table>`;
}

/* Mount a transactions table into a dedicated container with working sort
 * headers. The source rows and sort state are kept on the element, so a
 * sort click rebuilds the table without losing either. Safe to rebuild the
 * whole table: category <select>s commit on change, nothing is pending. */
function mountTxnTable(container, txns, opts = {}) {
  container._txns = txns;
  if (!container._sortState) container._sortState = { key: 'date', dir: 'desc' };
  container._editable = opts.editable;
  renderTxnTable(container);
}
function renderTxnTable(container) {
  const all = container._txns || [];
  const rows = sortTxns(all.filter(t => txnSearchMatch(t, container._search || '')),
                        container._sortState);
  container._lastShown = rows.length;
  container._lastTotal = all.length;
  const q = (container._search || '').trim();
  container.innerHTML = rows.length
    ? txnTableHTML(rows, { sort: container._sortState,
                           editable: container._editable,
                           search: container._search || '' })
    : (q
        ? `<p class="empty">No matches for “${esc(q)}” — ${all.length} transaction${all.length === 1 ? '' : 's'} in this period hidden by the search.</p>`
        : '<p class="empty">No transactions.</p>');
  wireCategorySelects(container);
  wireTxnSort(container);
}
/* Click a sortable header: same column flips direction, new column starts
 * at its natural direction (TXN_SORTS[key].def). Idempotent per <th> node
 * (renders may wire the same fresh header twice — e.g. a _detailRender
 * that wires, plus the click handler's re-wire — which would flip twice). */
function wireTxnSort(root) {
  root.querySelectorAll('th[data-sortkey]').forEach(th => {
    if (th._sortWired) return;
    th._sortWired = true;
    th.addEventListener('click', () => {
      const key = th.dataset.sortkey;
      const cur = root._sortState;
      root._sortState = cur.key === key
        ? { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: TXN_SORTS[key].def };
      if (root._detailRender) { root._detailRender(); wireTxnSort(root); }
      else renderTxnTable(root);
    });
  });
}

/* --------------------------------- transaction search */
/* Search uses exactly the same matching as category rules (app/rules.py):
 * case-insensitive partial substring on the whitespace-normalized
 * description. The rows shown for a query are therefore precisely the
 * rows a rule with that pattern would catch — a dry run of a proposed
 * rule before it is saved. Matching runs on the normalized text;
 * highlighting re-derives the same match on the raw text (whitespace
 * runs as \s+), so <mark> lines up with what is shown. */
function txnSearchNormalize(text) {
  return String(text == null ? '' : text).replace(/\s+/g, ' ').trim().toLowerCase();
}

function txnSearchRegex(q) {
  const norm = txnSearchNormalize(q);
  if (!norm) return null;
  const src = norm.split(' ')
    .map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('\\s+');
  return new RegExp(src, 'gi');
}

function txnSearchMatch(row, q) {
  const nq = txnSearchNormalize(q);
  return !nq || txnSearchNormalize(row.description).includes(nq);
}

function highlightDesc(text, re) {
  const raw = text == null ? '' : String(text);
  if (!re) return esc(raw);
  re.lastIndex = 0;
  let out = '', last = 0, m;
  while ((m = re.exec(raw))) {
    out += esc(raw.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
    last = m.index + m[0].length;
    if (!m[0].length) re.lastIndex++;
  }
  return out + esc(raw.slice(last));
}

/* Per-row rule verdict, from the rule_match/rule_matches/set_by fields
 * the API computes with the same code as the effective_transactions
 * view: which enabled rule this description matches (first in priority
 * order = the one that would classify it), whether that rule is the
 * effective source, and how many lower-priority rules it shadows. */
function ruleNoteHTML(t) {
  if (!t || !t.rule_match) return '';
  const m = t.rule_match;
  const active = t.set_by === 'rule';
  const shadowed = Math.max(0, (t.rule_matches || 1) - 1);
  const who = t.set_by === 'manual' ? 'you' : t.set_by;
  const title = active
    ? `Category comes from rule “${m.pattern}” (first match in priority order wins).`
      + (shadowed
         ? ` ${shadowed} lower-priority rule${shadowed > 1 ? 's' : ''} also match${shadowed > 1 ? '' : 'es'} this description.`
         : '')
    : `Matches rule “${m.pattern}” → ${m.category}, but the ${who} category wins (precedence: you > rule > LLM > CSV).`;
  const extra = active
    ? (shadowed ? ` · +${shadowed} shadowed` : '')
    : ` · overridden (${who} wins)`;
  return `<span class="rule-note${active ? ' rule-note-active' : ''}" title="${esc(title)}">${esc(`rule “${m.pattern}” → ${m.category}`)}${extra}</span>`;
}

/* Small provenance badge for the "All transactions" table — same
 * vocabulary as the "Set by" column of the categorization list. */
function setByBadge(t) {
  const map = { manual: 'you', llm: 'LLM', rule: 'rule' };
  if (!t || !map[t.set_by]) return '';
  const titles = {
    manual: 'Category set by you (manual override)',
    llm: 'Category set by the LLM auto-classify',
    rule: 'Category set by a custom rule (applied at read time)',
  };
  return `<span class="badge src-${esc(t.set_by)} setby-inline" title="${esc(titles[t.set_by])}">${map[t.set_by]}</span>`;
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
/* Keyword-in-description -> category, applied at read time. Data: {rules}.
   Rules targeting the reserved "Ignored" category live in their own box
   (renderIgnoreRules) — easier to find than a dropdown option.
   Both panels share ONE global priority order (the server evaluates
   rules across both lists, first match wins), so rows are draggable
   anywhere — including across the two panels — and the resulting
   global order is persisted via POST /api/rules/reorder. */

let _rulesOrder = [];        // global rule ids, highest priority first
let _rulesDataCache = null;  // last ruleData payload, for re-render on failure
let _dragRuleId = null;      // rule being dragged (dataTransfer is opaque
                             // during dragover, so track it ourselves)

function _rulesRefreshData(rulesData) {
  _rulesOrder = rulesData.rules.map(r => r.id);
  _rulesDataCache = rulesData;
}

function _clearRuleDropMarks() {
  for (const tr of document.querySelectorAll('.rule-row'))
    tr.classList.remove('drop-before', 'drop-after', 'dragging');
}

function _ruleDropTarget(e) {
  // Which side of the hovered row does the pointer indicate?
  const tr = e.currentTarget;
  const rect = tr.getBoundingClientRect();
  const before = e.clientY < rect.top + rect.height / 2;
  _clearRuleDropMarks();
  tr.classList.add(before ? 'drop-before' : 'drop-after');
  return before;
}

function ruleDragStart(e) {
  const tr = e.currentTarget;
  _dragRuleId = Number(tr.dataset.ruleId);
  tr.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  // Firefox requires data to start a drag.
  e.dataTransfer.setData('text/plain', String(_dragRuleId));
}

function ruleDragOver(e) {
  if (_dragRuleId === null) return;
  e.preventDefault();           // without this the drop won't fire
  e.dataTransfer.dropEffect = 'move';
  _ruleDropTarget(e);
}

function ruleDragLeave() {
  _clearRuleDropMarks();
}

function ruleDragEnd() {
  _clearRuleDropMarks();
  _dragRuleId = null;
}

function ruleDrop(e) {
  e.preventDefault();
  const toId = Number(e.currentTarget.dataset.ruleId);
  const before = _ruleDropTarget(e);
  _clearRuleDropMarks();
  const fromId = _dragRuleId;
  _dragRuleId = null;
  if (fromId === null || fromId === toId) return;
  reorderRules(_moveRuleId(_rulesOrder, fromId, toId, before));
}

/* Pure: move `fromId` so it sits just before/after `toId`. */
function _moveRuleId(order, fromId, toId, before) {
  const next = order.filter(id => id !== fromId);
  const idx = next.indexOf(toId);
  if (idx === -1) return order;
  next.splice(before ? idx : idx + 1, 0, fromId);
  return next;
}

async function reorderRules(orderedIds) {
  try {
    await api('/api/rules/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ordered_ids: orderedIds }),
    });
    _rulesOrder = orderedIds;
    toast('Rule order updated');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Reorder failed: ' + err.message + ' — order restored', 5000);
    if (_rulesDataCache) {
      renderRules(_rulesDataCache);
      renderIgnoreRules(_rulesDataCache);
    }
  }
}

function renderRules(data) {
  const el = $id('rulesPanel');
  if (!el) return;
  _rulesRefreshData(data);
  const rules = data.rules.filter(r => r.category !== 'Ignored');
  const list = rules.length
    ? '<table><thead><tr>' +
      '<th style="width:30px"></th>' +
      '<th>Keyword</th><th>→ Category</th><th class="num">Matches</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      rules.map(r => `
        <tr class="rule-row" draggable="true" data-rule-id="${r.id}"
            ondragstart="ruleDragStart(event)" ondragover="ruleDragOver(event)"
            ondragleave="ruleDragLeave(event)" ondrop="ruleDrop(event)"
            ondragend="ruleDragEnd(event)">
          <td class="rule-drag" title="Drag to change priority">⠿</td>
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
        ${CATS.filter(c => c !== 'Ignored').map(c => `<option>${esc(c)}</option>`).join('')}
      </select>
      <button type="submit">Add rule</button>
    </form>
    ${list}`;
}

/* Dedicated "Ignore transactions" box: pattern rules whose matches are
   excluded from every total (reserved category "Ignored"). Rows stay
   visible in lists, dimmed and tagged. */
function renderIgnoreRules(data) {
  const el = $id('ignoreRulesPanel');
  if (!el) return;
  _rulesRefreshData(data);
  const rules = data.rules.filter(r => r.category === 'Ignored');
  const list = rules.length
    ? '<table><thead><tr>' +
      '<th style="width:30px"></th>' +
      '<th>Keyword</th><th>Effect</th><th class="num">Matches</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      rules.map(r => `
        <tr class="rule-row" draggable="true" data-rule-id="${r.id}"
            ondragstart="ruleDragStart(event)" ondragover="ruleDragOver(event)"
            ondragleave="ruleDragLeave(event)" ondrop="ruleDrop(event)"
            ondragend="ruleDragEnd(event)">
          <td class="rule-drag" title="Drag to change priority">⠿</td>
          <td class="desc" title="${esc(r.pattern)}">${esc(r.pattern)}</td>
          <td><span class="badge badge-ignored" title="Matching rows are excluded from all totals but stay visible">⊘ Ignored</span></td>
          <td class="num">${r.matches}</td>
          <td class="num"><button class="rule-del" title="Delete rule" onclick="deleteRule(${r.id})">×</button></td>
        </tr>`).join('') +
      '</tbody></table>'
    : '<p class="empty" style="padding:14px">No ignore rules yet — add one above.</p>';
  el.innerHTML = `
    <form class="rule-form" onsubmit="addIgnoreRule(event)">
      <input id="ignorePattern" type="text" maxlength="200" required
             placeholder="Keyword, e.g. 'internal transfer' or 'to savings'">
      <button type="submit">Ignore matches</button>
    </form>
    ${list}`;
}

async function addIgnoreRule(e) {
  e.preventDefault();
  const pattern = $id('ignorePattern').value.trim();
  try {
    await api('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, category: 'Ignored' }),
    });
    $id('ignorePattern').value = '';
    toast('Ignore rule added');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Could not add ignore rule: ' + err.message, 5000);
  }
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
