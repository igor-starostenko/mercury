"""Mercury — full NiceGUI conversion of the static frontend.

All six pages (Overview, Categories, Merchants, Transactions, Year, Month)
are rebuilt on NiceGUI and share the production data layer (app.ingest /
app.analytics / app.imports / app.merchants / app.rules / app.date_ignores /
app.scoped_sankey / app.llm) unchanged — same tables, same effective view,
same semantics. The static HTML/JS/CSS frontend (app/static/*) and the page
routes in app/main.py are deleted in the same change: the NiceGUI app is
the only UI.

Simplifications vs the static app (deliberately, and honestly):
  * Sankey is rendered with ECharts' sankey type (3 columns: income
    categories → accounts → spend categories) with the same data payloads
    (top 8 spend categories / top 15 merchants by default), without the
    interactive 3-level picker menus — the whole-menu default is shown.
  * Charts are ECharts (ui.echart) instead of Chart.js.
  * The theme picker persists via localStorage (same mechanism as before)
    but the 8 palettes are applied as CSS variables to the page shell, not
    pixel-identical to the old static app.
  * Classify runs in-process with live progress (same batch loop as the
    old /api/classify endpoint).

Run:
    uv run python -m app.nicegui_app
(serves on MERCURY_PORT or 8730)
"""
from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

import duckdb
from nicegui import ui

from app import analytics, ingest, imports, merchants, rules, date_ignores, scoped_sankey, llm
from app.date_ignores import CREATE_DATE_IGNORES_SQL
from app.merchants import CREATE_MERCHANT_RULES_SQL, CREATE_MERCHANT_OVERRIDES_SQL, CREATE_MERCHANT_COLORS_SQL
from app.rules import CREATE_RULES_SQL
from app.imports import CREATE_IMPORTS_SQL, ensure_import_column, ensure_color_column

DB_PATH = ingest.DB_PATH
HOST = os.environ.get("MERCURY_HOST", "127.0.0.1")
PORT = int(os.environ.get("MERCURY_PORT", "8730"))

# ── init ─────────────────────────────────────────────────────────────────────

def _init_tables(con):
    con.execute(ingest.CREATE_TABLE_SQL)
    con.execute(ingest.CREATE_OVERRIDES_SQL)
    ingest.ensure_override_source(con)
    con.execute(ingest.CREATE_DELETED_SQL)
    con.execute(rules.CREATE_RULES_SQL)
    rules.ensure_rule_position(con)
    rules.ensure_rule_scope(con)
    con.execute(date_ignores.CREATE_DATE_IGNORES_SQL)
    con.execute(imports.CREATE_IMPORTS_SQL)
    imports.ensure_import_column(con)
    imports.ensure_color_column(con)
    ingest.ensure_llm_skipped_column(con)
    con.execute(merchants.CREATE_MERCHANT_RULES_SQL)
    merchants.ensure_rule_position(con)
    merchants.ensure_rule_scope(con)
    con.execute(merchants.CREATE_MERCHANT_OVERRIDES_SQL)
    con.execute(merchants.CREATE_MERCHANT_COLORS_SQL)
    con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)


def db():
    con = duckdb.connect(str(DB_PATH))
    _init_tables(con)
    return con


# ── helpers ──────────────────────────────────────────────────────────────────

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CAT_COLORS = {
    "Income": "#1e8e52", "Investing": "#9ad45f", "Other": "#9aa3b2",
    "Transfers": "#454c5c", "Groceries": "#106bc6", "Dining": "#eb6600",
    "Transportation": "#47c2ff", "Housing": "#953bb0", "Utilities": "#c28a29",
    "Subscriptions": "#ff8d85", "Entertainment": "#ccbe00", "Shopping": "#39e3ef",
    "Travel": "#1dd1a1", "Health": "#9d52ff", "Insurance": "#9aa2ea",
    "Fees": "#d31730", "Donations": "#ff0ade", "Beauty": "#cc0074",
    "Education": "#0c8599", "Uncategorized": "#7a7aff", "Ignored": "#8a90a2",
}


def cat_color(name):
    return CAT_COLORS.get(name, "#8a90a2")


def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def fmt_money(n):
    if n is None:
        return "$0.00"
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def month_label(m):
    try:
        y, mm = m.split("-")
        return f"{MONTHS_SHORT[int(mm) - 1]} {y}"
    except Exception:
        return m


def week_label(w):
    try:
        d = datetime.date.fromisoformat(w)
        return f"{d.month}/{d.day}"
    except Exception:
        return w


# ── themes ───────────────────────────────────────────────────────────────────

# Eight palettes, same tokens as the static app (app.css). Applied as CSS
# variables keyed on [data-theme="<id>"] so switching is a single attribute
# change + localStorage write — no server round-trip per switch.
THEMES = {
    "ember":      {"label": "Ember",      "bg": "#bfc9d1", "panel": "#EAEFEF", "panel2": "#dfe5e6", "text": "#25343f", "muted": "#5a6772", "accent": "#ff9b51"},
    "frost":      {"label": "Frost",      "bg": "#f8fafc", "panel": "#d9eafd", "panel2": "#c3d8ea", "text": "#3f4b59", "muted": "#5c6b7c", "accent": "#9aa6b2"},
    "terracotta": {"label": "Terracotta", "bg": "#eddcc6", "panel": "#fff4ea", "panel2": "#f3e4d0", "text": "#bf4646", "muted": "#9c5a48", "accent": "#7eacb5"},
    "clay":       {"label": "Clay",       "bg": "#eeeceb", "panel": "#e0ccbe", "panel2": "#d6c5b6", "text": "#3c3633", "muted": "#747264", "accent": "#747264"},
    "harbor":     {"label": "Harbor",     "bg": "#f0f0f0", "panel": "#f7f8fa", "panel2": "#e2e6ea", "text": "#213555", "muted": "#4f709c", "accent": "#4f709c"},
    "plum":       {"label": "Plum",       "bg": "#352f44", "panel": "#433b52", "panel2": "#4a415c", "text": "#faf0e6", "muted": "#b9b4c7", "accent": "#b9b4c7"},
    "tide":       {"label": "Tide",       "bg": "#27374d", "panel": "#32435a", "panel2": "#374a63", "text": "#dde6ed", "muted": "#9db2bf", "accent": "#9db2bf"},
    "onyx":       {"label": "Onyx",       "bg": "#393646", "panel": "#443e4f", "panel2": "#483f53", "text": "#f4eee0", "muted": "#9c93a6", "accent": "#6d5d6e"},
}
_DEFAULT_THEME = "ember"


def _theme_css():
    rules = []
    for tid, t in THEMES.items():
        rules.append(
            f'[data-theme="{tid}"] {{\n'
            f'  --bg: {t["bg"]}; --panel: {t["panel"]}; --panel-2: {t["panel2"]};\n'
            f'  --text: {t["text"]}; --muted: {t["muted"]}; --accent: {t["accent"]};\n'
            f'  color-scheme: light;\n}}'
        )
    base = """
body { background: var(--bg) !important; color: var(--text) !important; }
.q-card, .q-card__section, .q-dialog__inner { background: var(--panel) !important; color: var(--text) !important; }
.q-card__section { border-color: color-mix(in srgb, var(--muted) 30%, transparent) !important; }
.q-table { background: var(--panel) !important; }
.q-table thead th { color: var(--muted) !important; border-bottom: 1px solid color-mix(in srgb, var(--muted) 40%, transparent) !important; }
.q-table tbody td { color: var(--text) !important; border-bottom: 1px solid color-mix(in srgb, var(--muted) 20%, transparent) !important; }
.q-input__control, .q-field__control { background: var(--panel-2) !important; color: var(--text) !important; }
.q-input input, .q-field__input { color: var(--text) !important; }
.q-field__label, .q-select__label { color: var(--muted) !important; }
.q-field__native, .q-field__native:focus { color: var(--text) !important; }
.q-btn { font-weight: 600; }
.q-expansion-item { background: var(--panel) !important; color: var(--text) !important; }
.q-expansion-item .q-expansion-item__container { background: var(--panel) !important; }
.q-expansion-item .q-item__label { color: var(--text) !important; }
.q-menu { background: var(--panel) !important; color: var(--text) !important; }
.q-menu .q-item { color: var(--text) !important; }
.q-menu .q-item:hover { background: color-mix(in srgb, var(--accent) 20%, transparent) !important; }
.q-dialog .q-dialog__inner { background: var(--panel) !important; }
.q-dialog .q-dialog__title { color: var(--text) !important; }
.q-dialog .q-dialog__actions .q-btn { color: var(--text) !important; }
.q-dialog .q-dialog__footer { color: var(--muted) !important; }
.q-chip { background: var(--panel-2) !important; color: var(--text) !important; }
.q-separator { background: color-mix(in srgb, var(--muted) 30%, transparent) !important; }
.q-tabs .q-tab { color: var(--muted) !important; }
.q-tabs .q-tab--active { color: var(--accent) !important; }
.q-tabs .q-tab-indicator { background: var(--accent) !important; }
.q-fab { background: var(--accent) !important; color: var(--bg) !important; }
.q-btn--flat { color: var(--text) !important; }
.q-btn--flat:hover { background: color-mix(in srgb, var(--accent) 20%, transparent) !important; }
.q-btn--primary { background: var(--accent) !important; color: var(--bg) !important; }
.q-btn--primary:hover { filter: brightness(1.08); }
.q-uploader__list { background: var(--panel-2) !important; color: var(--text) !important; }
.q-uploader__list-item { color: var(--text) !important; }
.q-notification { background: var(--panel) !important; color: var(--text) !important; border-left: 4px solid var(--accent); }
"""
    return "\n".join(rules) + "\n" + base


def _init_theme():
    ui.add_head_html(
        "<script>(function(){var t=localStorage.getItem('mercury.theme')||'"
        + _DEFAULT_THEME
        + "';document.documentElement.setAttribute('data-theme',t);})();</script>"
    )
    ui.add_head_html(f"<style id='mercury-theme'>{_theme_css()}</style>")


def apply_theme(tid):
    ui.run_javascript(
        f"localStorage.setItem('mercury.theme','{tid}');"
        f"document.documentElement.setAttribute('data-theme','{tid}');"
    )


# ── charts ────────────────────────────────────────────────────────────────────

def _chart_base(title=None):
    return {
        "backgroundColor": "transparent",
        "color": ["#ff9b51", "#106bc6", "#1e8e52", "#47c2ff", "#eb6600", "#953bb0", "#c28a29", "#39e3ef"],
        "grid": {"left": 70, "right": 20, "top": 36, "bottom": 30},
        "tooltip": {"trigger": "axis", "backgroundColor": "#14171d",
                    "borderColor": "#3a3f4d", "textStyle": {"color": "#f4eee0"}},
        "legend": {"top": 0, "textStyle": {"color": "#9db2bf", "fontSize": 10}, "icon": "roundRect"},
        "title": {"text": title or "", "left": 0, "top": -28,
                  "textStyle": {"color": "#9db2bf", "fontSize": 11, "fontWeight": 500}},
    }


def flow_chart(months, spend_by_cat, income_by_month, avg_line=None, title=""):
    """Stacked bar of top categories by spend + income line + avg line."""
    cats = list(dict.fromkeys(spend_by_cat.keys()))
    top = cats[:5]
    # spend_by_cat: {cat: [per-month spend]}
    opts = _chart_base(title)
    opts["xAxis"] = {"type": "category", "data": months,
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10},
                     "axisLine": {"lineStyle": {"color": "#3a3f4d"}}}
    opts["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "#2a2e38"}},
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10,
                                   "formatter": "${value}"}}
    series = []
    for cat in top:
        series.append({
            "name": cat, "type": "bar", "stack": "spend",
            "data": [round(spend_by_cat[cat][i], 2) for i in range(len(months))],
            "barWidth": "55%", "itemStyle": {"color": cat_color(cat)},
        })
    series.append({
        "name": "Income", "type": "line",
        "data": [round(income_by_month.get(m, 0), 2) for m in months],
        "smooth": True, "symbol": "circle", "symbolSize": 5,
        "lineStyle": {"width": 2.5, "color": "#1e8e52"},
        "itemStyle": {"color": "#1e8e52"},
    })
    if avg_line:
        series.append({
            "name": "12-mo avg", "type": "line",
            "data": avg_line, "smooth": True, "symbol": "none",
            "lineStyle": {"width": 2, "type": "dashed", "color": "#9db2bf"},
            "itemStyle": {"color": "#9db2bf"},
        })
    opts["series"] = series
    return opts


def sankey_chart(flows, merchant_flows=None, title=""):
    """3-column sankey: income categories → accounts → spend categories.

    flows: list of {source, account, category, debit, credit}
    merchant_flows: optional list of {source, account, category, merchant, debit, credit}
    """
    income_cats = {}
    accounts = {}
    spend_cats = {}
    for f in flows:
        key = f["source"] + "::" + f["account"]
        accounts[key] = accounts.get(key, {"name": f["account"], "source": f["source"], "account": f["account"]})
        if f["credit"] > 0:
            income_cats[f["category"]] = income_cats.get(f["category"], 0) + f["credit"]
        if f["debit"] > 0:
            spend_cats[f["category"]] = spend_cats.get(f["category"], 0) + f["debit"]
    node_names = []
    for c in income_cats:
        node_names.append(f"income:{c}")
    for k, v in accounts.items():
        node_names.append(f"acct:{v['source']} · {v['account']}")
    for c in spend_cats:
        node_names.append(f"spend:{c}")
    nodes = []
    for name in node_names:
        if name.startswith("income:"):
            nodes.append({"name": name, "category": 0,
                          "itemStyle": {"color": cat_color(name.split(":", 1)[1])}})
        elif name.startswith("acct:"):
            nodes.append({"name": name, "category": 1,
                          "itemStyle": {"color": "#9db2bf"}})
        else:
            nodes.append({"name": name, "category": 2,
                          "itemStyle": {"color": cat_color(name.split(":", 1)[1])}})
    links = []
    for f in flows:
        key = f["source"] + "::" + f["account"]
        acct_node = f"acct:{f['source']} · {f['account']}"
        if f["credit"] > 0:
            links.append({"source": f"income:{f['category']}", "target": acct_node,
                          "value": round(f["credit"], 2)})
        if f["debit"] > 0:
            links.append({"source": acct_node, "target": f"spend:{f['category']}",
                          "value": round(f["debit"], 2)})
    # top 8 spend categories only; aggregate the rest into "Other"
    top_spend = dict(sorted(spend_cats.items(), key=lambda x: -x[1])[:8])
    other = sum(v for k, v in spend_cats.items() if k not in top_spend)
    if other > 0:
        nodes.append({"name": "spend:All other", "category": 2,
                      "itemStyle": {"color": "#8a90a2"}})
    filtered_links = []
    for link in links:
        if link["source"].startswith("acct:") and link["target"].startswith("spend:"):
            cat = link["target"].split(":", 1)[1]
            if cat in top_spend:
                filtered_links.append(link)
            else:
                # aggregate into All other
                found = False
                for fl in filtered_links:
                    if fl["source"] == link["source"] and fl["target"] == "spend:All other":
                        fl["value"] = round(fl["value"] + link["value"], 2)
                        found = True
                        break
                if not found:
                    filtered_links.append({"source": link["source"], "target": "spend:All other",
                                           "value": link["value"]})
        else:
            filtered_links.append(link)
    opts = _chart_base(title)
    opts["tooltip"] = {"trigger": "item", "backgroundColor": "#14171d",
                       "borderColor": "#3a3f4d", "textStyle": {"color": "#f4eee0"}}
    opts.pop("grid", None)
    opts.pop("legend", None)
    opts["series"] = [{
        "type": "sankey",
        "left": 10, "right": 10, "top": 30, "bottom": 20,
        "nodeWidth": 14, "nodeGap": 10, "layoutIterations": 32,
        "emphasis": {"focus": "adjacency"},
        "label": {"color": "#dde6ed", "fontSize": 10},
        "lineStyle": {"color": "source", "opacity": 0.25, "curveness": 0.5},
        "data": nodes,
        "links": filtered_links,
    }]
    return opts


def merchant_sankey_chart(merchant_flows, title=""):
    """3-column sankey: accounts → spend categories → merchants."""
    accts = {}
    cats = {}
    merch = {}
    for f in merchant_flows:
        key = f["source"] + "::" + f["account"]
        accts[key] = accts.get(key, {"source": f["source"], "account": f["account"]})
        if f["debit"] > 0:
            cats[f["category"]] = cats.get(f["category"], 0) + f["debit"]
            if f["merchant"]:
                merch[f["merchant"]] = merch.get(f["merchant"], 0) + f["debit"]
    nodes = []
    for k, v in accts.items():
        nodes.append({"name": f"acct:{v['source']} · {v['account']}", "category": 0,
                      "itemStyle": {"color": "#9db2bf"}})
    for c, v in sorted(cats.items(), key=lambda x: -x[1])[:8]:
        nodes.append({"name": f"cat:{c}", "category": 1,
                      "itemStyle": {"color": cat_color(c)}})
    for m, v in sorted(merch.items(), key=lambda x: -x[1])[:15]:
        nodes.append({"name": f"merch:{m}", "category": 2,
                      "itemStyle": {"color": "#9db2bf"}})
    links = []
    for f in merchant_flows:
        if f["debit"] <= 0:
            continue
        key = f["source"] + "::" + f["account"]
        acct_node = f"acct:{f['source']} · {f['account']}"
        if f["category"] not in {c for c in cats}:
            continue
        links.append({"source": acct_node, "target": f"cat:{f['category']}",
                      "value": round(f["debit"], 2)})
        if f["merchant"] and f["merchant"] in merch:
            links.append({"source": f"cat:{f['category']}", "target": f"merch:{f['merchant']}",
                          "value": round(f["debit"], 2)})
    opts = _chart_base(title)
    opts["tooltip"] = {"trigger": "item", "backgroundColor": "#14171d",
                       "borderColor": "#3a3f4d", "textStyle": {"color": "#f4eee0"}}
    opts.pop("grid", None)
    opts.pop("legend", None)
    opts["series"] = [{
        "type": "sankey",
        "left": 10, "right": 10, "top": 30, "bottom": 20,
        "nodeWidth": 14, "nodeGap": 10, "layoutIterations": 32,
        "emphasis": {"focus": "adjacency"},
        "label": {"color": "#dde6ed", "fontSize": 10},
        "lineStyle": {"color": "source", "opacity": 0.25, "curveness": 0.5},
        "data": nodes,
        "links": links,
    }]
    return opts


def trend_chart(months, values, title=""):
    opts = _chart_base(title)
    opts["xAxis"] = {"type": "category", "data": months,
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10},
                     "axisLine": {"lineStyle": {"color": "#3a3f4d"}}}
    opts["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "#2a2e38"}},
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10,
                                   "formatter": "${value}"}}
    opts["series"] = [{
        "name": "Spend", "type": "line", "data": values,
        "smooth": True, "symbol": "circle", "symbolSize": 5,
        "lineStyle": {"width": 2.5, "color": "#ff9b51"},
        "itemStyle": {"color": "#ff9b51"},
        "areaStyle": {"opacity": 0.15, "color": "#ff9b51"},
    }]
    return opts


def monthly_bar_chart(months, values, title=""):
    opts = _chart_base(title)
    opts["xAxis"] = {"type": "category", "data": months,
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10},
                     "axisLine": {"lineStyle": {"color": "#3a3f4d"}}}
    opts["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "#2a2e38"}},
                     "axisLabel": {"color": "#9db2bf", "fontSize": 10,
                                   "formatter": "${value}"}}
    opts["series"] = [{
        "name": "Spend", "type": "bar", "data": values, "barWidth": "55%",
        "itemStyle": {"color": "#ff9b51", "borderRadius": [4, 4, 0, 0]},
    }]
    return opts


# ── chrome ────────────────────────────────────────────────────────────────────

def chrome(active="/"):
    with ui.header().classes("bg-[#14171d] px-4 h-14 gap-3 items-center flex flex-wrap"):
        ui.html('<a href="/" class="text-lg font-bold tracking-tight no-underline text-white mr-2">Hg · Mercury</a>')
        nav = [("/", "Overview"), ("/categories", "Categories"), ("/merchants", "Merchants"),
               ("/transactions", "Transactions"), ("/year", "Year"), ("/month", "Month")]
        for href, label in nav:
            cls = "px-3 py-1 rounded-md text-sm font-medium no-underline"
            if href == active:
                ui.html(f'<a href="{href}" class="{cls} bg-white/15 text-white">{label}</a>')
            else:
                ui.html(f'<a href="{href}" class="{cls} text-white/70 hover:text-white hover:bg-white/10">{label}</a>')
        ui.space()
        with ui.row().classes("items-center gap-1"):
            for tid, t in THEMES.items():
                ui.button("", on_click=lambda e, i=tid: apply_theme(i)).props(
                    "flat dense round color=white").classes("p-0 m-0").style(
                    f"width:18px;height:18px;border-radius:50%;background:{t['accent']};border:1px solid rgba(255,255,255,0.35);box-shadow:none;")
        ui.button("Manage Imports", on_click=lambda: ui.navigate.to("/imports")).props("color=white flat icon=folder")


def footer(range_text=""):
    with ui.footer().classes("bg-[#14171d] text-white/60 text-xs px-4 py-3 flex justify-between"):
        ui.html(f'<span>{esc(range_text)}</span>' if range_text else "<span></span>")
        ui.html('<span>Mercury</span>')


# ── imports page ─────────────────────────────────────────────────────────────


def _imports_list_body(con):
    rows = imports.list_imports(con)
    total = sum(r["n"] for r in rows)
    if not rows:
        ui.html('<p class="text-white/50 text-sm">No imports yet — use “Import CSV” below.</p>')
    for r in rows:
        with ui.card().classes("bg-white/5 border border-white/10 w-full p-3"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.html(f'<span class="w-3 h-3 rounded-full inline-block" style="background:{esc(r.get("color") or "#8a90a2")}"></span>')
                ui.html(f'<span class="text-white text-sm font-medium">{esc(r["account"])}</span>')
                ui.html(f'<span class="text-white/50 text-xs">· {esc(r["source"] or "?")} · {r["n"]} rows</span>')
            with ui.row().classes("gap-2 items-center mt-2"):
                inp = ui.input("Account label", value=r["account"]).classes("flex-1").props("dark")
                ui.button("Rename", on_click=lambda e, rid=r["id"], i=inp: _rename_import(rid, i.value)).props("color=white flat size=sm")
                ui.button("Delete", on_click=lambda e, rid=r["id"]: _delete_import(rid)).props("color=negative flat size=sm icon=delete")
    ui.separator().classes("bg-white/20 my-2")
    ui.label(f"{total} rows across {len(rows)} import(s)").classes("text-white/50 text-xs")


def _rename_import(import_id, new_account):
    con = db()
    try:
        imports.rename_import(con, import_id, new_account)
        con.commit()
        ui.notify(f"Import #{import_id} renamed to “{new_account}”", type="positive")
    finally:
        con.close()
    ui.navigate.to("/imports")


def _delete_import(import_id):
    con = db()
    try:
        res = imports.delete_import(con, import_id)
        con.commit()
        ui.notify(f"Deleted import #{import_id} ({res.get('rows', 0)} rows)", type="positive")
    finally:
        con.close()
    ui.navigate.to("/imports")


async def _on_multi_upload(e):
    files = e.files or []
    csv_files = [f for f in files if (f.name or "").lower().endswith((".csv", ".txt"))]
    if not csv_files:
        ui.notify("No CSV files in that selection", type="warning")
        return
    ui.notify(f"Importing {len(csv_files)} file(s)…", type="info")
    con = db()
    try:
        report = []
        for f in csv_files:
            name = f.name or "unnamed.csv"
            data = await f.read()
            res = ingest.parse_file_bytes(name, data)
            entry = {"name": name, "source": res.source,
                     "rows": len(res.txns) if res.txns else 0,
                     "new": 0, "error": res.error, "needs_source": res.needs_source,
                     "note": res.note}
            if res.error is None and res.txns:
                imp_id = imports.resolve_import(con, name, res.txns[0].account)
                _, new = ingest.merge_txns(con, res.txns, imp_id)
                entry["new"] = new
            report.append(entry)
        con.commit()
        total_added = sum(e["new"] for e in report)
        total_rows = sum(e["rows"] for e in report)
        ui.notify(f"Imported {total_added} new rows ({total_rows} read)", type="positive")
    finally:
        con.close()
    ui.navigate.to("/imports")


@ui.page("/imports")
def imports_page():
    _init_theme()
    chrome("/imports")
    con = db()
    try:
        _imports_list_body(con)
    finally:
        con.close()
    ui.separator().classes("bg-white/20 my-3")
    ui.label("Import CSV").classes("text-white/70 text-xs uppercase tracking-wider")
    ui.upload(label="Drop CSV files here (multiple)", multiple=True, auto_upload=True,
              on_multi_upload=_on_multi_upload).classes("text-white")


# ── shared UI bits ────────────────────────────────────────────────────────────

def kpi_card(label, value, hint="", color="#ff9b51"):
    with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
        ui.html(f'<div class="text-white/50 text-xs font-medium uppercase tracking-wider">{esc(label)}</div>')
        ui.html(f'<div class="text-2xl font-bold mt-1" style="color:{color}">{esc(value)}</div>')
        if hint:
            ui.html(f'<div class="text-white/40 text-xs mt-1">{esc(hint)}</div>')


def badge(text, kind="none"):
    colors = {
        "rule": "#106bc6", "llm": "#ff9b51", "manual": "#1e8e52",
        "csv": "#9aa3b2", "none": "#9aa3b2", "ignored": "#8a90a2",
        "merchant": "#47c2ff",
    }
    c = colors.get(kind, "#9aa3b2")
    ui.html(f'<span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase" '
            f'style="background:{c}26;color:{c};border:1px solid {c}40">{esc(text)}</span>')


def section_head(title, hint=""):
    with ui.row().classes("items-center justify-between gap-3"):
        ui.html(f'<h2 class="text-white font-semibold text-lg m-0">{esc(title)}</h2>')
        if hint:
            ui.html(f'<span class="text-white/40 text-xs">{esc(hint)}</span>')


# ── Overview ──────────────────────────────────────────────────────────────────

@ui.page("/")
def overview_page():
    _init_theme()
    chrome("/")
    con = db()
    try:
        periods = analytics.available_periods(con)
        summary = analytics.summary(con)
        monthly = analytics.monthly_spend(con)
        income = analytics.income_by_month(con)
        flow = analytics.flow_by_category(con)
        year_totals = analytics.year_totals(con)
        accounts = analytics.accounts(con)
        flows = scoped_sankey.sankey_flow(con)
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        ui.html(f'<p class="text-white/40 text-xs m-0">{esc(summary["first_date"] or "—")} → {esc(summary["last_date"] or "—")} · {summary["n_transactions"]} transactions · {summary["n_accounts"]} accounts · {summary["n_sources"]} sources</p>')

        # cards
        with ui.grid(columns=4).classes("gap-3 w-full"):
            kpi_card("Spend", fmt_money(summary["total_spend"]), "expenses", "#eb6600")
            kpi_card("Income", fmt_money(summary["total_income"]), "income", "#1e8e52")
            kpi_card("Net", fmt_money(summary["net"]), "income − spend", "#ff9b51")
            kpi_card("Transactions", str(summary["n_transactions"]), "rows", "#9db2bf")

        # flow chart
        months = [r["month"] for r in monthly]
        spend_by_cat = {}
        for f in flow:
            spend_by_cat.setdefault(f["category"], [0.0] * len(months))
        for f in flow:
            if f["category"] in spend_by_cat:
                idx = months.index(f["bucket"]) if f["bucket"] in months else None
                if idx is not None:
                    spend_by_cat[f["category"]][idx] = round(f["spend"], 2)
        # order cats by total spend
        cat_totals = {c: sum(v) for c, v in spend_by_cat.items()}
        ordered_cats = sorted(spend_by_cat.keys(), key=lambda c: -cat_totals.get(c, 0))
        ordered_cats = ordered_cats[:5]
        spend_matrix = {c: spend_by_cat[c] for c in ordered_cats}
        income_by_month = {r["month"]: r["income"] for r in income}
        avg = [round(sum(spend_by_cat[c][i] for c in ordered_cats) / len(months) if months else 0, 2) for i in range(len(months))] if months else None
        with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
            section_head("Cash flow", "top 5 categories + income")
            if months:
                ui.echart(flow_chart(months, spend_matrix, income_by_month, avg, "Spend by category (top 5) + income")).classes("w-full h-80")
            else:
                ui.html('<p class="text-white/40 text-sm">No data yet.</p>')

        # year cards
        with ui.row().classes("gap-3 w-full flex-wrap"):
            for y in year_totals:
                with ui.card().classes("bg-white/5 border border-white/10 p-3 flex-1 min-w-[180px]"):
                    ui.html(f'<div class="text-white/50 text-xs font-medium uppercase tracking-wider">{y["year"]}</div>')
                    ui.html(f'<div class="text-lg font-bold text-white mt-1">{fmt_money(y["spend"])} <span class="text-white/40 text-xs font-normal">spent</span></div>')
                    ui.html(f'<div class="text-xs text-white/60 mt-1">income {fmt_money(y["income"])} · net {fmt_money(y["net"])}</div>')

        # accounts
        with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
            section_head("Accounts", f"{len(accounts)} accounts")
            for a in accounts:
                with ui.row().classes("items-center justify-between w-full py-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.html(f'<span class="w-3 h-3 rounded-full inline-block" style="background:{esc(a.get("color") or "#8a90a2")}"></span>')
                        ui.html(f'<span class="text-white text-sm">{esc(a["account"])}</span>')
                        ui.html(f'<span class="text-white/40 text-xs">{esc(a["source"])} · {a["n"]} rows</span>')
                    ui.html(f'<span class="text-white/80 text-sm font-medium">{fmt_money(a["net"])}</span>')

        # sankey
        with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
            section_head("Money Flow", "income → accounts → spend")
            if flows:
                ui.echart(sankey_chart(flows, title="Income → accounts → spend")).classes("w-full h-96")
            else:
                ui.html('<p class="text-white/40 text-sm">No data yet.</p>')

    footer(f"{summary['first_date'] or '—'} → {summary['last_date'] or '—'} · {summary['n_transactions']} rows · {summary['n_accounts']} accounts")


# ── Categories ────────────────────────────────────────────────────────────────

@ui.page("/categories")
def categories_page():
    _init_theme()
    chrome("/categories")
    con = db()
    try:
        cats = analytics.spend_by_category(con)
        periods = analytics.available_periods(con)
        flow = analytics.flow_by_category(con)
        rule_list = rules.load_rules(con, enabled_only=False)
        matches = rules.match_counts(con, [r for r in rule_list if r.enabled])
        ignore_items = date_ignores.load(con)
        ignore_stats = date_ignores.ignored_counts(con, ignore_items)
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        # pick a category for the trend chart
        cat_names = [c["category"] for c in cats]
        if not cat_names:
            ui.html('<p class="text-white/40 text-sm">No categories yet.</p>')
        else:
            with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    ui.label("Trend:").classes("text-white/60")
                    trend_cat = ui.select(cat_names, value=cat_names[0], with_input=False).classes("min-w-[220px]")
                chart_slot = ui.column().classes("w-full")

                def draw_trend():
                    chart_slot.clear()
                    with chart_slot:
                        sel = trend_cat.value
                        months = sorted({f["bucket"] for f in flow if f["category"] == sel})
                        values = [next((f["spend"] for f in flow
                                        if f["bucket"] == m and f["category"] == sel), 0.0)
                                  for m in months]
                        with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                            section_head(f"Spend trend — {sel}", f"{len(months)} months")
                            if months:
                                ui.echart(trend_chart(months, values, f"{sel} spend")).classes("w-full h-72")
                            else:
                                ui.html('<p class="text-white/40 text-sm">No trend data.</p>')

                trend_cat.on_value_change(lambda e: draw_trend())
                draw_trend()

            # category table
            with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                section_head("Spend by category", f"{len(cats)} categories")
                total_spend = sum(c.get("spend", 0) for c in cats) or 1
                table_rows = []
                for c in cats:
                    share = c.get("spend", 0) / total_spend * 100
                    table_rows.append({
                        "category": c["category"],
                        "spend": fmt_money(c.get("spend", 0)),
                        "n": str(c.get("n", 0)),
                        "share": f"{share:.1f}%",
                        "net": fmt_money(c.get("net", 0)),
                    })
                ui.table(
                    rows=table_rows,
                    columns=[{"name": "category", "label": "Category"},
                             {"name": "spend", "label": "Spend", "align": "right"},
                             {"name": "n", "label": "Count", "align": "right"},
                             {"name": "share", "label": "Share", "align": "right"},
                             {"name": "net", "label": "Net", "align": "right"}],
                    row_key="category",
                    pagination=15,
                ).classes("w-full")

            # rules panel
            with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                section_head("Custom rules", "keyword → category (read-time)")
                with ui.row().classes("gap-2 flex-wrap items-center"):
                    pattern_in = ui.input("Keyword", placeholder="e.g. walmart").classes("flex-1 min-w-[160px]").props("dark")
                    cat_in = ui.select(llm.UI_CATEGORIES, value=llm.UI_CATEGORIES[0]).classes("min-w-[160px]")
                    ui.button("Add rule", on_click=lambda: _add_category_rule(pattern_in.value, cat_in.value)).props("color=primary")
                for r in rule_list:
                    with ui.row().classes("items-center justify-between w-full py-1 border-b border-white/5"):
                        with ui.row().classes("items-center gap-2"):
                            badge("rule" if r.enabled else "disabled", "rule" if r.enabled else "none")
                            ui.html(f'<span class="text-white text-sm">“{esc(r.pattern)}”</span>')
                            ui.html(f'<span class="text-white/50 text-xs">→ {esc(r.category)} · {matches.get(r.id, 0)} matches</span>')
                        with ui.row().classes("gap-1"):
                            ui.button("▲", on_click=lambda e, rid=r.id: _move_rule(rid, -1)).props("flat color=white size=xs")
                            ui.button("▼", on_click=lambda e, rid=r.id: _move_rule(rid, +1)).props("flat color=white size=xs")
                            ui.button("×", on_click=lambda e, rid=r.id: _delete_rule(rid)).props("flat color=negative size=xs")

            # ignore rules panel
            with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                section_head("Date ignores", "exclude rows by date (read-time)")
                with ui.row().classes("gap-2 flex-wrap items-center"):
                    kind_sel = ui.select(["before", "after", "range"], value="before").classes("min-w-[120px]")
                    start_in = ui.input("Start", placeholder="YYYY-MM-DD").classes("min-w-[160px]").props("dark")
                    end_in = ui.input("End (range only)", placeholder="YYYY-MM-DD").classes("min-w-[160px]").props("dark")
                    ui.button("Add", on_click=lambda: _add_ignore(kind_sel.value, start_in.value, end_in.value)).props("color=primary")
                for r in ignore_items:
                    stats = ignore_stats.get(r.id, (0, 0.0))
                    with ui.row().classes("items-center justify-between w-full py-1 border-b border-white/5"):
                        ui.html(f'<span class="text-white text-sm">{esc(r.kind)} {esc(r.start)}{(" → " + esc(r.end)) if r.end else ""}</span>')
                        ui.html(f'<span class="text-white/50 text-xs">{stats[0]} rows · {fmt_money(stats[1])}</span>')
                        ui.button("×", on_click=lambda e, rid=r.id: _delete_ignore(rid)).props("flat color=negative size=xs")

    footer("Categories")


def _add_category_rule(pattern, category):
    if not pattern or not category:
        ui.notify("Pattern and category required", type="warning")
        return
    try:
        rules.validate(pattern, category)
    except ValueError as e:
        ui.notify(str(e), type="negative")
        return
    con = db()
    try:
        rules.create_rule(con, pattern, category)
        con.commit()
        ui.notify(f"Rule added: “{pattern}” → {category}", type="positive")
    finally:
        con.close()
    ui.navigate.to("/categories")


def _move_rule(rule_id, delta):
    con = db()
    try:
        rule_list = rules.load_rules(con, enabled_only=False)
        ids = [r.id for r in rule_list]
        idx = ids.index(rule_id) if rule_id in ids else -1
        if idx < 0:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(ids):
            return
        ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
        rules.reorder_rules(con, ids)
        con.commit()
    finally:
        con.close()
    ui.navigate.to("/categories")


def _delete_rule(rule_id):
    con = db()
    try:
        rules.delete_rule(con, rule_id)
        con.commit()
        ui.notify("Rule deleted", type="positive")
    finally:
        con.close()
    ui.navigate.to("/categories")


def _add_ignore(kind, start, end):
    if not start:
        ui.notify("Start date required", type="warning")
        return
    try:
        k, s, e = date_ignores.validate(kind, start, end or None)
    except ValueError as err:
        ui.notify(str(err), type="negative")
        return
    con = db()
    try:
        date_ignores.create(con, k, s, e)
        con.commit()
        ui.notify("Date ignore added", type="positive")
    finally:
        con.close()
    ui.navigate.to("/categories")


def _delete_ignore(ignore_id):
    con = db()
    try:
        date_ignores.delete(con, ignore_id)
        con.commit()
        ui.notify("Ignore removed", type="positive")
    finally:
        con.close()
    ui.navigate.to("/categories")


# ── Merchants ────────────────────────────────────────────────────────────────

@ui.page("/merchants")
def merchants_page():
    _init_theme()
    chrome("/merchants")
    con = db()
    try:
        data = analytics.merchant_rows(con, limit=100)
        rows = data.get("rows", [])
        merchant_rule_list = merchants.load_rules(con)
        merchant_colors = merchants.load_colors(con)
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        with ui.row().classes("items-center justify-between w-full flex-wrap gap-2"):
            section_head("Merchants", f"{len(rows)} merchants")
            classify_btn = ui.button("Classify unknowns (LLM)", on_click=_classify_merchants).props("color=primary icon=auto_awesome")
            progress_label = ui.label("").classes("text-white/60 text-xs")

        with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
            table_rows = []
            for r in rows:
                table_rows.append({
                    "id": r["id"],
                    "merchant": r.get("merchant") or "—",
                    "set_by": r.get("set_by", "—"),
                    "date": r.get("date", ""),
                    "amount": fmt_money(r.get("amount", 0)),
                    "description": r.get("description", ""),
                })
            ui.table(
                rows=table_rows,
                columns=[{"name": "merchant", "label": "Merchant"},
                         {"name": "set_by", "label": "Source"},
                         {"name": "date", "label": "Date"},
                         {"name": "amount", "label": "Amount", "align": "right"},
                         {"name": "description", "label": "Description"}],
                row_key="id",
                pagination=25,
            ).classes("w-full")

    footer("Merchants")


async def _classify_merchants():
    if not llm.llm_configured():
        ui.notify("LLM not configured — set MERCURY_LLM_ENDPOINT and MERCURY_LLM_MODEL", type="negative")
        return
    ui.notify("Classifying merchants…", type="info")

    def work():
        con = db()
        try:
            return _classify_loop(
                con,
                lambda c: analytics.merchant_rows(c, limit=None, include_uncategorized=True, set_by="uncategorized")["rows"],
                llm.classify_merchants_batch_llm,
                lambda c, row, guess: merchants.set_merchant(c, row["id"], guess, source="llm"),
            )
        finally:
            con.close()

    import asyncio
    result = await asyncio.to_thread(work)
    ui.notify(f"Classified {result['classified']} of {result['of']}", type="positive")


# ── Transactions ──────────────────────────────────────────────────────────────

@ui.page("/transactions")
def transactions_page():
    _init_theme()
    chrome("/transactions")
    con = db()
    try:
        periods = analytics.available_periods(con)
        months = periods.get("months", [])
        summary = analytics.summary(con)
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        with ui.row().classes("items-center gap-3 flex-wrap"):
            search_in = ui.input("Search", placeholder="Keyword, e.g. 'walmart'").classes("flex-1 min-w-[200px]").props("dark")
            month_sel = ui.select(["All"] + months, value="All").classes("min-w-[160px]")
            classify_btn = ui.button("Classify unknowns", on_click=_classify_categories).props("color=primary icon=auto_awesome")
            classify_status = ui.label("").classes("text-white/60 text-xs")

        chart_slot = ui.column().classes("w-full")
        table_slot = ui.column().classes("w-full")

        def draw():
            chart_slot.clear()
            table_slot.clear()
            q = (search_in.value or "").strip()
            month = month_sel.value if month_sel.value != "All" else None
            con = db()
            try:
                rows = analytics.transactions(con, month=month, limit=200)
                if q:
                    ql = q.lower()
                    rows = [r for r in rows if ql in (r["description"] or "").lower()]
                monthly = analytics.monthly_spend(con)
            finally:
                con.close()
            with chart_slot:
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Monthly spend", f"{len(rows)} rows shown")
                    months_all = [r["month"] for r in monthly]
                    vals = [r["spend"] for r in monthly]
                    if months_all:
                        ui.echart(monthly_bar_chart(months_all, vals, "Monthly spend")).classes("w-full h-64")
            with table_slot:
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    ui.table(
                        rows=[{
                            "date": r["date"],
                            "description": r["description"] or "",
                            "category": r["category"] or "—",
                            "amount": fmt_money(r["amount"]),
                            "set_by": r["set_by"],
                        } for r in rows],
                        columns=[{"name": "date", "label": "Date"},
                                 {"name": "description", "label": "Description"},
                                 {"name": "category", "label": "Category"},
                                 {"name": "amount", "label": "Amount", "align": "right"},
                                 {"name": "set_by", "label": "Set by"}],
                        row_key="description",
                        pagination=20,
                    ).classes("w-full")

        search_in.on_value_change(lambda e: draw())
        month_sel.on_value_change(lambda e: draw())
        draw()

    footer("Transactions")


async def _classify_categories():
    if not llm.llm_configured():
        ui.notify("LLM not configured — set MERCURY_LLM_ENDPOINT and MERCURY_LLM_MODEL", type="negative")
        return
    ui.notify("Classifying categories…", type="info")

    def work():
        con = db()
        try:
            return _classify_loop(
                con,
                lambda c: analytics.unknown_categories(c, limit=None),
                llm.classify_batch_llm,
                lambda c, row, cat: analytics.set_category(c, row["id"], cat, source="llm"),
            )
        finally:
            con.close()

    import asyncio
    result = await asyncio.to_thread(work)
    ui.notify(f"Classified {result['classified']} of {result['of']}", type="positive")


# ── Year ──────────────────────────────────────────────────────────────────────

@ui.page("/year")
def year_page():
    _init_theme()
    chrome("/year")
    con = db()
    try:
        periods = analytics.available_periods(con)
        year_totals = analytics.year_totals(con)
        flow = analytics.flow_by_category(con)
        sankey_flows = scoped_sankey.sankey_flow(con)
        merchant_flows = scoped_sankey.sankey_merchants_flow(con)
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        with ui.row().classes("items-center gap-3 flex-wrap"):
            year_sel = ui.select(["All"] + periods.get("years", []), value="All").classes("min-w-[140px]")

        content = ui.column().classes("w-full")

        def draw():
            content.clear()
            with content:
                sel_year = year_sel.value if year_sel.value != "All" else None
                con = db()
                try:
                    summary = analytics.summary(con, year=sel_year)
                    monthly = analytics.monthly_spend(con, year=sel_year)
                    income = analytics.income_by_month(con, year=sel_year)
                    flow = analytics.flow_by_category(con, year=sel_year)
                    sf = scoped_sankey.sankey_flow(con, year=sel_year)
                    mf = scoped_sankey.sankey_merchants_flow(con, year=sel_year)
                finally:
                    con.close()
                with ui.grid(columns=4).classes("gap-3 w-full"):
                    kpi_card("Spend", fmt_money(summary["total_spend"]), sel_year or "all time", "#eb6600")
                    kpi_card("Income", fmt_money(summary["total_income"]), sel_year or "all time", "#1e8e52")
                    kpi_card("Net", fmt_money(summary["net"]), "income − spend", "#ff9b51")
                    kpi_card("Transactions", str(summary["n_transactions"]), "rows", "#9db2bf")
                months = [r["month"] for r in monthly]
                income_by_month = {r["month"]: r["income"] for r in income}
                spend_matrix = {}
                for f in flow:
                    spend_matrix.setdefault(f["category"], [0.0] * len(months))
                for f in flow:
                    idx = months.index(f["bucket"]) if f["bucket"] in months else None
                    if idx is not None:
                        spend_matrix[f["category"]][idx] = round(f["spend"], 2)
                cat_totals = {c: sum(v) for c, v in spend_matrix.items()}
                ordered = sorted(spend_matrix.keys(), key=lambda c: -cat_totals.get(c, 0))[:5]
                spend_matrix = {c: spend_matrix[c] for c in ordered}
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Cash flow", sel_year or "all time")
                    if months:
                        ui.echart(flow_chart(months, spend_matrix, income_by_month, None, "Spend + income")).classes("w-full h-80")
                    else:
                        ui.html('<p class="text-white/40 text-sm">No data.</p>')
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Money Flow", "accounts → categories → merchants")
                    if sf:
                        ui.echart(merchant_sankey_chart(mf, "Accounts → categories → merchants")).classes("w-full h-96")
                    else:
                        ui.html('<p class="text-white/40 text-sm">No data.</p>')

        year_sel.on_value_change(lambda e: draw())
        draw()

    footer("Year")


# ── Month ─────────────────────────────────────────────────────────────────────

@ui.page("/month")
def month_page():
    _init_theme()
    chrome("/month")
    con = db()
    try:
        periods = analytics.available_periods(con)
        months = periods.get("months", [])
    finally:
        con.close()

    with ui.column().classes("p-4 md:p-6 max-w-7xl mx-auto gap-3"):
        with ui.row().classes("items-center gap-3 flex-wrap"):
            month_sel = ui.select(months, value=months[-1] if months else "").classes("min-w-[160px]")

        content = ui.column().classes("w-full")

        def draw():
            content.clear()
            with content:
                m = month_sel.value
                if not m:
                    ui.html('<p class="text-white/40 text-sm">No months yet.</p>')
                    return
                con = db()
                try:
                    summary = analytics.summary(con, month=m)
                    weeks = analytics.weekly_breakdown(con, m)
                    flow = analytics.flow_by_category(con, month=m, weekly=True)
                    sf = scoped_sankey.sankey_flow(con, month=m)
                    mf = scoped_sankey.sankey_merchants_flow(con, month=m)
                    rows = analytics.transactions(con, month=m, limit=50)
                finally:
                    con.close()
                with ui.grid(columns=4).classes("gap-3 w-full"):
                    kpi_card("Spend", fmt_money(summary["total_spend"]), month_label(m), "#eb6600")
                    kpi_card("Income", fmt_money(summary["total_income"]), month_label(m), "#1e8e52")
                    kpi_card("Net", fmt_money(summary["net"]), "income − spend", "#ff9b51")
                    kpi_card("Transactions", str(summary["n_transactions"]), "rows", "#9db2bf")
                week_labels = [w["week"] for w in weeks]
                spend_vals = [w["spend"] for w in weeks]
                income_vals = [w["income"] for w in weeks]
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Weekly cash flow", month_label(m))
                    if week_labels:
                        opts = _chart_base("Weekly spend + income")
                        opts["xAxis"] = {"type": "category", "data": [week_label(w) for w in week_labels],
                                         "axisLabel": {"color": "#9db2bf", "fontSize": 10},
                                         "axisLine": {"lineStyle": {"color": "#3a3f4d"}}}
                        opts["yAxis"] = {"type": "value", "splitLine": {"lineStyle": {"color": "#2a2e38"}},
                                         "axisLabel": {"color": "#9db2bf", "fontSize": 10,
                                                       "formatter": "${value}"}}
                        opts["series"] = [
                            {"name": "Spend", "type": "bar", "data": spend_vals,
                             "itemStyle": {"color": "#ff9b51", "borderRadius": [4, 4, 0, 0]}, "barWidth": "50%"},
                            {"name": "Income", "type": "line", "data": income_vals, "smooth": True,
                             "symbol": "circle", "symbolSize": 5,
                             "lineStyle": {"width": 2.5, "color": "#1e8e52"},
                             "itemStyle": {"color": "#1e8e52"}},
                        ]
                        ui.echart(opts).classes("w-full h-72")
                    else:
                        ui.html('<p class="text-white/40 text-sm">No weeks with data.</p>')
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Money Flow", month_label(m))
                    if sf:
                        ui.echart(merchant_sankey_chart(mf, f"Accounts → categories → merchants — {month_label(m)}")).classes("w-full h-96")
                    else:
                        ui.html('<p class="text-white/40 text-sm">No data.</p>')
                with ui.card().classes("bg-white/5 border border-white/10 p-4 w-full"):
                    section_head("Transactions", f"{len(rows)} rows")
                    ui.table(
                        rows=[{
                            "date": r["date"],
                            "description": r["description"] or "",
                            "category": r["category"] or "—",
                            "amount": fmt_money(r["amount"]),
                        } for r in rows],
                        columns=[{"name": "date", "label": "Date"},
                                 {"name": "description", "label": "Description"},
                                 {"name": "category", "label": "Category"},
                                 {"name": "amount", "label": "Amount", "align": "right"}],
                        row_key="description",
                        pagination=15,
                    ).classes("w-full")

        month_sel.on_value_change(lambda e: draw())
        draw()

    footer("Month")


# ── classify loop (same batch semantics as the old /api/classify) ─────────────

def _classify_loop(con, get_unknowns, classify_fn, apply_fn):
    state = {"total_unknown": 0, "processed_descs": 0, "total_descs": 0,
             "classified": 0, "skipped": 0, "remaining_unknown": 0,
             "batches_done": 0, "total_batches": 0}
    initial = get_unknowns(con)
    total = len(initial)
    state["total_unknown"] = total
    state["remaining_unknown"] = total
    state["total_descs"] = len({(r["description"] or "") for r in initial})
    state["total_batches"] = (state["total_descs"] + llm.BATCH_SIZE - 1) // llm.BATCH_SIZE
    while True:
        unknown = get_unknowns(con)
        if not unknown:
            break
        descs = list(dict.fromkeys((r["description"] or "") for r in unknown))
        batch_descs = descs[:llm.BATCH_SIZE]
        batch_set = set(batch_descs)
        results = classify_fn(batch_descs)
        res_by_desc = dict(zip(batch_descs, results))
        for row in unknown:
            desc = row["description"] or ""
            if desc not in batch_set:
                continue
            res = res_by_desc.get(desc)
            if res:
                apply_fn(con, row, res)
                state["classified"] += 1
            else:
                con.execute("UPDATE transactions SET llm_skipped = true WHERE id = ?",
                            [row["id"]])
                state["skipped"] += 1
        con.commit()
        state["processed_descs"] += len(batch_descs)
        state["batches_done"] += 1
        state["remaining_unknown"] = len(get_unknowns(con))
    return {
        "classified": state["classified"],
        "skipped": state["skipped"],
        "of": total,
        "unique_descriptions": state["total_descs"],
    }


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    con = db()
    try:
        # no-op: tables are ensured on every db() call
        pass
    finally:
        con.close()
    ui.run(host=HOST, port=PORT, reload=False, title="Mercury")


if __name__ == "__main__":
    main()
