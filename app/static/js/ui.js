/* Mercury — shared UI components.
 *
 * Pages embed the shared chrome via:
 *   <div id="site-header"></div> ... <div id="modal-root"></div>
 * and call renderChrome('home'|'categories'|'transactions'|'year'|'month')
 * before their first paint.
 * After any data mutation (upload / classify / categorize) components call
 * window.onDataChanged, which each page sets to its own re-render.
 */

let CATS = []; // standard category names, filled by each page's init

/* Theme-aware color for canvas charts: read the active scheme's token at
 * build time. The scheme itself is applied pre-paint (head script) and a
 * switch reloads the page, so every chart is always drawn with the
 * current tokens — categories keep their own fixed palette (CAT_COLORS). */
function cssColor(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* Skeleton placeholders: seed a container before its fetch so the
 * section's final height exists at first paint (no layout jump when the
 * data lands). kind 'value' = label + one big value (month/year cards);
 * kind 'rows' = label + three label/value rows (overview year cards).
 * The bars reuse the real .card/.row/.label/.value boxes, so the
 * reserved height equals the rendered height exactly. */
function skelCard(kind) {
  const bar = (w) => `<i class="skel-bar" style="width:${w}"></i>`;
  const rows = [0, 1, 2].map(() =>
    `<div class="row">${bar('42%')}${bar('20%')}</div>`).join('');
  return kind === 'rows'
    ? `<div class="card skel" aria-hidden="true"><div class="label">${bar('34%')}</div>${rows}</div>`
    : `<div class="card skel" aria-hidden="true"><div class="label">${bar('34%')}</div><div class="value">${bar('52%')}</div></div>`;
}
function skelCards(n, kind) {
  return Array.from({ length: n }, () => skelCard(kind)).join('');
}

/* ------------------------------------------------------------- page chrome */
function renderChrome(active) {
  $id('site-header').innerHTML = `
    <header>
      <div class="brand">
      <div class="mark" title="Hg — mercury, element 80">Hg</div>
      <div class="brand-text"><h1>Mercury</h1></div>
      </div>
      <nav>
        <a href="/" class="${active === 'home' ? 'active' : ''}">Overview</a>
        <a href="/categories" class="${active === 'categories' ? 'active' : ''}">Categories</a>
        <a href="/merchants" class="${active === 'merchants' ? 'active' : ''}">Merchants</a>
        <a href="/transactions" class="${active === 'transactions' ? 'active' : ''}">Transactions</a>
        <a href="/year" class="${active === 'year' ? 'active' : ''}">Year</a>
        <a href="/month" class="${active === 'month' ? 'active' : ''}">Month</a>
      </nav>
      <div class="header-actions">
        <div class="themepick" id="themePick">
          <button class="themepick-btn" id="themeBtn" onclick="toggleThemeMenu(event)"
                  aria-haspopup="true" aria-expanded="false" title="Color scheme">
            <span class="themepick-swatch" id="themeSwatch"></span>Theme
          </button>
          <div class="themepick-menu" id="themeMenu" role="menu" aria-label="Color scheme"></div>
        </div>
        <button class="primary" onclick="openImports()">Manage Imports</button>
        <input type="file" id="filePick" accept=".csv,.txt" multiple hidden onchange="uploadFiles(this.files)">
        <input type="file" id="folderPick" webkitdirectory hidden onchange="uploadFiles(this.files)">
      </div>
    </header>`;
  paintThemeSwatch();
  wireThemeMenu();
  $id('modal-root').innerHTML = `
    <div class="modal-backdrop" id="importsModal" onclick="if(event.target===this)closeImports()">
      <div class="modal" style="min-width:660px">
        <h2>Manage imports</h2>
        <div class="imports-tools">
          <button class="primary" onclick="pickFiles()">Import CSV files</button>
          <button onclick="pickFolder()">Import folder</button>
          <span class="hint">Each CSV becomes one import below — rename its
          account label (e.g. a second checking at the same bank) or delete
          it with all of its transactions. Rules are never touched.</span>
        </div>
        <div id="importsList" style="margin-top:14px"></div>
        <div class="modal-foot">
          <span id="importsTotals" class="hint"></span>
          <button onclick="closeImports()">Close</button>
        </div>
      </div>
    </div>
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

/* ------------------------------------------------------------- color schemes
 * Eight palettes for sr1us to evaluate (2026-11-20/21), 4 colors each —
 * the swatch order is the palette as given (bg / panel / accent / text).
 * Ember stays the default (kept after the 2026-11-20 evaluation); the
 * five added on 2026-11-21 are Clay, Harbor, Plum, Tide, Onyx — Plum,
 * Tide and Onyx are dark schemes (app.css flips color-scheme for them).
 * The active id lives in localStorage (mercury.theme); each page's head
 * script applies it before first paint (no flash of default). Picking
 * the default (Ember) removes the attribute and the saved value.
 * Switching reloads so charts and canvas redraws pick up the new tokens.
 * Category colors (CAT_COLORS in common.js) are separate and don't follow. */
const THEMES = {
  ember:      { label: 'Ember', default: true,
                colors: ['#EAEFEF', '#BFC9D1', '#25343F', '#FF9B51'] },
  frost:      { label: 'Frost',
                colors: ['#F8FAFC', '#D9EAFD', '#BCCCDC', '#9AA6B2'] },
  terracotta: { label: 'Terracotta',
                colors: ['#BF4646', '#EDDCC6', '#FFF4EA', '#7EACB5'] },
  clay:       { label: 'Clay',
                colors: ['#EEEDEB', '#E0CCBE', '#747264', '#3C3633'] },
  harbor:     { label: 'Harbor',
                colors: ['#F0F0F0', '#213555', '#4F709C', '#E5D283'] },
  plum:       { label: 'Plum',
                colors: ['#352F44', '#5C5470', '#B9B4C7', '#FAF0E6'] },
  tide:       { label: 'Tide',
                colors: ['#27374D', '#526D82', '#9DB2BF', '#DDE6ED'] },
  onyx:       { label: 'Onyx',
                colors: ['#393646', '#4F4557', '#6D5D6E', '#F4EEE0'] },
};
const THEME_KEY = 'mercury.theme';

function currentTheme() {
  let t = '';
  try { t = (localStorage.getItem(THEME_KEY) || '').toLowerCase(); } catch (e) {}
  return THEMES[t] ? t : 'ember';
}

function applyTheme(id) {
  if (!THEMES[id]) id = 'ember';
  const root = document.documentElement;
  try {
    if (id === 'ember') {
      root.removeAttribute('data-theme');
      localStorage.removeItem(THEME_KEY);
    } else {
      root.setAttribute('data-theme', id);
      localStorage.setItem(THEME_KEY, id);
    }
  } catch (e) {} // private mode: scheme still applies for this page load
  paintThemeSwatch();
}

function paintThemeSwatch() {
  const sw = $id('themeSwatch');
  if (!sw) return;
  sw.innerHTML = THEMES[currentTheme()].colors
    .map(c => `<i style="background:${c}"></i>`).join('');
}

function themeMenuHTML() {
  const cur = currentTheme();
  const items = Object.entries(THEMES).map(([id, t]) => `
    <div class="themepick-item${id === cur ? ' active' : ''}" role="menuitemradio"
         aria-checked="${id === cur}" onclick="pickTheme('${id}')">
      <span class="themepick-swatch">${t.colors.map(c => `<i style="background:${c}"></i>`).join('')}</span>
      <span class="themepick-name">${t.label}${t.default ? ' <span class="hint">· default</span>' : ''}</span>
      <span class="themepick-check">${id === cur ? '✓' : ''}</span>
    </div>`).join('');
  return `<div class="themepick-head">Color scheme</div>${items}`;
}

function toggleThemeMenu(ev) {
  ev && ev.stopPropagation();
  const menu = $id('themeMenu'), btn = $id('themeBtn');
  if (!menu || !btn) return;
  const open = menu.classList.toggle('open');
  if (open) menu.innerHTML = themeMenuHTML();
  btn.setAttribute('aria-expanded', String(open));
}

function pickTheme(id) {
  applyTheme(id);
  location.reload(); // redraw charts + canvases from the new tokens
}

/* Close-on-outside-click / Escape. renderChrome rebuilds the header DOM on
 * every page, so the document-level listeners are attached only once. */
let _themeMenuWired = false;
function wireThemeMenu() {
  if (_themeMenuWired) return;
  _themeMenuWired = true;
  document.addEventListener('click', e => {
    const pick = $id('themePick'), menu = $id('themeMenu');
    if (pick && menu && menu.classList.contains('open') && !pick.contains(e.target)) {
      menu.classList.remove('open');
      $id('themeBtn').setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const menu = $id('themeMenu');
    if (menu && menu.classList.contains('open')) {
      menu.classList.remove('open');
      $id('themeBtn').setAttribute('aria-expanded', 'false');
    }
  });
}

/* In-page scoped label (top of page content): date range + transaction
 * count, "first → last · N transactions". Both live here now — the
 * count moved out of the header per sr1us, so the header keeps one
 * stable shape on every page. note is the pre-formatted count or a
 * fallback ("no transactions in 2026"); empty parts are skipped. */
function setPageRange(first, last, note) {
  const el = $id('page-range');
  if (!el) return;
  const parts = [];
  if (first && last) parts.push(`${first} → ${last}`);
  if (note) parts.push(note);
  el.textContent = parts.join('  ·  ');
}

/* Scoped transaction count for the in-page range label (was the header
 * sub). */
function txnCountText(s, fallback) {
  const t = s && s.n_transactions;
  return t
    ? `${t} transaction${t === 1 ? '' : 's'}`
    : (fallback || '');
}

/* ------------------------------------------------- period navigation
 * Prev/next step through the AVAILABLE periods — the select's options,
 * i.e. only years/months that actually have data. "Previous" = older,
 * "Next" = newer; a button is disabled when that neighbor doesn't
 * exist ("if available"). Arrow keys mirror the buttons: ArrowLeft =
 * previous, ArrowRight = next — suppressed while the caret is in a
 * text field (search box, rule inputs, rename boxes) or on a
 * <select>, so those keep their native arrow behavior. Options are
 * re-read on every step, so the state is always live (a re-render
 * never needs to re-wire). */
function wirePeriodNav({ sel, navigate, prevBtn, nextBtn }) {
  const cmp = (a, b) =>
    (/^\d+$/.test(a) && /^\d+$/.test(b)) ? (+a - +b) : a.localeCompare(b);
  // Ascending (oldest -> newest) from the select's options.
  const available = () => [...sel.options].map(o => o.value)
    .filter(Boolean).sort(cmp);
  const step = dir => { // dir -1 = older, +1 = newer; null at the edge
    const list = available();
    const i = list.indexOf(sel.value);
    if (i === -1) return null;
    const j = i + dir;
    return (j >= 0 && j < list.length) ? list[j] : null;
  };
  const go = dir => {
    const v = step(dir);
    if (v != null && v !== sel.value) navigate(v);
  };
  const paint = () => {
    const hasPrev = step(-1) != null;
    const hasNext = step(1) != null;
    if (prevBtn) {
      prevBtn.disabled = !hasPrev;
      prevBtn.title = hasPrev ? 'Previous period (←)' : 'No earlier period with data';
    }
    if (nextBtn) {
      nextBtn.disabled = !hasNext;
      nextBtn.title = hasNext ? 'Next period (→)' : 'No later period with data';
    }
  };
  if (prevBtn) prevBtn.addEventListener('click', () => go(-1));
  if (nextBtn) nextBtn.addEventListener('click', () => go(1));
  document.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const t = e.target;
    const tag = (t && t.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
        (t && t.isContentEditable)) return;
    e.preventDefault();
    go(e.key === 'ArrowLeft' ? -1 : 1);
  });
  paint();
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
  if (_importsOpen) setTimeout(refreshImports, 500);
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
  if (_importsOpen) setTimeout(refreshImports, 500);
}
function closeUpload() {
  const m = $id('uploadModal');
  if (m) m.style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeUpload(); closeImports(); } });

/* ------------------------------------------------------------ manage imports */
/* Each uploaded CSV is one import (csv_imports). Its account label is
 * editable — a retroactive re-label of ALL of its rows in every report,
 * so two accounts at the same bank can finally be told apart — and the
 * import can be deleted with all of its transactions (rules untouched).
 * _importsData is aligned with the table rows; buttons carry data-row. */
let _importsOpen = false;
let _importsData = [];

async function openImports() {
  _importsOpen = true;
  const m = $id('importsModal');
  if (m) m.style.display = 'flex';
  await refreshImports();
}

function closeImports() {
  _importsOpen = false;
  const m = $id('importsModal');
  if (m) m.style.display = 'none';
}

async function refreshImports() {
  const wrap = $id('importsList');
  if (!wrap) return;
  wrap.innerHTML = '<p class="empty">Loading imports…</p>';
  let d;
  try { d = await api('/api/imports'); }
  catch (e) { wrap.innerHTML = '<p class="empty">' + esc(e.message) + '</p>'; return; }
  _importsData = d.imports || [];
  if (!_importsData.length) {
    wrap.innerHTML = '<p class="empty">No imports yet — pick CSV files or a '
      + 'folder above.</p>';
    $id('importsTotals').textContent = '';
    return;
  }
  wrap.innerHTML = `
    <table class="imports-table">
      <thead><tr>
        <th>File</th><th>Bank</th><th>Account</th>
        <th class="num">Txns</th><th>Imported</th>
        <th class="num" title="Sum of the amounts of this import's transactions">Net</th>
        <th style="width:64px"></th>
      </tr></thead>
      <tbody>${_importsData.map((im, i) => importsRowHTML(im, i)).join('')}</tbody>
    </table>`;
  $id('importsTotals').textContent =
    `${_importsData.length} import${_importsData.length === 1 ? '' : 's'} · ${d.total} transactions`;
}

function importsRowHTML(im, i) {
  const file = im.file
    ? `<span class="imp-file" title="${esc(im.file)}">${esc(im.file)}</span>`
    : '<span class="imp-legacy" title="Imported before per-file tracking — '
      + 'grouped by bank and account">earlier import</span>';
  const span = im.first_date && im.last_date
    ? ` title="${esc(im.first_date)} → ${esc(im.last_date)}"` : '';
  return `<tr>
    <td>${file}</td>
    <td>${esc(im.source)}</td>
    <td><span class="imp-acct" data-row="${i}">${esc(im.account)}</span></td>
    <td class="num">${im.n}</td>
    <td${span}>${im.imported_at ? esc(String(im.imported_at).slice(0, 10)) : '—'}</td>
    <td class="num ${im.net < 0 ? 'neg' : im.net > 0 ? 'pos' : ''}">${fmtMoney(im.net)}</td>
    <td class="num" style="white-space:nowrap">
      <button class="txn-del" data-row="${i}" title="Rename this import's account label" onclick="startImportRename(this)">✎</button>
      <button class="txn-del" data-row="${i}" title="Delete this import and all of its transactions" onclick="deleteImportRow(this)">✕</button>
    </td>
  </tr>`;
}

function startImportRename(btn) {
  const i = Number(btn.dataset.row);
  const im = _importsData[i];
  const cell = btn && btn.closest('tr') && btn.closest('tr').querySelector('.imp-acct');
  if (!im || !cell) return;
  cell.innerHTML = `
    <input class="imp-rename" maxlength="100" value="${esc(im.account)}"
           placeholder="Account name" aria-label="Account name">
    <button class="imp-save" title="Save new name">Save</button>
    <button class="imp-cancel" title="Cancel">Cancel</button>`;
  const inp = cell.querySelector('input');
  inp.focus(); inp.select();
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') saveImportRename(i, cell);
    else if (e.key === 'Escape') refreshImports();
  });
  cell.querySelector('.imp-save').onclick = () => saveImportRename(i, cell);
  cell.querySelector('.imp-cancel').onclick = () => refreshImports();
}

async function saveImportRename(i, cell) {
  const im = _importsData[i];
  const inp = cell && cell.querySelector('input');
  const acct = inp ? inp.value.trim() : '';
  if (!acct) { toast('Account name cannot be empty', 4000); return; }
  if (acct === im.account) { refreshImports(); return; }
  try {
    await api('/api/imports/' + im.id + '/account', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: acct }),
    });
    toast(`Renamed to “${acct}” — ${im.n} transaction${im.n === 1 ? '' : 's'} re-labeled`);
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (e) {
    toast('Rename failed: ' + e.message, 5000);
  }
  await refreshImports();
}

async function deleteImportRow(btn) {
  const i = Number(btn.dataset.row);
  const im = _importsData[i];
  if (!im) return;
  const label = im.file || 'earlier import';
  if (!confirm(
    `Delete ${label} and its ${im.n} transaction${im.n === 1 ? '' : 's'}?\n\n` +
    `Only the transactions from this import are removed — category rules and ` +
    `every other import stay untouched. This cannot be undone.`)) return;
  const tr = btn.closest('tr');
  if (tr) tr.style.opacity = '.35';
  try {
    const r = await api('/api/imports/' + im.id +
      (im.source ? '?source=' + encodeURIComponent(im.source) : ''),
      { method: 'DELETE' });
    toast(`Import deleted — ${r.rows} transaction${r.rows === 1 ? '' : 's'} removed, rules untouched`);
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (e) {
    toast('Delete failed: ' + e.message, 5000);
    if (tr) tr.style.opacity = '';
    return;
  }
  await refreshImports();
}

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
const FLOW_MAX_CATEGORIES = 8; // at most N category bars by spend: the top N
                               // by max single-bucket spend get spelled out,
                               // the rest folds into one "All other" segment
                               // (the Income category is always spelled out
                               // in addition — see flowStackPlan)
const ALL_OTHER = 'All other'; // aggregate bar name — "Other" is a REAL
                               // category, so the two must not collide
const OTHER_CATEGORY = 'Other'; // standard category (see CAT_COLORS); it
                                // always aggregates into ALL_OTHER, never
                                // takes a top-N slot of its own
const INCOME_CATEGORY = 'Income'; // standard category (see CAT_COLORS);
                                  // ALWAYS spelled out, never folds into
                                  // ALL_OTHER — income-only, so the spend
                                  // ranking can't see it

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
 * segments don't all land on the grey fallback. The "All other"
 * aggregate is pinned FIRST to the bar chart's aggregate grey
 * (CAT_COLORS['Other'] = #9aa3b2), so every chart — flow charts,
 * Sankey links — colors the catch-all identically by construction.
 * Accepted edge: a custom rule literally named "All other" now
 * renders that grey instead of its hash color — ALL_OTHER is a
 * reserved name for the aggregate (see the const above). */
function catColor(name) {
  if (name === ALL_OTHER) return CAT_COLORS[OTHER_CATEGORY];
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

/* Stacked-view plan: only the top FLOW_MAX_CATEGORIES by max single-bucket
 * spend are spelled out — the chart is capped at 8 category bars by spend
 * no matter how many buckets or categories the range holds (the old
 * per-bucket union grew with the data: 16+ category rows overflowed the
 * hover popup). The Income category is ALWAYS spelled out in addition
 * (it is income-only, so peak spend 0 would sink it into "All other" —
 * the largest cash-flow item would be invisible). The real "Other"
 * category never takes a slot: it always aggregates into "All other"
 * alongside everything outside the top 8 (they share the same grey, so
 * the popup gets one aggregate row, not two). Rank is
 * deterministic: peak single-bucket spend, then all-range spend total,
 * then name. Income-only categories other than Income (peak spend 0)
 * sort last and fold in. */
function flowStackPlan(byCategory) {
  const stats = new Map(); // category -> {peak, total} spend
  for (const r of byCategory || []) {
    const s = r.spend || 0;
    let e = stats.get(r.category);
    if (!e) { e = { peak: 0, total: 0 }; stats.set(r.category, e); }
    e.peak = Math.max(e.peak, s);
    e.total += s;
  }
  const ranked = [...stats.entries()]
    .filter(([c]) => c !== OTHER_CATEGORY)
    .sort((a, b) => b[1].peak - a[1].peak ||
                    b[1].total - a[1].total ||
                    a[0].localeCompare(b[0]));
  const keep = ranked.slice(0, FLOW_MAX_CATEGORIES).map(e => e[0]);
  if (stats.has(INCOME_CATEGORY) && !keep.includes(INCOME_CATEGORY)) {
    keep.push(INCOME_CATEGORY); // income is always spelled out
  }
  const keepSet = new Set(keep);
  const merged = [...stats.keys()].filter(c => !keepSet.has(c));
  return { keep, merged };
}

/* Trailing rolling average over a fixed window of calendar months.
 * valueKeys/values: the FULL history (sorted 'YYYY-MM' buckets, may have
 * gaps — gap months count as zero inside a window data already covers);
 * returns one value per key in targetKeys (defaults to valueKeys, which
 * lets a year view average across the prior year from all-time data).
 * The denominator is the calendar months of the window that fall inside
 * the data history, so the average ramps up during the first year
 * instead of dividing a few months' total by 12. */
function trailingAvg(valueKeys, values, windowMonths, targetKeys) {
  const targets = targetKeys || valueKeys;
  if (!valueKeys.length || !targets.length) return targets.map(() => 0);
  const toT = k => { const p = k.split('-'); return Number(p[0]) * 12 + Number(p[1]) - 1; };
  const val = new Map(valueKeys.map((k, i) => [k, Number(values[i]) || 0]));
  const first = toT(valueKeys[0]);
  const last = toT(valueKeys[valueKeys.length - 1]);
  return targets.map(t => {
    const end = toT(t);
    const start = Math.max(end - (windowMonths - 1), first);
    if (end < first || start > last) return 0;
    let sum = 0;
    for (let x = start; x <= end; x++) {
      sum += val.get(String(Math.floor(x / 12)) + '-' +
        String(x % 12 + 1).padStart(2, '0')) || 0;
    }
    return Math.round(sum / (end - start + 1) * 100) / 100;
  });
}

/* cfg: {
 *   labels:     display labels for the x axis,
 *   keys:       bucket keys aligned with labels ('YYYY-MM', or the ISO
 *               week Monday 'YYYY-MM-DD' on the month page),
 *   spend:      zero-filled total spend per bucket (totals view + the
 *               stacked view's tooltip footer),
 *   income:     zero-filled total income per bucket,
 *   avgSpend:   optional per-bucket rolling 12-month average spend —
 *               drawn as a line over the bars (same length as spend),
 *   avgIncome:  same for income,
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
    // The canvas sits in a fixed-height .chart-wrap (app.css), so the
    // chart section is the same height before and after the data lands —
    // no aspect-ratio-derived height when the page first paints.
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', align: 'end',
        // Stacked mode: a category active in BOTH spend and income has
        // two same-label datasets (one per stack). generateLabels below
        // shows ONE legend entry per name, but the default onClick only
        // toggles the FIRST dataset of that name — the other side keeps
        // rendering after "unchecking" the category. Toggle every
        // dataset that carries the label together.
        onClick: (evt, legendItem, legend) => {
          const chart = legend.chart;
          const show = !chart.isDatasetVisible(legendItem.datasetIndex);
          chart.data.datasets.forEach((ds, i) => {
            if (ds.label === legendItem.text) chart.setDatasetVisibility(i, show);
          });
          chart.update();
        },
        labels: { color: cssColor('--muted'), usePointStyle: true, boxWidth: 8, boxHeight: 8,
          // Stacked mode: a category active in BOTH spend and income gets
          // two same-label datasets (one per stack) — show one legend
          // entry per name (toggle still hits the first dataset).
          generateLabels: chart => {
            const items = Chart.defaults.plugins.legend.labels.generateLabels(chart);
            const seen = new Set();
            return items.filter(it => seen.has(it.text) ? false : (seen.add(it.text), true));
          } } },
      tooltip: {
        // Index mode lists EVERY dataset at the hovered bucket. Two
        // cleanups: drop $0.00 rows, and in stacked mode fold a category
        // present in BOTH stacks (one same-label dataset per stack) into
        // a single row — keep the first occurrence, stash the other side
        // on it for the label callback (same one-entry-per-name policy
        // as the legend's generateLabels above).
        // NOTE: this vendored Chart.js build passes filter(item, index,
        // dataPoints, chart) — the documented (item, dataPoints, index)
        // order is NOT what _createItems sends, so take the array arg
        // from whichever slot holds it.
        filter: (item, second, third) => {
          const dataPoints = Array.isArray(second) ? second
            : Array.isArray(third) ? third : [];
          if (Number(item.raw) === 0) return false;
          const keep = dataPoints.findIndex(d =>
            d.dataset.label === item.dataset.label && Number(d.raw) !== 0);
          if (keep === dataPoints.indexOf(item)) return true;
          const k = dataPoints[keep];
          if (k) (k._sides = k._sides || {})[item.dataset.stack] = Number(item.raw);
          return false;
        },
        callbacks: {
          label: c => {
            const own = Number(c.raw);
            const otherSide = c.dataset.stack === 'spend' ? 'income' : 'spend';
            const stashed = c._sides && c._sides[otherSide];
            if (stashed == null) return ' ' + c.dataset.label + ': ' + fmtMoney(own);
            const spend = c.dataset.stack === 'spend' ? own : stashed;
            const income = c.dataset.stack === 'income' ? own : stashed;
            return [
              ' ' + c.dataset.label + ': ' + fmtMoney(spend - income),
              '   spent ' + fmtMoney(spend) + ' · income ' + fmtMoney(income),
            ];
          },
        },
      },
    },
    scales: {
      x: { ticks: { color: cssColor('--muted') }, grid: { display: false } },
      y: { beginAtZero: true,
        ticks: { color: cssColor('--muted'), callback: v => fmtAxis(v) },
        grid: { color: cssColor('--grid') } },
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
        backgroundColor: 'rgba(30,142,82,.85)', hoverBackgroundColor: '#1e8e52',
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
      const color = name === ALL_OTHER ? CAT_COLORS[OTHER_CATEGORY] : catColor(name);
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
  // Optional overlay: trailing 12-month rolling averages as lines. Each
  // line gets its OWN stack group: on the stacked (by-category) y-scale
  // Chart.js accumulates every dataset into a `${xId}.${yId}.${stack||type}`
  // group, so sharing a group (or omitting `stack` — the default group is
  // per chart TYPE, and both lines are 'line') would draw the income line
  // on top of the spend line instead of from zero.
  if (cfg.avgSpend || cfg.avgIncome) {
    const line = (label, data, color, stack) => ({
      label, data, type: 'line', stack,
      borderColor: color, backgroundColor: color, borderWidth: 2,
      pointRadius: 2.5, pointHoverRadius: 4, pointBackgroundColor: color,
      tension: .3, fill: false,
    });
    if (cfg.avgSpend) datasets.push(
      line('Spend · 12-mo avg', cfg.avgSpend, '#ff6b6b', 'avg-spend'));
    if (cfg.avgIncome) datasets.push(
      line('Income · 12-mo avg', cfg.avgIncome, CAT_COLORS[INCOME_CATEGORY], 'avg-income'));
  }
  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels: cfg.labels, datasets },
    options,
  });
  if (cfg.onBar) chart.options.onClick = (evt, els) => { if (els.length) cfg.onBar(els[0].index); };
  return chart;
}

/* Overview sankey: 3-column flow — NET-POSITIVE (income) categories
 * on the LEFT, accounts in the MIDDLE, NET-NEGATIVE (spend)
 * categories on the RIGHT: income flows in, passes through the
 * accounts, and lands in spend categories. Edge orientation encodes
 * the side: credit flows run category → account, debit flows run
 * account → category. Classification happens upstream (index.html
 * renderSankeyChart): a category is net positive → left (credit
 * flows only), net negative → right (debit flows only), net zero →
 * not shown at all. Ignored rows never reach the endpoint (server-side,
 * like every aggregate).
 *
 * Column placement is PINNED explicitly (options.column): the plugin
 * reads it per node and skips graph-depth assignment for pinned
 * nodes. This is deliberate — in the flipped graph an account with
 * no credit inflow (Apple Card in the live data) is a pure source,
 * and graph topology would dump it in the LEFT column with the
 * income categories. Pinning guarantees the 3-column structure
 * regardless of topology: credit = 0, accounts = 1, debit = 2.
 * nodeAlign:'start' packs each column from the top instead of
 * justifying it.
 *
 * Node order: per column, flow total desc (left credit categories by
 * credit, middle accounts by total, right debit categories by
 * debit) — the layout keeps first-appearance order, so the widest
 * bands sit at the top.
 * Node colors follow the band ends — the plugin takes each node's
 * fill from the flow element's resolved colorFrom/colorTo, so BOTH
 * are scriptable here: a category endpoint (either side) carries its
 * palette color, an account endpoint the theme-neutral fill.
 * flows: [{from, to, flow}] (flow > 0) where from/to are node
 * identities: plain category names or account keys
 * (source + '\u0000' + account, never written into HTML — the
 * U+0000 is also what separates the two node kinds here; see
 * skPickers in index.html).
 * emptyMsg: text for the no-data hint (defaults to the generic
 * "no transactions yet" — the Overview passes a filter-specific one
 * when the pickers hid everything). */
function makeSankeyChart(canvas, flows, emptyMsg = 'no transactions yet') {
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  const wrap = canvas.parentElement;
  const hint = wrap.querySelector('.chart-empty');
  if (hint) hint.remove();
  if (!flows.length) { // no data: muted hint in a compact wrap
    wrap.style.height = '280px';
    const h = document.createElement('div'); // (panel never hidden —
    h.className = 'chart-empty'; // stable space, like the other charts)
    h.textContent = emptyMsg;
    wrap.appendChild(h);
    return null;
  }

  // Account keys embed U+0000, category names never do — that is what
  // separates the two node kinds. Two banks can share an account name
  // ("Checking") — disambiguate only the duplicates, with the source
  // prefixed (label logic unchanged from v1.6.0).
  const isAcctKey = x => String(x).includes('\u0000');
  const total = {}, catSide = {};
  for (const f of flows) {
    for (const end of [f.from, f.to])
      total[end] = (total[end] || 0) + f.flow;
    const c = isAcctKey(f.from) ? f.to : f.from;
    const t = catSide[c] || (catSide[c] = { debit: 0, credit: 0 });
    // Flipped orientation: account → category is a debit (spend)
    // flow; category → account is a credit (income) flow.
    if (isAcctKey(f.from)) t.debit += f.flow; else t.credit += f.flow;
  }
  const acctKeys = Object.keys(total).filter(k => isAcctKey(k));
  const names = acctKeys.map(k => k.split('\u0000').pop());
  const dupNames = new Set(names.filter((n, i) => names.indexOf(n) !== i));
  const acctLabel = k => {
    const [s, a] = k.split('\u0000');
    return dupNames.has(a) ? s + ' · ' + a : a;
  };
  // Column order: credit (left), accounts (middle), debit (right) —
  // per column, flow total desc (credit by credit, accounts by
  // total, debit by debit). A category name active on BOTH sides
  // collapses to one node (odd data, none today — don't special-
  // case): it lands in the column of its first occurrence (credit
  // first), and its pin matches that placement.
  const creditCats = Object.keys(catSide)
    .filter(c => catSide[c].credit > 0)
    .sort((a, b) => catSide[b].credit - catSide[a].credit || a.localeCompare(b));
  const debitCats = Object.keys(catSide)
    .filter(c => catSide[c].debit > 0)
    .sort((a, b) => catSide[b].debit - catSide[a].debit || a.localeCompare(b));
  const colItems = [
    ...creditCats.map(c => [c, 0]),
    ...acctKeys
      .sort((a, b) => total[b] - total[a] || a.localeCompare(b))
      .map(k => [acctLabel(k), 1]),
    ...debitCats.map(c => [c, 2]),
  ];
  const seen = new Set();
  const labels = [];
  const column = {};
  for (const [l, col] of colItems) {
    if (seen.has(l)) continue;
    seen.add(l);
    labels.push(l);
    column[l] = col;
  }
  // Size the wrap to the tallest column instead of a fixed 640px: the
  // plugin packs columns from the top, so an oversized wrap just leaves
  // dead space below the bands. ~48px per node (node ~30px + nodePadding
  // 14 + margin), clamped so a dense chart still gets the old 640px.
  const colCount = [0, 0, 0];
  for (const l of labels) colCount[column[l]]++;
  const maxCol = Math.max(...colCount);
  wrap.style.height = Math.max(280, Math.min(640, maxCol * 48 + 48)) + 'px';
  const nodeIndex = new Map(labels.map((l, i) => [l, i]));
  const render = x => isAcctKey(x) ? acctLabel(x) : x;
  const labelTotal = {};
  for (const f of flows)
    labelTotal[render(f.to)] = (labelTotal[render(f.to)] || 0) + f.flow;
  // First-appearance order = column rank, then node rank within the
  // column (the band sort keeps each node's ribbons contiguous).
  const data = flows.map(f => ({
    from: render(f.from), to: render(f.to), flow: f.flow,
  })).sort((a, b) =>
    nodeIndex.get(a.from) - nodeIndex.get(b.from) ||
    (labelTotal[b.to] || 0) - (labelTotal[a.to] || 0));

  const css = getComputedStyle(document.documentElement);
  const acctColor = (css.getPropertyValue('--muted') || '').trim() || '#5a6772';
  // Endpoint colors resolve on the RENDERED labels (data items carry
  // those, not the raw keys) — the Set of rendered account labels is
  // what tells the ends apart. A node's fill is last-edge-wins in the
  // plugin, and every edge incident to a node resolves it to the same
  // color, so nodes stay consistent.
  const renderedAccts = new Set(acctKeys.map(acctLabel));
  const nodeColor = x => renderedAccts.has(String(x)) ? acctColor
                                                      : catColor(String(x));

  return new Chart(canvas, {
    type: 'sankey',
    data: {
      datasets: [{
        data,
        // Scriptable ctx: Chart.js passes the dataset context with
        // ctx.raw = the raw data item ({from, to, flow}).
        colorFrom: ctx => nodeColor((ctx && ctx.raw && ctx.raw.from) ?? ''),
        colorTo: ctx => nodeColor((ctx && ctx.raw && ctx.raw.to) ?? ''),
      }],
    },
    options: {
      responsive: true,
      // Fixed-height .chart-wrap (app.css) — no aspect-ratio-derived
      // height when the page first paints (layout-stability rule).
      maintainAspectRatio: false,
      layout: { padding: { top: 6, bottom: 6 } },
      // Labels: the plugin draws node keys beside the nodes; give them
      // the theme text color (default is black — unreadable on dark
      // schemes). Keep the default font (the chart font).
      // Middle-column (account) labels land over ribbons — the bands
      // are alpha 0.5, so the bold theme-colored text stays readable
      // over them (verified by screenshot on the live data).
      nodeLabels: { display: true, color:
        (css.getPropertyValue('--text') || '').trim() || '#25343f' },
      nodeWidth: 12,
      nodePadding: 14,
      // Columns are pinned explicitly via `column` (built above):
      // the plugin takes each pinned node's x from the map and skips
      // graph-depth assignment — topology can't shove an account
      // into the wrong column. modeX:'node' keeps any unpinned node
      // at its natural graph depth (the default 'edge' mode would
      // push every sink to the rightmost column), and nodeAlign:
      // 'start' packs each column from the top instead of justifying.
      modeX: 'node',
      nodeAlign: 'start',
      column,
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          callbacks: {
            title: () => '',
            // Default shape: parsed._custom = {from:{key}, to:{key},
            // flow} (the keys carry the U+0000 account separator) —
            // render labels (never raw keys) and format the flow as
            // money.
            label(ctx) {
              const p = ctx.parsed, c = p._custom || {};
              const name = (r, f) => typeof r === 'string' ? r
                : String((f && f.key) ?? '').replace('\u0000', ' · ');
              const from = name(c.from, p.from);
              const to = name(c.to, p.to);
              const v = (c && typeof c.flow === 'number') ? c.flow : p.flow;
              return `${from} → ${to}: ${fmtMoney(v)}`;
            },
          },
        },
      },
    },
  });
}

/* Categories page trend chart: monthly flow stacked over EXACTLY the
 * categories the multiselect picker chose — no top-8 folding, no
 * "All other", no totals/category mode toggle (the picker is the
 * category control). Spend and income sit in SEPARATE stacks so each
 * starts at zero (house rule, same as makeFlowChart's stacked view) —
 * that is what lets net-positive categories (Income, …) plot at all.
 * Overlay line: the 12-month rolling average of the selected
 * categories' COMBINED monthly spend. cfg:
 *   labels:     display labels for the x axis,
 *   keys:       'YYYY-MM' bucket keys aligned with labels,
 *   categories: selected category names, in picker order (drives the
 *               stack order); a side with no activity is skipped,
 *   byCategory: [{key, category, spend, income}] rows already filtered
 *               to the selection (no row for a (bucket, category)
 *               without activity — the series below zero-fill),
 *   totalsSpend/totalsIncome: per-key selected totals (aligned with
 *               keys) — tooltip footer,
 *   avgSpend:   trailing 12-mo average of totalsSpend (aligned, or
 *               null when the selection carries no spend — the line
 *               is omitted rather than drawn flat at zero).
 */
function makeCategoryTrendChart(canvas, cfg) {
  // Re-renders (data changes, picker toggles) reuse the same canvas:
  // destroy first, or Chart.js throws "Canvas is already in use".
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  const options = {
    responsive: true,
    // Fixed-height .chart-wrap (app.css): same section height before and
    // after the data lands (see makeFlowChart's note).
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', align: 'end',
        // A category active in BOTH spend and income carries two
        // same-label datasets (one per stack): the default onClick
        // would toggle only the first — toggle every dataset with the
        // label together (same rule as makeFlowChart).
        onClick: (evt, legendItem, legend) => {
          const chart = legend.chart;
          const show = !chart.isDatasetVisible(legendItem.datasetIndex);
          chart.data.datasets.forEach((ds, i) => {
            if (ds.label === legendItem.text) chart.setDatasetVisibility(i, show);
          });
          chart.update();
        },
        labels: { color: cssColor('--muted'), usePointStyle: true, boxWidth: 8, boxHeight: 8,
          // One legend entry per category name (the avg line has a
          // unique label of its own).
          generateLabels: chart => {
            const items = Chart.defaults.plugins.legend.labels.generateLabels(chart);
            const seen = new Set();
            return items.filter(it => seen.has(it.text) ? false : (seen.add(it.text), true));
          } } },
      tooltip: {
        // Index mode lists every dataset at the hovered month: drop
        // $0.00 rows, and fold a category present in BOTH stacks into
        // a single row — net first, then spent/income (same policy as
        // makeFlowChart's stacked view).
        // NOTE: this vendored Chart.js build passes filter(item,
        // index, dataPoints, chart) — take the array arg from
        // whichever slot holds it.
        filter: (item, second, third) => {
          const dataPoints = Array.isArray(second) ? second
            : Array.isArray(third) ? third : [];
          if (Number(item.raw) === 0) return false;
          const keep = dataPoints.findIndex(d =>
            d.dataset.label === item.dataset.label && Number(d.raw) !== 0);
          if (keep === dataPoints.indexOf(item)) return true;
          const k = dataPoints[keep];
          if (k) (k._sides = k._sides || {})[item.dataset.stack] = Number(item.raw);
          return false;
        },
        callbacks: {
          label: c => {
            const own = Number(c.raw);
            const otherSide = c.dataset.stack === 'spend' ? 'income' : 'spend';
            const stashed = c._sides && c._sides[otherSide];
            if (stashed == null) return ' ' + c.dataset.label + ': ' + fmtMoney(own);
            const spend = c.dataset.stack === 'spend' ? own : stashed;
            const income = c.dataset.stack === 'income' ? own : stashed;
            return [
              ' ' + c.dataset.label + ': ' + fmtMoney(spend - income),
              '   spent ' + fmtMoney(spend) + ' · income ' + fmtMoney(income),
            ];
          },
          footer: items =>
            'Spend: ' + fmtMoney(cfg.totalsSpend[items[0].dataIndex] || 0) +
            '   ·   Income: ' + fmtMoney(cfg.totalsIncome[items[0].dataIndex] || 0),
        },
      },
    },
    scales: {
      // Bars pile: per-dataset `stack` only groups, the SCALES must be
      // stacked too (same rule as makeFlowChart's stacked view).
      x: { stacked: true, ticks: { color: cssColor('--muted') }, grid: { display: false } },
      y: { stacked: true, beginAtZero: true,
        ticks: { color: cssColor('--muted'), callback: v => fmtAxis(v) },
        grid: { color: cssColor('--grid') } },
    },
  };
  // Zero-fill per (key, category) from the sparse rows; spend and
  // income are separate series (separate stacks, each from zero).
  const cell = new Map(); // key -> Map(category -> [spend, income])
  for (const r of cfg.byCategory) {
    let m = cell.get(r.key);
    if (!m) { m = new Map(); cell.set(r.key, m); }
    const v = m.get(r.category) || [0, 0];
    m.set(r.category, [v[0] + (r.spend || 0), v[1] + (r.income || 0)]);
  }
  const datasets = [];
  for (const name of cfg.categories) {
    const color = catColor(name);
    for (const side of ['spend', 'income']) {
      const idx = side === 'spend' ? 0 : 1;
      const data = cfg.keys.map(k =>
        Math.round((cell.get(k)?.get(name)?.[idx] || 0) * 100) / 100);
      if (data.every(v => v === 0)) continue;
      datasets.push({
        label: name, data, stack: side,
        backgroundColor: withAlpha(color, .82), hoverBackgroundColor: color,
        maxBarThickness: 60,
      });
    }
  }
  // The avg line gets its OWN stack group: on a stacked y-scale every
  // dataset accumulates into its `${xId}.${yId}.${stack||type}` group,
  // so sharing a group (or omitting `stack`) would draw it on top of
  // the bars instead of from zero.
  if (cfg.avgSpend) {
    datasets.push({
      label: '12-mo avg (selected)', data: cfg.avgSpend, type: 'line', stack: 'avg-line',
      borderColor: '#ff6b6b', backgroundColor: '#ff6b6b', borderWidth: 2,
      pointRadius: 2.5, pointHoverRadius: 4, pointBackgroundColor: '#ff6b6b',
      tension: .3, fill: false,
    });
  }
  return new Chart(canvas, {
    type: 'bar',
    data: { labels: cfg.labels, datasets },
    options,
  });
}

/* ------------------------------------------------------------ category table */
/* Rendered into a container:
 *   container: element (use a .panel.tight wrapper)
 *   catData:   [{category, spend, refunds, net, n}] from /api/categories —
 *              net = spend − refunds. Signed display: net spend (net > 0)
 *              renders red with a − sign (class num neg), net credit
 *              (net < 0) green with a + sign (class num pos).
 *   Share: bidirectional bar around a center zero line — spend bars extend
 *          left, credit bars right; each half is scaled to ITS OWN
 *          direction's total, so both directions can use the full half-width.
 *          The % label shows the same share next to the bar.
 *   scope:     {} | {year} | {month} — query scope for drill-downs
 */
async function renderCategoryTable(container, catData, scope) {
  if (!catData.length) {
    container.innerHTML = '<p class="empty">No spending in this scope.</p>';
    return;
  }
  const spendTotal = catData.reduce((a, d) => a + Math.max(0, d.net), 0) || 1;
  const creditTotal = catData.reduce((a, d) => a + Math.max(0, -d.net), 0) || 1;
  container.innerHTML = `
    <table class="cat-table">
      <thead><tr>
        <th style="width:28px"></th><th>Category</th><th class="num">Txns</th>
        <th class="num" title="Net = spend − refunds. Red − = net spend; green + = net credit (refunds exceeded spend).">Net</th>
        <th style="width:24%" title="Share of each direction's total: left bars = share of total spend, right bars = share of total net credits.">Share</th>
      </tr></thead>
      <tbody>${catData.map((d, i) => {
        const color = catColor(d.category);
        const net = d.net;
        const isSpend = net >= 0;
        const pct = ((isSpend ? net / spendTotal : -net / creditTotal) * 100).toFixed(1);
        const netCell = isSpend
          ? `<td class="num neg">${fmtMoney(-net)}</td>`
          : `<td class="num pos">+${fmtMoney(-net)}</td>`;
        // Diverging bar: fill anchored at the zero line, extending outward.
        // The % label ALWAYS sits on the left (neg-side), for both spend and
        // credit rows, so the left half is never hollow. Spend bars extend
        // left (red), credit bars extend right (green); the opposite side
        // keeps an empty slot so both halves stay the same width.
        const shareCell = isSpend
          ? `<td><div class="share-split">
              <div class="share-side neg"><span class="share-pct">${pct}%</span><div class="share-bar"><i style="width:${pct}%;background:var(--spend)"></i></div></div>
              <div class="share-zero"></div>
              <div class="share-side pos"><div class="share-bar"><i style="width:0"></i></div><span class="share-pct"></span></div>
            </div></td>`
          : `<td><div class="share-split">
              <div class="share-side neg"><span class="share-pct">${pct}%</span><div class="share-bar"><i style="width:0"></i></div></div>
              <div class="share-zero"></div>
              <div class="share-side pos"><div class="share-bar"><i style="width:${pct}%;background:var(--income)"></i></div><span class="share-pct"></span></div>
            </div></td>`;
        return `
        <tr class="drill-row" data-i="${i}" data-cat="${esc(d.category)}">
          <td><span class="chev">▶</span></td>
          <td><span class="dot" style="background:${color}"></span>${esc(d.category)}</td>
          <td class="num">${d.n}</td>
          ${netCell}
          ${shareCell}
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
  const rows = sortTxns(all.filter(t =>
      txnSearchMatch(t, container._search || '') &&
      txnSetByMatch(t, container._setBy || 'all')),
    container._sortState);
  container._lastShown = rows.length;
  container._lastTotal = all.length;
  const q = (container._search || '').trim();
  const by = container._setBy || 'all';
  container.innerHTML = rows.length
    ? txnTableHTML(rows, { sort: container._sortState,
                           editable: container._editable,
                           search: container._search || '' })
    : ((q || by !== 'all')
        ? `<p class="empty">No matching transactions — ${all.length} transaction${all.length === 1 ? '' : 's'} in this period hidden by the ${by !== 'all' ? (q ? 'search and Set-by filter' : 'Set-by filter') : 'search'}.</p>`
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

/* "Set by" dropdown filter — the search bars on the Month ("All
 * transactions") and Transactions ("categorization") pages narrow rows
 * by who set the row's effective category. Option values map onto the
 * API's per-row `set_by` field; 'uncategorized' is the label for rows
 * with nothing set (set_by 'none' from /api/transactions — no override,
 * no rule, no CSV category). The Transactions page runs the same filter
 * server-side via /api/overrides?set_by=, so this client-side match only
 * serves the locally-scoped tables (Month, drill-downs). */
const TXN_SET_BY = { manual: 'manual', llm: 'llm', rule: 'rule',
                     csv: 'csv', uncategorized: 'none' };
function txnSetByMatch(row, sel) {
  return sel === 'all' || row.set_by === TXN_SET_BY[sel];
}

/* Shared transaction-search bar: the search input + the "Set by"
 * dropdown (default "All"), rendered INSIDE the panel above the table
 * on BOTH the Transactions and the Month pages — the same bar layout
 * the Categories rule panels use (form on top, list below). One options
 * table keeps the two pages' dropdowns identical; values are the
 * server's set_by vocabulary (/api/overrides) and map onto the local
 * row set via txnSetByMatch for client-side tables. */
const TXN_SET_BY_OPTIONS = [
  ['all', 'All'],
  ['manual', 'You'],
  ['llm', 'LLM'],
  ['rule', 'Rule'],
  ['csv', 'Bank'],
  ['uncategorized', 'Unknown'],
];
/* Wire the shared bar. onFilter({q, setBy}) fires on every change;
 * input keystrokes are debounced when debounceMs is set (the
 * server-side page refetches on each fire — the client-side page
 * re-renders directly and leaves it at 0). onInput (optional) fires on
 * EVERY keystroke, before the debounce, for UI that must react at once
 * (the Transactions page enables the rule-builder controls on input). */
function wireTxnSearchBar(input, setBySel, onFilter,
                          { debounceMs = 0, onInput = null } = {}) {
  const read = () => ({ q: input.value, setBy: setBySel.value || 'all' });
  let timer = null;
  input.addEventListener('input', () => {
    if (onInput) onInput();
    if (debounceMs) {
      clearTimeout(timer);
      timer = setTimeout(() => onFilter(read()), debounceMs);
    } else {
      onFilter(read());
    }
  });
  setBySel.addEventListener('change', () => onFilter(read()));
}
/* Shared hint text for the search panels (Transactions + Month). While
 * a search term is active the shown-count badge already reports the
 * narrowing, so the hint carries no "for "X"" echo; idle -> empty. A
 * non-default sort direction and an optional extra note (e.g. "LLM not
 * configured") show in both states. */
function txnSearchHint(q, st, note) {
  const parts = [];
  if (st && (st.key !== 'date' || st.dir !== 'desc'))
    parts.push(txnSortLabel(st));
  if (note) parts.push(note);
  return parts.join(' · ');
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

/* Small provenance badge for the "Transactions" table (Month page) — same
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
let _accountsCache = [];     // last /api/accounts rows {source, account,
                             // n, net} — backs the rule scope dropdowns

/* --------------------------------------------------- rule scope (source) */
/* A rule may be restricted to one bank (source) and/or one account;
   NULL = All, so unscoped rules behave exactly as before. The options
   come from the live account list (the same data the Accounts table
   shows), and the account scope targets the account's CURRENT display
   label — Manage Imports renames re-scope rows read-time. */

function _scopeSources(accounts) {
  return [...new Set((accounts || []).map(a => a.source))].sort();
}

function _scopeAccounts(accounts, source) {
  const rows = (accounts || []).filter(a => !source || a.source === source);
  return [...new Set(rows.map(a => a.account))].sort();
}

function _scopeOptions(values, allLabel) {
  return `<option value="">${allLabel}</option>` +
    values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
}

function _scopeForm(prefix) {
  return `
    <select id="${prefix}ScopeSource" aria-label="Source scope"
            onchange="scopeSourceChanged('${prefix}')">
      ${_scopeOptions(_scopeSources(_accountsCache), 'All sources')}
    </select>
    <select id="${prefix}ScopeAccount" aria-label="Account scope">
      ${_scopeOptions(_scopeAccounts(_accountsCache, ''), 'All accounts')}
    </select>`;
}

function scopeSourceChanged(prefix) {
  const srcId = prefix + 'ScopeSource';
  const acctId = prefix + 'ScopeAccount';
  $id(acctId).innerHTML = _scopeOptions(
    _scopeAccounts(_accountsCache, $id(srcId).value), 'All accounts');
}

function _scopeBody() {
  const source = $id('ruleScopeSource').value || null;
  const account = $id('ruleScopeAccount').value || null;
  return { source, account };
}

function _ignoreScopeBody() {
  return { source: $id('ignoreScopeSource').value || null,
           account: $id('ignoreScopeAccount').value || null };
}

function _scopeLabel(r) {
  const parts = [];
  if (r.source) parts.push(r.source);
  if (r.account) parts.push(r.account);
  return parts.length
    ? parts.map(esc).join(' · ')
    : '<span style="color:var(--muted)">All</span>';
}

/* Editable category on an existing rule (PUT /api/rules/{id}): every
   standard category plus the reserved "Ignored", current value
   preselected. Picking "Ignored" moves the rule to the Ignore panel
   and vice versa; pattern, scope, and priority are untouched, so the
   re-render from the fresh /api/rules payload lands the row in its
   new panel. ondragstart stopPropagation keeps row-dragging from
   hijacking the dropdown. */
function _ruleCatSelect(r) {
  return `<select class="rule-cat" aria-label="Category for rule '${esc(r.pattern)}'"
      onchange="updateRuleCategory(${r.id}, this.value)"
      ondragstart="event.stopPropagation()">
    ${CATS.map(c => `<option ${c === r.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
  </select>`;
}

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

function renderRules(data, accounts) {
  const el = $id('rulesPanel');
  if (!el) return;
  if (accounts) _accountsCache = accounts;
  _rulesRefreshData(data);
  const rules = data.rules.filter(r => r.category !== 'Ignored');
  const list = rules.length
    ? '<table><thead><tr>' +
      '<th style="width:30px"></th>' +
      '<th>Keyword</th>' +
      '<th title="Where the rule applies — empty means every source and account">Scope</th>' +
      '<th>→ Category</th><th class="num">Matches</th><th class="num" title="Net of the rows this rule actually categorizes — first match wins, manual categories beat rules. − spend, + income">Net</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      rules.map(r => `
        <tr class="rule-row" draggable="true" data-rule-id="${r.id}"
            ondragstart="ruleDragStart(event)" ondragover="ruleDragOver(event)"
            ondragleave="ruleDragLeave(event)" ondrop="ruleDrop(event)"
            ondragend="ruleDragEnd(event)">
          <td class="rule-drag" title="Drag to change priority">⠿</td>
          <td class="desc" title="${esc(r.pattern)}">${esc(r.pattern)}</td>
          <td title="Applies to: ${esc([r.source, r.account].filter(Boolean).join(' · ') || 'all sources and accounts')}">
              ${_scopeLabel(r)}</td>
          <td>${_ruleCatSelect(r)}</td>
          <td class="num">${r.matches}</td>
          <td class="num ${r.net < 0 ? 'neg' : r.net > 0 ? 'pos' : ''}">${fmtMoney(r.net)}</td>
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
      ${_scopeForm('rule')}
      <button type="submit">Add rule</button>
    </form>
    ${list}`;
}

/* Dedicated "Ignore transactions" box: pattern rules whose matches are
   excluded from every total (reserved category "Ignored"). Rows stay
   visible in lists, dimmed and tagged. Below the keyword rules, the
   panel also holds date-based ignores (before / within / after a range)
   — same effect, matched by txn_date instead of description. */
let _dateIgCache = [];  // last /api/date-ignores items, for re-render
                        // paths that don't refetch (e.g. reorder failure)

function renderIgnoreRules(data, dateIgData, accounts) {
  const el = $id('ignoreRulesPanel');
  if (!el) return;
  if (accounts) _accountsCache = accounts;
  _rulesRefreshData(data);
  if (dateIgData) _dateIgCache = dateIgData.ignores || [];
  const rules = data.rules.filter(r => r.category === 'Ignored');
  const list = rules.length
    ? '<table><thead><tr>' +
      '<th style="width:30px"></th>' +
      '<th>Keyword</th>' +
      '<th title="Where the rule applies — empty means every source and account">Scope</th>' +
      '<th>→ Category</th><th class="num">Matches</th><th class="num" title="Net amount of the rows excluded from all totals by this rule">Net</th><th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      rules.map(r => `
        <tr class="rule-row" draggable="true" data-rule-id="${r.id}"
            ondragstart="ruleDragStart(event)" ondragover="ruleDragOver(event)"
            ondragleave="ruleDragLeave(event)" ondrop="ruleDrop(event)"
            ondragend="ruleDragEnd(event)">
          <td class="rule-drag" title="Drag to change priority">⠿</td>
          <td class="desc" title="${esc(r.pattern)}">${esc(r.pattern)}</td>
          <td title="Applies to: ${esc([r.source, r.account].filter(Boolean).join(' · ') || 'all sources and accounts')}"
              >${_scopeLabel(r)}</td>
          <td>${_ruleCatSelect(r)}</td>
          <td class="num">${r.matches}</td>
          <td class="num ${r.net < 0 ? 'neg' : r.net > 0 ? 'pos' : ''}">${fmtMoney(r.net)}</td>
          <td class="num"><button class="rule-del" title="Delete rule" onclick="deleteRule(${r.id})">×</button></td>
        </tr>`).join('') +
      '</tbody></table>'
    : '<p class="empty" style="padding:14px">No ignore rules yet — add one above.</p>';
  el.innerHTML = `
    <form class="rule-form" onsubmit="addIgnoreRule(event)">
      <input id="ignorePattern" type="text" maxlength="200" required
             placeholder="Keyword, e.g. 'internal transfer' or 'to savings'">
      ${_scopeForm('ignore')}
      <button type="submit">Ignore matches</button>
    </form>
    ${list}
    ${_dateIgSection()}`;
}

/* --------------------------------------------- date-based ignores (panel) */
/* Before / within / after a date range — matched by txn_date at read
   time, same treatment as the keyword rules: excluded from every total,
   still listed (dimmed, flagged). A month or year fully covered by a
   date ignore has no aggregate row, so its bars/cards disappear. */
function dateIgLabel(r) {
  if (r.kind === 'before') return 'Everything before ' + r.start;
  if (r.kind === 'after') return 'Everything after ' + r.start;
  return 'Within ' + r.start + ' → ' + r.end;
}

function _dateIgSection() {
  const items = _dateIgCache;
  const list = items.length
    ? '<table><thead><tr>' +
      '<th>Scope</th><th class="num">Txns</th>' +
      '<th class="num" title="Net amount of the rows excluded from all totals by this range">Net</th>' +
      '<th style="width:40px"></th>' +
      '</tr></thead><tbody>' +
      items.map(r => `
        <tr>
          <td class="desc" title="${esc(dateIgLabel(r))}">${esc(dateIgLabel(r))}</td>
          <td class="num">${r.n}</td>
          <td class="num ${r.net < 0 ? 'neg' : r.net > 0 ? 'pos' : ''}">${fmtMoney(r.net)}</td>
          <td class="num"><button class="rule-del" title="Delete date ignore" onclick="deleteDateIgnore(${r.id})">×</button></td>
        </tr>`).join('') +
      '</tbody></table>'
    : '<p class="empty" style="padding:14px">No date ignores yet — pick a mode and a date above.</p>';
  return `
    <div class="date-ig">
      <h3 class="date-ig-head">By date</h3>
      <form class="rule-form" onsubmit="addDateIgnore(event)">
        <select id="dateIgKind" onchange="dateIgKindChanged()" aria-label="Date ignore mode">
          <option value="before">Before</option>
          <option value="range">Within</option>
          <option value="after">After</option>
        </select>
        <input type="date" id="dateIgStart" required>
        <input type="date" id="dateIgEnd" disabled>
        <button type="submit">Ignore dates</button>
      </form>
      <p class="hint" style="margin:-6px 0 12px">
        “Before 2025-01-01” keeps that date; “After” keeps it too; “Within” covers both end dates.
      </p>
      ${list}
    </div>`;
}

function dateIgKindChanged() {
  const isRange = $id('dateIgKind').value === 'range';
  $id('dateIgEnd').disabled = !isRange;
  if (!isRange) $id('dateIgEnd').value = '';
}

async function addDateIgnore(e) {
  e.preventDefault();
  const kind = $id('dateIgKind').value;
  let start = $id('dateIgStart').value;
  let end = $id('dateIgEnd').value || null;
  if (kind === 'range') {
    if (!end) { toast('Pick both dates for a range', 4000); return; }
    if (end < start) [start, end] = [end, start]; // normalize order
  }
  try {
    await api('/api/date-ignores', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, start, end }),
    });
    $id('dateIgStart').value = '';
    $id('dateIgEnd').value = '';
    toast('Date ignore added');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Could not add date ignore: ' + err.message, 5000);
  }
}

async function deleteDateIgnore(id) {
  try {
    await api('/api/date-ignores/' + id, { method: 'DELETE' });
    toast('Date ignore deleted');
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Delete failed: ' + err.message, 5000);
  }
}

async function addIgnoreRule(e) {
  e.preventDefault();
  const pattern = $id('ignorePattern').value.trim();
  const scope = _ignoreScopeBody();
  try {
    await api('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, category: 'Ignored', ...scope }),
    });
    $id('ignorePattern').value = '';
    toast('Ignore rule added' + (scope.source || scope.account
      ? ' (scoped: ' + [scope.source, scope.account].filter(Boolean).join(' · ') + ')'
      : ''));
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Could not add ignore rule: ' + err.message, 5000);
  }
}

async function addRule(e) {
  e.preventDefault();
  const pattern = $id('rulePattern').value.trim();
  const category = $id('ruleCategory').value;
  const scope = _scopeBody();
  try {
    await api('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, category, ...scope }),
    });
    $id('rulePattern').value = '';
    toast('Rule added' + (scope.source || scope.account
      ? ' (scoped: ' + [scope.source, scope.account].filter(Boolean).join(' · ') + ')'
      : ''));
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

/* Change the category of an already-saved rule (the row dropdown in
   either panel). Pattern, scope, and priority are untouched server-side.
   On failure, re-render both panels from the cache so the dropdown
   snaps back to the stored category. */
async function updateRuleCategory(id, category) {
  try {
    await api('/api/rules/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    });
    toast('Rule now assigns ' + category);
    if (window.onDataChanged) setTimeout(onDataChanged, 400);
  } catch (err) {
    toast('Could not update rule: ' + err.message, 5000);
    if (_rulesDataCache) {
      renderRules(_rulesDataCache);
      renderIgnoreRules(_rulesDataCache);
    }
  }
}
