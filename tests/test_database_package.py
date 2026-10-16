"""The query package's own rules.

`utils/database` was one module of 3,333 lines and 216 functions — a phone
book, where finding the one that writes `cp_drops` meant scrolling. It is
thirteen modules now, one per feature, grouped by the tables each query
touches, behind an `__init__` that re-exports every name.

The split was safe to make because the coupling was almost nothing: of 233
calls between functions in that file, all but 18 were to `get_pool()`, and
**not one of the 18 crossed a feature group**. These pin that it stays so,
and the third one pins the failure mode a split like this actually has.
"""

import ast
import builtins
import pathlib

from utils import database

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'utils' / 'database'


def _modules():
    files = sorted(p for p in PKG.glob('*.py') if p.stem != '__init__')
    assert len(files) > 5, 'utils/database lost its modules'
    return files


def test_the_re_export_list_is_generated_and_current():
    """`python scripts/gen_exports.py --check`, as a test.

    The list used to be maintained by hand, and the test below — every name a
    module defines is on `database` — is what caught an omission. This is the
    other half: the list is written from the modules, so there is nothing to
    remember, and falling behind is a red test rather than an AttributeError
    somewhere later. Same bargain `gen_help.py` makes with the README.
    """
    import subprocess
    import sys
    out = subprocess.run([sys.executable, 'scripts/gen_exports.py', '--check'],
                         capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_name_a_module_defines_is_re_exported():
    """`__init__` is a hand-maintained list, and a name missing from it is an
    AttributeError in whichever cog or route wanted that query."""
    missing = []
    for path in _modules():
        tree = ast.parse(path.read_text())
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for name in names:
                if not hasattr(database, name):
                    missing.append('%s.%s' % (path.stem, name))
    assert not missing, 'defined but not re-exported: %s' % missing


# The one dependency between two feature modules, and it is a real one rather
# than a leftover: the campaign planner finds or creates a Kill Tracker
# campaign for each real campaign it starts, so `_link_kt_campaign` writes
# `kt_campaigns` — which is why it lives in the Kill Tracker module, grouped by
# the table it touches — and only the planner ever calls it. Moving it would
# make "the module is named after the tables it writes" untrue; allowing every
# sideways import would make the grouping mean nothing. So: one, named.
ALLOWED_SIDEWAYS = {('campaigns', 'killtracker')}


def test_only_one_feature_module_imports_from_another():
    """Otherwise `core` is the only thing shared.

    A query about events has no business knowing about campaigns, and the
    moment modules import each other freely the grouping stops meaning
    anything. Everything else a module needs comes from `core` — the pool,
    and `_naive`.
    """
    wrong = []
    for path in _modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module in (None, 'core') or path.stem == 'core':
                    continue
                if (path.stem, node.module) not in ALLOWED_SIDEWAYS:
                    wrong.append('%s imports from %s' % (path.stem, node.module))
    assert not wrong, 'new sideways imports: %s' % wrong


def _bound_in(node):
    """Every name this function binds: args, assignments, its own imports,
    comprehension targets, `except x`, nested defs."""
    out = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs) + [a.vararg, a.kwarg]:
            if arg:
                out.add(arg.arg)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            out.add(sub.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            out.update(a.asname or a.name.split('.')[0] for a in sub.names)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            out.add(sub.name)
        elif isinstance(sub, (ast.Global, ast.Nonlocal)):
            out.update(sub.names)
    return out


def test_every_global_a_query_references_resolves_in_its_own_module():
    """The failure mode a split like this really has.

    Moving a function to a new file leaves its imports behind. A module using
    `json` without importing it is a `NameError` raised the first time that
    query runs — in production, because almost nothing here is reachable by a
    test without a database.

    **The scope matters, and getting it wrong makes this test useless.** The
    first version of it pooled every import in the file into one set, so the
    two function-local `import json as _json` statements in the Kill Tracker
    module masked the module-level one: deleting that import left five other
    functions broken and the test still passed. Each function is therefore
    checked against its own bound names plus the module's, and nothing else.
    """
    bad = []
    for path in sorted(PKG.glob('*.py')):
        tree = ast.parse(path.read_text())
        module = set(dir(builtins))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module.add(node.name)
            elif isinstance(node, ast.Assign):
                module.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                module.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module.update(a.asname or a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        module.update(a.asname or a.name.split('.')[0] for a in sub.names)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                allowed = module | _bound_in(node)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        if sub.id not in allowed:
                            bad.append('%s:%d %s (in %s)'
                                       % (path.name, sub.lineno, sub.id, node.name))
        # module level, outside any function
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id not in module:
                        bad.append('%s:%d %s (module level)' % (path.name, sub.lineno, sub.id))
    assert not bad, 'names that would raise NameError when the query runs: %s' % bad[:10]


def test_the_pool_is_opened_in_one_place():
    """`get_pool()` is the only thing that creates a connection pool, and every
    query goes through it. Thirteen modules each making their own would be
    thirteen pools against the same database."""
    creators = []
    for path in sorted(PKG.glob('*.py')):
        if 'create_pool' in path.read_text() and path.stem != 'core':
            creators.append(path.name)
    assert not creators, 'these open their own pool: %s' % creators
