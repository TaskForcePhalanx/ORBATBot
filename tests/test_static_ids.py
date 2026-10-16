"""The two scripts only ever reach for an element some template renders.

The site has exactly two scripts (`web/static/tacmap.js` and
`web/static/campaign.js`, each argued where the exception is made), and neither
is reachable from pytest: they are one DOM-bound IIFE apiece, so there is no
seam to call. That is the trade the no-JavaScript rule accepts — but it leaves
one failure mode completely uncovered, and it is not a subtle one:

    document.getElementById('tmredo').addEventListener('click', redo);

with no `id="tmredo"` in the template throws a TypeError on load, and because
both scripts are a single IIFE, the throw takes *the whole editor* with it —
every tool, every shortcut, the save button. A reviewer reading the diff sees a
handler wired to a button and has no way to notice the button was never added.
Nothing else here would catch it: the Python suite never loads a page, and the
templates are only parsed by Jinja at request time.

So this reads the source the way `tests/test_logging.py` and
`tests/test_help.py` do, and pins the one thing that is checkable without a
browser: every id either script asks for is an id some template actually
renders.

An id a template builds out of a Jinja expression — `id="cpf-{{ f.key }}"`, the
campaign form's per-field boxes — is matched as a pattern rather than a string,
since its real values are only known at render time. That is a deliberate hole
in the check, and a narrow one: it admits any `cpf-…`, and nothing else.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ['web/static/tacmap.js', 'web/static/campaign.js']

# id="…" / id='…'. The attribute is written plainly everywhere in these
# templates; nothing builds one by string concatenation in Python.
_ID_ATTR = re.compile(r"""id=["']([^"']+)["']""")
# Only a lookup whose argument is one complete literal string. A computed one —
# `getElementById('tmt-' + hex)`, the SVG filter the script appends and then
# finds again — names an element the script creates rather than one a template
# renders, so there is nothing here to check it against. Requiring the closing
# quote and bracket is what leaves those out; matching the literal prefix of a
# concatenation instead would report `tmt-` as missing forever.
_GET_BY_ID = re.compile(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")
_JINJA = re.compile(r'\{\{.*?\}\}|\{%.*?%\}')


def _rendered_ids():
    """Every id the templates render: the literal ones, and a pattern each for
    the ones a Jinja expression fills in."""
    literal = set()
    patterns = []
    for path in sorted((ROOT / 'web' / 'templates').rglob('*.html')):
        for value in _ID_ATTR.findall(path.read_text(encoding='utf-8')):
            if '{{' in value or '{%' in value:
                # 'cpf-{{ f.key }}' -> ^cpf\-.+$ : the fixed text has to match,
                # whatever the expression turns out to be.
                parts = _JINJA.split(value)
                # An id that is *nothing but* an expression — help.html's
                # `id="{{ shelf.group.key }}"` — would compile to ^.+$ and
                # quietly make this whole check pass for any id at all. It
                # vouches for nothing, so it is dropped rather than used. If a
                # script ever does reach for one, this test fails and says so,
                # which is the right moment to think about it.
                if not any(part for part in parts):
                    continue
                patterns.append(
                    re.compile('^' + '.+'.join(re.escape(part) for part in parts) + '$'))
            else:
                literal.add(value)
    return literal, patterns


def _ids_used(source: str):
    return sorted(set(_GET_BY_ID.findall(source)))


def test_every_id_the_scripts_reach_for_is_one_a_template_renders():
    literal, patterns = _rendered_ids()
    assert literal, 'found no id attributes at all — the templates moved'

    missing = {}
    for script in SCRIPTS:
        source = (ROOT / script).read_text(encoding='utf-8')
        used = _ids_used(source)
        assert used, f'{script}: no getElementById calls found — did it move?'
        absent = [name for name in used
                  if name not in literal
                  and not any(rule.match(name) for rule in patterns)]
        if absent:
            missing[script] = absent

    assert not missing, (
        'these ids are asked for in JavaScript but no template renders them, '
        'so the script throws on load and takes the whole editor with it: '
        + '; '.join(f'{script}: {", ".join(names)}'
                    for script, names in sorted(missing.items())))


def test_the_check_would_notice_a_handler_wired_to_nothing():
    """The proof the test above is not vacuous.

    It is the mutation that prompted this file: a Redo button's handler added
    to the script while the template still had no Redo button.
    """
    literal, patterns = _rendered_ids()
    invented = 'tmbuttonthatwasneveradded'
    assert invented not in literal
    assert not any(rule.match(invented) for rule in patterns), (
        'a Jinja id pattern is loose enough to match anything, which would '
        'make the real check pass no matter what')


def test_the_jinja_hole_admits_its_own_prefix_and_no_more():
    """The relaxation above is the only hole, so it is worth pinning shut.

    `cpf-{{ f.key }}` must admit `cpf-results` — the AAR's objectives table,
    which is why the hole exists — and must not admit an id that merely looks
    similar.
    """
    _, patterns = _rendered_ids()
    matches = lambda name: any(rule.match(name) for rule in patterns)
    assert matches('cpf-results')
    assert not matches('cpf-')          # the expression renders something
    assert not matches('cpfresults')    # the fixed text includes the dash
    assert not matches('xcpf-results')  # and it is anchored at the start


# ---------------------------------------------------------------------------
# The other thing about these scripts that is checkable without a browser
# ---------------------------------------------------------------------------

# Everything a person needs to move around the map rather than draw on it. Each
# is matched by a piece of its source that occurs exactly once.
VIEW_CONTROLS = (
    ('the zoom buttons', "[['tmzoomin'"),
    ('the zoom helper they call', 'function zoomBy('),
    ('pinch to zoom and two-finger pan', 'var touches = {}'),
)

_READER_GATE = 'if (!editable) {'


def test_the_viewport_controls_are_above_the_read_only_gate():
    """A reader must be able to move around the map, not only look at it whole.

    `tacmap.js` returns early for anyone without an edit link, and everything
    below that line is the editor. The zoom buttons, the pinch and the
    drag-to-pan were all below it while the buttons themselves render for
    everyone — so a shared plan opened on a phone had **no way to zoom or pan
    at all**: no wheel, dead buttons, no gesture. That is the one case the
    feature existed for, and nothing failed; the controls were simply inert.

    Ordering in a file is a strange thing to pin, which is the point — it is
    invisible in a diff, and the only symptom is on a device the suite cannot
    reach.
    """
    source = (ROOT / 'web' / 'static' / 'tacmap.js').read_text(encoding='utf-8')
    assert source.count(_READER_GATE) == 1, 'the read-only gate moved'
    gate = source.index(_READER_GATE)

    late = []
    for name, needle in VIEW_CONTROLS:
        assert source.count(needle) == 1, f'{name}: {needle!r} is not unique'
        if source.index(needle) > gate:
            late.append(name)

    assert not late, (
        'these are below the read-only gate, so a shared map cannot use them: '
        + ', '.join(late))


def test_a_reader_still_gets_pointer_handlers_of_their_own():
    """The editor's handlers sit below the gate and do far more than a reader
    needs, so the reader has a smaller pair rather than both sets fighting
    over the same events. If those go, panning goes with them silently."""
    source = (ROOT / 'web' / 'static' / 'tacmap.js').read_text(encoding='utf-8')
    gate = source.index(_READER_GATE)
    reader = source[gate:source.index('drawLayers();', gate)]
    for event in ('pointerdown', 'pointermove', 'pointerup', 'pointercancel'):
        assert f"addEventListener('{event}'" in reader, (
            f'the read-only branch no longer handles {event}')
