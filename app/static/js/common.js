/* Mercury — shared helpers: API, formatting, palette. Loaded before ui.js. */

const CAT_COLORS = {
  Groceries: '#4f8cff', Dining: '#ff9f43', Transportation: '#54a0ff',
  Housing: '#a55eea', Utilities: '#26de81', Subscriptions: '#ff6b6b',
  Entertainment: '#feca57', Shopping: '#48dbfb', Travel: '#1dd1a1',
  Health: '#576574', Insurance: '#c8d6e5', Investing: '#6ab04c',
  Income: '#38c172', Fees: '#b33939', Transfers: '#8a90a2',
  Donations: '#e84393', Education: '#2d98da', Other: '#57606f',
  Uncategorized: '#8a90a2',
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
