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
    const err = new Error(d || r.status);
    err.status = r.status;
    throw err;
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

/* ------------------------------------------------------------- favicon
 * The tab/bookmark icon mirrors the .brand .mark tile: a 135° accent
 * gradient rounded square carrying the "Hg" symbol. Each page's <head>
 * ships the static default-scheme .ico; this swaps in a data-URI SVG
 * built from the active scheme's computed tokens, so the tab icon
 * matches the header tile in any color scheme. Runs in <head>, after
 * app.css — the scheme was applied pre-paint, so the tokens are final. */
function mixSrgb(a, b, t) { // === color-mix(in srgb, a t%, b (100-t)%)
  const p = c => { c = c.replace('#', '');
    if (c.length === 3) c = c.split('').map(x => x + x).join('');
    return [0, 2, 4].map(i => parseInt(c.slice(i, i + 2), 16)); };
  const A = p(a), B = p(b);
  return '#' + A.map((v, i) =>
    Math.round(v * t + B[i] * (1 - t)).toString(16).padStart(2, '0')).join('');
}

function paintFavicon() {
  try {
    const s = getComputedStyle(document.documentElement);
    const accent = s.getPropertyValue('--accent').trim();
    const text = s.getPropertyValue('--text').trim();
    const onAccent = s.getPropertyValue('--on-accent').trim();
    // Non-hex tokens (e.g. a future rgb()/color-mix value) -> keep .ico.
    if (!['accent', 'text'].every(k => /^#[0-9a-f]{3,6}$/i.test(k === 'accent' ? accent : text))
        || !/^#[0-9a-f]{3,6}$/i.test(onAccent)) return;
    const mix = mixSrgb(accent, text, 0.55);
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
      '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="' + accent + '"/>' +
      '<stop offset="1" stop-color="' + mix + '"/></linearGradient></defs>' +
      '<rect x="1.5" y="1.5" width="61" height="61" rx="17" fill="url(#g)"/>' +
      '<text x="32" y="32" text-anchor="middle" dominant-baseline="central" ' +
      'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif" ' +
      'font-size="27" font-weight="700" letter-spacing="0.3" fill="' + onAccent + '">Hg</text></svg>';
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    link.type = 'image/svg+xml';
    link.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
  } catch (e) { /* keep the static .ico */ }
}
paintFavicon();
