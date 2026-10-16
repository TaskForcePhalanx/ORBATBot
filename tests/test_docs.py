"""The documentation's own rules.

`CLAUDE.md` was one 4,100-line file. It held every rule in this project and was
read in full by every session before a line of code, and it was the single file
every branch touched — so two open PRs conflicted on it almost every time, in
the one file where resolving a conflict means re-reading four thousand lines to
be sure nothing else shifted.

It is now split **by kind rather than by feature**: the rules every session
needs whatever it is working on stayed, and each feature's own rules moved to
`docs/<feature>.md`, verbatim. What makes that safe rather than merely tidier
is that the split is checkable, which is what these tests are:

- a link between the files resolves to a heading that exists,
- nothing under `docs/` is orphaned — every file is reachable from CLAUDE.md,
- the routing table names paths that exist,
- and CLAUDE.md does not quietly grow back into what it was.

The last one is the point. Nothing stops a future session appending one more
section here, and nothing would notice until the file was four thousand lines
again.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAUDE = ROOT / 'CLAUDE.md'
DOCS = ROOT / 'docs'

# What CLAUDE.md is allowed to cost every session. It sat at 4,124 lines before
# the split and at ~430 after; this is room to grow by half again before
# somebody has to think about it, rather than a target.
MAX_CLAUDE_LINES = 700


def _files():
    return [CLAUDE] + sorted(DOCS.glob('*.md'))


def _anchor(heading):
    """GitHub's slug: lowercase, drop punctuation, each space becomes a hyphen.

    Each space, not each run of them: `a + b` loses the `+` and keeps both
    spaces, so it slugs to `a--b`. Getting that wrong is what hid a broken
    link in CLAUDE.md for months — see test_every_link_between_the_files_lands.
    """
    s = heading.lower()
    s = re.sub(r'[^\w\s-]', '', s)
    return s.strip().replace(' ', '-')


def _headings(path):
    return {_anchor(line.lstrip('#').strip())
            for line in path.read_text().split('\n') if line.startswith('#')}


_LINK = re.compile(r'\]\(([^)#\s]*)#([a-z0-9-]+)\)')


def test_every_link_between_the_files_lands():
    """A link that does not resolve is worse than no link: it reads as a
    promise that the detail is written down somewhere.

    This is not hypothetical. The Kill Tracker was linked as
    `#…killtrackerpy--utilsocappy--…` — two hyphens where its heading has a
    comma and one space — from the day the link was written, in two places.
    Nothing was checking.
    """
    broken = []
    for path in _files():
        for target, anchor in _LINK.findall(path.read_text()):
            if target == '':
                dest = path
            elif target == '../CLAUDE.md':
                dest = CLAUDE
            elif target.startswith('docs/'):
                dest = ROOT / target
            else:
                dest = path.parent / target
            if not dest.exists():
                broken.append('%s -> %s (no such file)' % (path.name, target))
            elif anchor not in _headings(dest):
                broken.append('%s -> %s#%s' % (path.name, target or path.name, anchor))
    assert not broken, 'links that do not resolve:\n  ' + '\n  '.join(broken)


def test_every_doc_is_reachable_from_claude_md():
    """An unlinked file under docs/ is a file nobody opens.

    CLAUDE.md is the only thing loaded automatically, so it is the only route
    in. A feature's rules that nothing points at may as well not be written.
    """
    linked = set(re.findall(r'\(docs/([a-z-]+\.md)[#)]', CLAUDE.read_text()))
    present = {p.name for p in DOCS.glob('*.md')}
    assert present - linked == set(), 'not linked from CLAUDE.md: %s' % sorted(present - linked)
    assert linked - present == set(), 'linked but missing: %s' % sorted(linked - present)


def test_the_routing_table_names_paths_that_exist():
    """The table tells a session which file to read before touching some code,
    so it is keyed on paths. A renamed module silently stops routing anybody.
    """
    text = CLAUDE.read_text()
    start = text.index('| Changing… | Read |')
    end = text.index('\n\n', start)
    rows = [r for r in text[start:end].split('\n') if r.startswith('| `')]
    assert len(rows) >= 10, 'the routing table lost its rows'

    missing = []
    for row in rows:
        for path in re.findall(r'`([a-z_]+/[a-z_./]+|[a-z_]+\.py)`', row.split('|')[1]):
            if not (ROOT / path.rstrip('/')).exists():
                missing.append(path)
    assert not missing, 'the routing table names paths that do not exist: %s' % missing


def test_every_doc_says_where_it_came_from():
    """Opened on its own — in an editor, on GitHub — a docs file has to say
    that CLAUDE.md is what decides when it is read. Otherwise it reads as the
    authority and the routing table it belongs to is never seen.
    """
    for path in DOCS.glob('*.md'):
        head = path.read_text()[:400]
        assert 'CLAUDE.md' in head, '%s does not point back at CLAUDE.md' % path.name


def test_claude_md_has_not_grown_back():
    """The file is loaded in full by every session, so its length is a cost
    paid on every turn before any work starts. It was 4,124 lines once.

    If this fails, the answer is a new or existing file under `docs/` and a row
    in the routing table — not a bigger number here.
    """
    lines = CLAUDE.read_text().count('\n')
    assert lines <= MAX_CLAUDE_LINES, (
        'CLAUDE.md is %d lines, over the %d it is meant to stay under. '
        'Move the new material into docs/ and link it.' % (lines, MAX_CLAUDE_LINES))


def test_every_table_still_has_its_shape():
    """A table row with the wrong number of cells renders as nonsense.

    Worth checking because it is invisible in a diff and because the split
    inherited one: the `cp_missions` row carries `fields::jsonb || patch`, and
    a pipe inside backticks still ends a cell in GitHub's markdown, so that
    row had been rendering as four columns in a two-column table since the day
    it was written. Escaped now (`\\|`), which is why this counts unescaped
    pipes only.
    """
    bad = []
    for path in _files():
        lines = path.read_text().split('\n')
        i = 0
        while i < len(lines):
            if not lines[i].startswith('|'):
                i += 1
                continue
            block = []
            while i < len(lines) and lines[i].startswith('|'):
                block.append((i + 1, lines[i]))
                i += 1
            cells = lambda row: row.replace('\\|', '').count('|')
            if len(block) < 2 or set(block[1][1]) - set('|-: '):
                bad.append('%s:%d a table with no separator row' % (path.name, block[0][0]))
                continue
            width = cells(block[0][1])
            for n, row in block:
                if cells(row) != width:
                    bad.append('%s:%d has %d cells, its header has %d'
                               % (path.name, n, cells(row) - 1, width - 1))
    assert not bad, 'tables that will not render:\n  ' + '\n  '.join(bad)


def test_no_feature_section_stayed_behind():
    """What stayed is deliberate: the project, the layout, the testing rule,
    the variables, the channels, who may do what, how it deploys.

    A feature's own rules go to its own file. This pins the list so that
    adding a `## The widget (utils/widget.py)` section here is a red test
    rather than the first step back to one enormous file.
    """
    kept = [line[3:].strip() for line in CLAUDE.read_text().split('\n')
            if line.startswith('## ')]
    assert kept == [
        'What This Is',
        'Where the rest of this is',
        'Repository Structure',
        'Environment Variables',
        'Discord Channels (auto-created)',
        'Unit Roles & Access Control',
        'Deployment',
    ], kept
