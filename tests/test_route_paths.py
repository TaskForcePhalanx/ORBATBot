"""No two routes may claim the same path.

FastAPI serves whichever was registered first and says nothing about the other,
so a second feature taking `/m/{x}` — the modlist selector once did, and the
tactical map's share links already owned it — just never answers, and the only
symptom is a page that says "No such map" on the other feature's URL.
"""

import pathlib
import re
from collections import defaultdict

ROUTES = pathlib.Path(__file__).resolve().parent.parent / 'web'
DECORATOR = re.compile(r"@app\.(get|post)\(\s*f?['\"]([^'\"]+)['\"]")


def test_no_two_routes_share_a_method_and_path():
    seen = defaultdict(list)
    for path in sorted(ROUTES.rglob('*.py')):
        for method, route in DECORATOR.findall(path.read_text()):
            shape = re.sub(r'\{[^}:]+(:[^}]+)?\}', '{}', route)
            seen[(method, shape)].append(f'{path.name}: {route}')
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not clashes, f'routes that share a path: {clashes}'
