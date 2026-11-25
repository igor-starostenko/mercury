"""Mercury — NiceGUI evaluation prototype.

Experimental port of the Overview page to NiceGUI (ECharts + Quasar-style
components). It reuses the existing data layer (app.ingest / app.analytics /
app.imports) unchanged — no database schema, API or frontend static assets
are modified by this file.

Status: evaluation prototype, NOT a merge candidate for the production
frontend. The static UI (app/main.py + app/static/*) remains the default.

Run:
    uv run python -m app.nicegui_app
(serves on MERCURY_PORT or 8730)
"""
from __future__ import annotations

import os

import duckdb
from nicegui import ui

from app import analytics, ingest, imports, merchants, rules

from app.date_ignores import CREATE_DATE_IGNORES_SQL
from app.merchants import (
    CREATE_MERCHANT_RULES_SQL,
    CREATE_MERCHANT_OVERRIDES_SQL,
    CREATE_MERCHANT_COLORS_SQL,
)

DB_PATH = ingest.DB_PATH

# Accent palette for the stacked chart. Fixed (not derived) so the
# prototype is deterministic and self-contained.
PALETTE = [
    "#4c8dff", "#36c98f", "#f2a33c", "#e35d6a", "#9d6ef2",
    "#3fb7c4", "#c9a24c", "#e07bb0", "#7bd88f", "#b8b2d9",
]


def fmt_money(n: float) -> str:
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def init_tables() -> None:
    """Same bootstrap as the FastAPI lifespan: tables and the effective
    view must exist before any query runs."""
    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute(ingest.CREATE_TABLE_SQL)
        con.execute(ingest.CREATE_OVERRIDES_SQL)
        ingest.ensure_override_source(con)
        con.execute(ingest.CREATE_DELETED_SQL)
        con.execute(rules.CREATE_RULES_SQL)
        rules.ensure_rule_position(con)
        rules.ensure_rule_scope(con)
        con.execute(CREATE_DATE_IGNORES_SQL)
        con.execute(imports.CREATE_IMPORTS_SQL)
        imports.ensure_import_column(con)
        imports.ensure_color_column(con)
        ingest.ensure_llm_skipped_column(con)
        con.execute(CREATE_MERCHANT_RULES_SQL)
        merchants.ensure_rule_position(con)
        merchants.ensure_rule_scope(con)
        con.execute(CREATE_MERCHANT_OVERRIDES_SQL)
        con.execute(CREATE_MERCHANT_COLORS_SQL)
        con.execute(ingest.CREATE_EFFECTIVE_VIEW_SQL)
    finally:
        con.close()


def load_overview():
    con = duckdb.connect(str(DB_PATH))
    try:
        summary = analytics.summary(con)
        monthly = analytics.monthly_spend(con)
        flow = analytics.flow_by_category(con)
        year_totals = analytics.year_totals(con)
        accounts = analytics.accounts(con)
        sankey_rows = analytics.sankey_flow(con)
        return summary, monthly, flow, year_totals, accounts, sankey_rows
    finally:
        con.close()


@ui.page("/")
def overview():
    init_tables()
    summary, monthly, flow, year_totals, accounts, sankey_rows = load_overview()

    # -- Top bar -------------------------------------------------------------
    with ui.header().classes("bg-gray-900 text-white items-center px-6 h-14"):
        ui.html('<a href="/" class="text-xl font-bold tracking-tight">Mercury</a>')
        ui.badge("NiceGUI prototype", color="amber")
        ui.space()
        ui.button("Refresh", on_click=lambda: ui.navigate.reload()).props("flat color=white")

    # -- Empty state -----------------------------------------------------------
    if summary["n_transactions"] == 0:
        with ui.column().classes("w-full max-w-4xl mx-auto p-8"):
            with ui.card().classes("w-full p-8 text-center"):
                ui.html('<h2 class="text-xl font-semibold">No transactions yet</h2>')
                ui.html('<p class="text-gray-400 mt-2">Upload bank CSVs in the production UI — the NiceGUI prototype reads the same database.</p>')
        return

    with ui.column().classes("w-full max-w-7xl mx-auto p-4"):
        # -- Summary cards ------------------------------------------------------
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for label, value, color in (
                ("Spend", fmt_money(summary["total_spend"]), "red"),
                ("Income", fmt_money(summary["total_income"]), "green"),
                ("Net", fmt_money(summary["net"]), "amber" if summary["net"] < 0 else "green"),
                ("Transactions", f'{summary["n_transactions"]:,}', "blue"),
            ):
                with ui.card().classes("flex-1 min-w-36 p-4"):
                    ui.html(f'<div class="text-xs uppercase tracking-wider text-gray-400">{label}</div>')
                    ui.html(f'<div class="text-2xl font-semibold text-{color}-400 mt-1">{value}</div>')

        # -- Cash flow by month (stacked by category) ---------------------------
        ui.html('<h2 class="text-lg font-semibold mt-6 mb-2">Cash flow by month</h2>')
        ui.html('<p class="text-sm text-gray-400 -mt-1 mb-2">Stacked by top categories · all time</p>')
        months = [m["month"] for m in monthly]
        month_set = set(months)
        # Aggregate spend by (month, category); keep top-5 categories overall.
        by_cat_month: dict[str, dict[str, float]] = {}
        totals: dict[str, float] = {}
        for row in flow:
            if row["bucket"] not in month_set:
                continue
            by_cat_month.setdefault(row["bucket"], {})[row["category"]] = row["spend"]
            totals[row["category"]] = totals.get(row["category"], 0) + row["spend"]
        top_cats = [c for c, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:5]]
        series = []
        for i, cat in enumerate(top_cats):
            series.append({
                "name": cat,
                "type": "bar",
                "stack": "spend",
                "itemStyle": {"color": PALETTE[i % len(PALETTE)]},
                "data": [by_cat_month.get(m, {}).get(cat, 0) for m in months],
                "barMaxWidth": 48,
            })
        # 12-month trailing average line (totals mode, simplified)
        spend_by_month = {m["month"]: m["spend"] for m in monthly}
        avg_line = []
        for i, m in enumerate(months):
            window = months[max(0, i - 11): i + 1]
            window_sum = sum(spend_by_month.get(w, 0) for w in window)
            avg_line.append(round(window_sum / len(window), 2))
        series.append({
            "name": "12-mo avg",
            "type": "line",
            "smooth": True,
            "symbol": "none",
            "lineStyle": {"color": "#e5e7eb", "width": 2, "type": "dashed"},
            "data": avg_line,
        })
        chart_options = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 56, "right": 24, "top": 40, "bottom": 32},
            "xAxis": {"type": "category", "data": months, "axisLabel": {"color": "#9ca3af", "rotate": 30, "interval": 0}},
            "yAxis": {"type": "value", "axisLabel": {"color": "#9ca3af", "formatter": "${value}"}},
            "series": series,
        }
        with ui.card().classes("w-full p-2"):
            ui.echart(chart_options).style("width:100%;height:380px")

        # -- Year cards ----------------------------------------------------------
        ui.html('<h2 class="text-lg font-semibold mt-6 mb-2">By year</h2>')
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for y in year_totals:
                with ui.card().classes("flex-1 min-w-44 p-4"):
                    ui.html(f'<div class="text-2xl font-bold">{y["year"]}</div>')
                    ui.html(f'<div class="text-sm text-gray-400 mt-1">Spend {fmt_money(y["spend"])}</div>')
                    ui.html(f'<div class="text-sm text-gray-400">Income {fmt_money(y["income"])}</div>')
                    ui.html(f'<div class="text-sm mt-1 font-medium {"text-green-400" if y["net"] >= 0 else "text-red-400"}">Net {fmt_money(y["net"])} · {y["n"]:,} txns</div>')

        # -- Accounts ------------------------------------------------------------
        ui.html('<h2 class="text-lg font-semibold mt-6 mb-2">Accounts</h2>')
        with ui.card().classes("w-full p-4"):
            for a in accounts:
                with ui.row().classes("w-full items-center justify-between py-1 border-b border-gray-800 last:border-0"):
                    with ui.row().classes("items-center gap-2"):
                        ui.html(f'<span class="inline-block w-3 h-3 rounded-full" style="background:{a["color"] or "#6b7280"}"></span>')
                        ui.html(f'<span class="font-medium">{a["account"]}</span>')
                        ui.html(f'<span class="text-xs text-gray-500">{a["source"]}</span>')
                    ui.html(f'<span class="font-mono">{fmt_money(a["net"])}</span>')

        # -- Money flow sankey ---------------------------------------------------
        ui.html('<h2 class="text-lg font-semibold mt-6 mb-2">Money flow</h2>')
        ui.html('<p class="text-sm text-gray-400 -mt-1 mb-2">Account → category (spend) · simplified 2-column sankey</p>')
        nodes = {}
        links = []
        for r in sankey_rows:
            if r["debit"] <= 0:
                continue
            if r["account"] not in nodes:
                nodes[r["account"]] = {"name": r["account"], "depth": 0}
            if r["category"] not in nodes:
                nodes[r["category"]] = {"name": r["category"], "depth": 1}
            links.append({"source": r["account"], "target": r["category"], "value": r["debit"]})
        # collapse duplicate links
        agg: dict[tuple, float] = {}
        for l in links:
            agg[(l["source"], l["target"])] = agg.get((l["source"], l["target"]), 0) + l["value"]
        sankey_links = [{"source": s, "target": t, "value": round(v, 2)} for (s, t), v in agg.items()]
        sankey_options = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "series": [{
                "type": "sankey",
                "layout": "none",
                "nodeWidth": 14,
                "nodeGap": 12,
                "draggable": False,
                "emphasis": {"focus": "adjacency"},
                "data": list(nodes.values()),
                "links": sankey_links,
                "lineStyle": {"color": "gradient", "opacity": 0.35},
                "label": {"color": "#e5e7eb"},
                "nodeStyle": {"borderWidth": 0},
            }],
        }
        with ui.card().classes("w-full p-2"):
            ui.echart(sankey_options).style("width:100%;height:460px")

        ui.html('<p class="text-xs text-gray-500 mt-6 text-center">NiceGUI evaluation prototype — experimental, not a replacement for the static UI. Same database, same data layer, simplified sankey (spend only, 2 columns).</p>')


def run(port: int | None = None):
    port = port or int(os.environ.get("MERCURY_PORT", "8730"))
    ui.run(title="Mercury (NiceGUI prototype)", port=port, reload=False)


if __name__ == "__main__":
    run()
