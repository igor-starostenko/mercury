/* Mercury — shared helpers: API, formatting, palette. Loaded before ui.js. */

const CAT_COLORS = {
  // Reserved families: green = money coming in (dark Income, light
  // Investing), grey = moves that are not spending (light Other, dark
  // Transfers). The rest are a Lab-distance-optimized spread — no two
  // palette entries closer than ~27 ΔE* so every bar stays distinct.
  Income: '#1e8e52', Investing: '#9ad45f',
  Other: '#9aa3b2', Transfers: '#454c5c',
  Groceries: '#106bc6', Dining: '#eb6600', Transportation: '#47c2ff',
  Housing: '#953bb0', Utilities: '#c28a29', Subscriptions: '#ff8d85',
  Entertainment: '#ccbe00', Shopping: '#39e3ef', Travel: '#1dd1a1',
  Health: '#9d52ff', Insurance: '#9aa2ea', Fees: '#d31730',
  Donations: '#ff0ade', Beauty: '#cc0074', Education: '#0c8599',
  Uncategorized: '#7a7aff',
  // Reserved "Ignored" marker: deliberately muted — it is not a place
  // money went, just a row excluded from the totals.
  Ignored: '#8a90a2',
};
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June',
                     'July', 'August', 'September', 'October', 'November', 'December'];

const $id = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmtMoney(n) {
  const sign = n < 0 ? '-' : '';
  return sign + '$' + Math.abs(n).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtAxis(n) {
  const sign = n < 0 ? '-$' : '$';
  const a = Math.abs(n);
  if (a >= 1000) {
    return sign + (a / 1000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, '') + 'k';
  }
  return sign + Math.round(a);
}

function monthLabel(m) { // 'YYYY-MM' -> 'Sep 2025'
  const [y, mm] = m.split('-');
  return MONTHS_SHORT[+mm - 1] + ' ' + y;
}

function monthLong(m) { // 'YYYY-MM' -> 'September 2025'
  const [y, mm] = m.split('-');
  return MONTHS_FULL[+mm - 1] + ' ' + y;
}

function getParam(name) {
  return new URLSearchParams(location.search).get(name);
}

async function api(url, opt) {
  const r = await fetch(url, opt);
  if (!r.ok) {
    let d = '';
    try { d = (await r.json()).detail || ''; } catch (e) { /* not json */ }
    throw new Error(d || r.status);
  }
  return r.json();
}

function toast(msg, ms = 2500) {
  const t = $id('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), ms);
}
