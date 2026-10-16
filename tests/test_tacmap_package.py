"""The package's own rules.

`utils/tacmap` was one 3,257-line module. It is now six files behind an
`__init__` that re-exports every name, so `from utils import tacmap` and
`tacmap.anything` are unchanged — and that re-export list is 270 lines of
hand-maintained imports, which is a silent failure waiting to happen: add a
function, forget the line, and `tacmap.newthing` simply does not exist.

These pin the three things that make the split work and that nothing else
would notice breaking.
"""

import ast
import pathlib
import subprocess
import sys

import pytest

from utils import tacmap

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'utils' / 'tacmap'
# base first, then each layer on the ones before it. A module may import from
# anything earlier in this list and nothing later.
LAYERS = ['base', 'doc', 'places', 'drawing', 'arma', 'icons']


def _defined(path):
    """The top-level names a module defines, excluding what it imported."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def test_the_re_export_list_is_generated_and_current():
    """`python scripts/gen_exports.py --check`, as a test.

    The list used to be maintained by hand, and the test below — every name a
    module defines is on `tacmap` — is what caught an omission. This is the
    other half: the list is written from the modules, so there is nothing to
    remember, and falling behind is a red test rather than an AttributeError
    somewhere later. Same bargain `gen_help.py` makes with the README.
    """
    import subprocess
    import sys
    out = subprocess.run([sys.executable, 'scripts/gen_exports.py', '--check'],
                         capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_name_a_submodule_defines_is_re_exported():
    """The one thing the split can quietly break.

    A name that is not in `__init__` is not on `tacmap`, and the only sign is
    an AttributeError from whichever caller wanted it — which, for a module
    this one is imported by `web/`, `cogs/` and `utils/campaign.py`, may be a
    page nobody opens until the night it matters.
    """
    missing = []
    for name in LAYERS:
        for defined in sorted(_defined(PKG / ('%s.py' % name))):
            if not hasattr(tacmap, defined):
                missing.append('%s.%s' % (name, defined))
    assert not missing, 'defined but not re-exported: %s' % missing


def test_the_layers_only_ever_import_downwards():
    """What keeps six files a layer cake rather than a knot.

    The split looked circular at first and was not: four per-item predicates
    were sitting among the drawing code and four place constants among the
    place names, so `base` appeared to depend on `drawing` and `doc` on
    `places`. They moved to `base`, where everything may reach them.

    **Python enforces this one itself, as it happens.** Every upward import I
    could construct — at module level, and lazily inside a function — fails at
    import time with a circular-import error, because `base`'s own tables are
    built by calling its own functions and every other module reads `base` at
    module level. So this test is the rule written down rather than a net under
    it: it names the order, and it would catch an upward import in a module
    that something day changes to not be loaded at import time. I could not
    make it fail where Python did not fail first, which is worth knowing before
    trusting it as a safety net.
    """
    wrong = []
    for i, name in enumerate(LAYERS):
        allowed = set(LAYERS[:i])
        tree = ast.parse((PKG / ('%s.py' % name)).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                if node.module not in allowed:
                    wrong.append('%s imports from %s' % (name, node.module))
    assert not wrong, 'imports that go the wrong way up the layers: %s' % wrong


def test_the_package_still_needs_nothing_but_the_standard_library():
    """The reason any of this is tested at all.

    `utils/arma_icons.py` is the one exception and is itself stdlib-only — it
    holds names and paths, never artwork.
    """
    out = subprocess.run(
        [sys.executable, '-c',
         'import utils.tacmap, sys;'
         'print([m for m in sys.modules if m.split(".")[0] in'
         ' ("discord", "asyncpg", "fastapi", "aiohttp", "gspread", "jinja2", "reportlab")])'],
        capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '[]', out.stdout


def test_the_package_is_what_the_layout_says_it_is():
    """CLAUDE.md's layout names these six files; a seventh nobody listed is a
    file nobody reads."""
    present = sorted(p.stem for p in PKG.glob('*.py') if p.stem != '__init__')
    assert present == sorted(LAYERS), present
    layout = (ROOT / 'CLAUDE.md').read_text()
    for name in LAYERS:
        assert '%s.py' % name in layout, '%s.py is not in the layout' % name
