"""Static contract test: every /api/… path the frontend calls must be a
registered FastAPI route. Catches pages calling endpoints that were never
implemented (e.g. the Year page's /api/years and /api/all-categories, which
404'd in #109 and left the page unrendered)."""
import re
from pathlib import Path

import app.main as main

STATIC_DIR = Path(main.STATIC_DIR)


def _called_prefixes():
    """Every literal prefix of an api('…') call across the static frontend."""
    prefixes = set()
    for f in sorted(STATIC_DIR.rglob("*.html")) + sorted(STATIC_DIR.rglob("*.js")):
        src = f.read_text()
        for m in re.finditer(r"api\(\s*'([^']+)'", src):
            p = m.group(1).split('?')[0].split('+')[0].strip()
            if p.startswith('/api/'):
                prefixes.add(p)
    return prefixes


def _route_paths():
    return {r.path for r in main.app.routes
            if getattr(r, 'path', None) and r.path.startswith('/api/')}


def test_frontend_only_calls_registered_api_paths():
    routes = _route_paths()
    missing = {}
    for p in sorted(_called_prefixes()):
        if p in routes:
            continue
        # Dynamic route (e.g. /api/imports/{import_id}) — the literal prefix
        # is valid if some registered path starts with it.
        if any(r.startswith(p) for r in routes):
            continue
        missing[p] = True
    assert not missing, (
        "frontend calls /api paths with no registered route: "
        + ", ".join(sorted(missing))
    )
