#!/usr/bin/env python3
"""Write the re-export lists in the two packages' `__init__.py`.

    python scripts/gen_exports.py          # rewrite them in place
    python scripts/gen_exports.py --check  # fail if either is out of date

`utils/tacmap` and `utils/database` were each one module, and each is now a
package whose `__init__` re-exports every name so that `tacmap.anything` and
`database.anything` keep working. That is 500-odd lines of import list between
them, and it was hand-maintained: add a function, forget the line, and the name
simply is not there — an AttributeError in whichever caller wanted it, which
for a query may be a page nobody opens until the night it matters.

A test caught the omission, which was better than nothing and still left the
list a thing to remember. This generates it instead, the same bargain
`gen_help.py` makes with the README: the names come from the modules, and
`--check` is what says the file has fallen behind.

**`import *` is not the alternative it looks like.** It skips every
underscore-prefixed name, and four of those are used outside their package —
`tacmap._INK_DARK`, `._INK_LIGHT`, `._arma_type`, `._colour`, all in
`tests/test_tacmap.py` — so each module would need an `__all__` listing them.
That is the same hand-maintained list, fragmented across six files instead of
gathered in one.
"""

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = [ROOT / 'utils' / 'tacmap', ROOT / 'utils' / 'database']

START = '# exports:start — written by scripts/gen_exports.py; do not edit by hand'
END = '# exports:end'


def defined_in(path: Path) -> list:
    """The top-level names a module defines, in the order a reader meets them.

    What it *imports* is deliberately not included: re-exporting a neighbour's
    name through two modules would make the surface depend on import order.
    """
    names = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Try):
            # `try: from .. import arma_icons as _icons` — the fallback import
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    names += [a.asname or a.name.split('.')[0] for a in sub.names]
    return names


def modules_of(pkg: Path) -> list:
    """`core`/`base` first when it exists, then the rest alphabetically: the
    import order has to follow the dependencies, and both packages have one
    module everything else is built on."""
    mods = sorted(p for p in pkg.glob('*.py') if p.stem != '__init__')
    first = [p for p in mods if p.stem in ('core', 'base')]
    return first + [p for p in mods if p not in first]


def block_for(pkg: Path) -> str:
    out = [START]
    for path in modules_of(pkg):
        names = defined_in(path)
        if not names:
            continue
        out.append('from .%s import (  # noqa: F401' % path.stem)
        out += ['    %s,' % n for n in names]
        out.append(')')
    out.append(END)
    return '\n'.join(out)


def rewrite(pkg: Path, check: bool) -> bool:
    init = pkg / '__init__.py'
    text = init.read_text()
    block = block_for(pkg)
    if START in text and END in text:
        head = text[:text.index(START)]
        tail = text[text.index(END) + len(END):]
        wanted = head + block + tail
    else:
        # First run: keep the docstring and any comments above the first import.
        tree = ast.parse(text)
        cut = next((n.lineno - 1 for n in tree.body
                    if isinstance(n, (ast.Import, ast.ImportFrom))), len(text.split('\n')))
        lines = text.split('\n')[:cut]
        while lines and not lines[-1].strip():
            lines.pop()
        wanted = '\n'.join(lines) + '\n' + block + '\n'
    if wanted == text:
        return True
    if not check:
        init.write_text(wanted)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='exit 1 if either list is out of date')
    args = parser.parse_args()

    stale = [pkg for pkg in PACKAGES if not rewrite(pkg, args.check)]
    if args.check:
        for pkg in stale:
            print('❌ %s/__init__.py is out of date — run python scripts/gen_exports.py'
                  % pkg.relative_to(ROOT))
        if not stale:
            print('✅ both re-export lists are up to date')
        return 1 if stale else 0
    for pkg in stale:
        print('wrote %s/__init__.py' % pkg.relative_to(ROOT))
    if not stale:
        print('both re-export lists were already up to date')
    return 0


if __name__ == '__main__':
    sys.exit(main())
