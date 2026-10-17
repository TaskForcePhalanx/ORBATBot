"""Arma 3 Launcher presets: reading one, telling which list it belongs to, and
writing one back out.

Nothing here imports anything but the standard library, so the tests can reach
all of it. The cog that watches the channel, the web pages and the database sit
on top and only carry the answers.

A preset is the `.html` file the Launcher exports. It is not a format anyone
documents, so this reads exactly what the Launcher writes — a
`<tr data-type="ModContainer">` per mod and a `<tr data-type="DlcContainer">`
per DLC — and nothing it would have to guess at.
"""
import html
import re
from html.parser import HTMLParser

# The four lists a guild keeps, in the order the page shows them. The key is
# what is stored; the label is what people read.
SLOTS = (
    ('weekly', 'Weekly Mission Mods'),
    ('jointop', 'Joint Op Mods'),
    ('core', 'Core Mods'),
    ('optional', 'Optional Mods'),
)
SLOT_KEYS = tuple(key for key, _ in SLOTS)
SLOT_LABELS = dict(SLOTS)

# Weekly and Joint Op are an evening's whole list: a member takes one of them as
# it is. Core and Optional are picked from mod by mod.
OPERATION_SLOTS = ('weekly', 'jointop')
PICKABLE_SLOTS = ('optional',)
# Core is the baseline everybody runs: taken whole or not at all, but listed mod
# by mod so a member can see what it asks of them.
WHOLE_SLOTS = ('core',)

# What the channel's message text is searched for when nothing has been set.
# These are the words the old sync script used, so a channel that worked keeps
# working.
DEFAULT_KEYWORDS = {
    'jointop': ['joint op', 'jointop', 'joint operation', 'operation'],
    'weekly': ['saturday', 'campaign', 'weekly'],
    'core': ['core'],
    'optional': ['optional'],
}

# A real preset is tens of kilobytes. The cap is there so a pasted wall of text
# is not parsed, not because a big list is expected.
MAX_BYTES = 1024 * 1024
MAX_MODS = 2000

CBA_ID = '450814997'
CBA_NAME = 'CBA_A3'

_STEAM_ID = re.compile(r'[?&]id=(\d+)')
_STEAM_APP = re.compile(r'/app/(\d+)')


class _PresetReader(HTMLParser):
    """Collects the two kinds of row and the preset's own name."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ''
        self.mods = []
        self.dlcs = []
        self._row = None          # the row being read: {'kind', 'name', 'source', 'link'}
        self._cell = None         # 'name' while inside the display-name cell
        self._source = False
        self._link = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and a.get('name'):
            self.meta[a['name']] = a.get('content') or ''
        elif tag == 'tr' and a.get('data-type') in ('ModContainer', 'DlcContainer'):
            self._row = {'kind': a['data-type'], 'name': '', 'source': '', 'link': ''}
        elif self._row is not None:
            if tag == 'td' and a.get('data-type') == 'DisplayName':
                self._cell = 'name'
            elif tag == 'span' and (a.get('class') or '').startswith('from-'):
                self._source = True
                self._row['source'] = (a.get('class') or '')[5:]
            elif tag == 'a' and a.get('data-type') == 'Link':
                self._link = True
                self._row['link'] = (a.get('href') or '').strip()

    def handle_endtag(self, tag):
        if tag == 'td':
            self._cell = None
        elif tag == 'span':
            self._source = False
        elif tag == 'a':
            self._link = False
        elif tag == 'tr' and self._row is not None:
            row, self._row = self._row, None
            if row['name'].strip():
                (self.mods if row['kind'] == 'ModContainer' else self.dlcs).append(row)

    def handle_data(self, data):
        if self._row is not None and self._cell == 'name':
            self._row['name'] += data


def steam_id(link: str):
    """The workshop id out of a link, or None for anything that isn't one."""
    m = _STEAM_ID.search(link or '')
    return m.group(1) if m else None


def _safe_link(link: str) -> str:
    """Only http(s) links are kept. The link is shown and written back out, and a
    `javascript:` one in an uploaded file must not survive either."""
    link = (link or '').strip()
    return link if re.match(r'https?://', link, re.I) else ''


def parse_preset(text: str) -> dict:
    """`{'name', 'type', 'mods': [...], 'dlcs': [...]}` out of a Launcher export.

    Raises `ValueError`, with a sentence a person can act on, for anything that
    is not one — an empty file, a different kind of html, a preset with nothing
    in it. Every mod is `{'name', 'id', 'link', 'source'}`; `id` is the workshop
    id, or None for a local mod, which has no link to follow.
    """
    if not isinstance(text, str):
        raise ValueError("That file isn't text.")
    if len(text.encode('utf-8', 'ignore')) > MAX_BYTES:
        raise ValueError('That file is larger than 1 MB — not an Arma preset.')
    reader = _PresetReader()
    try:
        reader.feed(text)
        reader.close()
    except Exception:
        raise ValueError("That file couldn't be read as an Arma 3 Launcher preset.")
    kind = (reader.meta.get('arma:Type') or '').strip().lower()
    if kind not in ('preset', 'list'):
        raise ValueError("That isn't an Arma 3 Launcher export — it has no "
                         "arma:Type of preset.")
    mods, seen = [], set()
    for row in reader.mods:
        link = _safe_link(row['link'])
        wid = steam_id(link)
        key = wid or row['name'].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        mods.append({'name': row['name'].strip(), 'id': wid, 'link': link,
                     'source': row['source'] or ('steam' if wid else 'local')})
    if not mods:
        raise ValueError('That preset has no mods in it.')
    if len(mods) > MAX_MODS:
        raise ValueError('That preset has more than %d mods.' % MAX_MODS)
    dlcs = []
    for row in reader.dlcs:
        link = _safe_link(row['link'])
        m = _STEAM_APP.search(link)
        dlcs.append({'name': row['name'].strip(), 'id': m.group(1) if m else None,
                     'link': link})
    return {'name': (reader.meta.get('arma:PresetName') or '').strip(),
            'type': kind, 'mods': mods, 'dlcs': dlcs}


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').lower())


def classify(content: str, filename: str = '', preset_name: str = '',
             keywords: dict = None) -> tuple:
    """Which list a posted preset belongs to: `(slot, candidates)`.

    * one slot matched → `(slot, [slot])`
    * nothing matched → `(None, [])`
    * more than one → `(None, [slots…])`, which is the caller's cue to *ask*.
      The old script took the first rule that matched, so a message saying
      "weekly operation" went to Joint Op because of one word.

    The message text is read first. The file name and the preset's own name are
    only looked at when the text said nothing, so a clear message always wins.
    `keywords` maps a slot to its words and falls back to `DEFAULT_KEYWORDS` for
    any slot it leaves out.
    """
    words = {slot: list((keywords or {}).get(slot) or DEFAULT_KEYWORDS[slot])
             for slot in SLOT_KEYS}
    for haystack in (content, filename, preset_name):
        text = _norm(haystack)
        if not text:
            continue
        hits = [slot for slot in SLOT_KEYS
                if any(_has_word(text, _norm(w)) for w in words[slot] if w.strip())]
        if hits:
            # "joint operation" contains "operation" and "joint op" contains no
            # other slot's word, so a hit on a longer phrase hides the shorter
            # one it contains — otherwise every Joint Op message is ambiguous.
            hits = _drop_contained(hits, words, text)
            return (hits[0], hits) if len(hits) == 1 else (None, hits)
    return None, []


def _has_word(text: str, word: str) -> bool:
    return bool(word) and re.search(r'(?<![a-z0-9])' + re.escape(word) + r'(?![a-z0-9])', text) is not None


def _drop_contained(hits, words, text):
    """Slots whose only matching words sit inside a longer matching word of another slot."""
    matched = {slot: [_norm(w) for w in words[slot] if _has_word(text, _norm(w))]
               for slot in hits}
    keep = []
    for slot in hits:
        alone = [w for w in matched[slot]
                 if not any(w != o and w in o for other in hits if other != slot
                            for o in matched[other])]
        if alone:
            keep.append(slot)
    return keep or hits


def parse_keywords(text: str) -> list:
    """A comma- or line-separated list of words, as typed into the settings form."""
    out = []
    for part in re.split(r'[,\n]', text or ''):
        part = part.strip()
        if part and part.lower() not in [o.lower() for o in out]:
            out.append(part[:40])
    return out[:20]


def keywords_from_form(typed: dict) -> dict:
    """`{slot: [words]}` from what the settings form typed, one box per list.

    A box left empty is not stored: the list then falls back to
    `DEFAULT_KEYWORDS`, so clearing a box means "use the defaults" rather than
    "match nothing" — a list nothing can ever reach would silently turn every
    post for it into a question.
    """
    out = {}
    for slot in SLOT_KEYS:
        words = parse_keywords((typed or {}).get(slot))
        if words:
            out[slot] = words
    return out


def key_of(mod: dict) -> str:
    return mod.get('id') or 'name:' + _norm(mod.get('name'))


def diff(old: list, new: list) -> dict:
    """What changed between two mod lists: `{'added', 'removed', 'renamed'}`.

    Mods are matched by workshop id, so a rename is a rename and not a removal
    plus an addition; a local mod, with no id, is matched by name.
    """
    before = {key_of(m): m for m in old or []}
    after = {key_of(m): m for m in new or []}
    return {
        'added': [after[k] for k in after if k not in before],
        'removed': [before[k] for k in before if k not in after],
        'renamed': [(before[k], after[k]) for k in after
                    if k in before and before[k]['name'] != after[k]['name']],
    }


def diff_summary(d: dict) -> str:
    """`+3 mods, −1 mod` — empty when nothing changed."""
    def n(count, word='mod'):
        return '%d %s%s' % (count, word, '' if count == 1 else 's')
    parts = []
    if d['added']:
        parts.append('+' + n(len(d['added'])))
    if d['removed']:
        parts.append('−' + n(len(d['removed'])))
    if d['renamed']:
        parts.append(n(len(d['renamed'])) + ' renamed')
    return ', '.join(parts)


def with_cba(mods: list) -> list:
    """The mods with CBA_A3 first, added if it is missing.

    A preset made from Core and Optional alone is not one that loads without
    CBA, and the selector it replaces added it for exactly that reason. A list
    that already carries it is returned as it was.
    """
    if any(m.get('id') == CBA_ID for m in mods):
        return list(mods)
    return [{'name': CBA_NAME, 'id': CBA_ID, 'source': 'steam',
             'link': 'https://steamcommunity.com/sharedfiles/filedetails/?id=' + CBA_ID},
            *mods]


def merge(selections: list) -> list:
    """Several mod lists as one, a mod once however many lists carry it."""
    seen, out = set(), []
    for mods in selections:
        for mod in mods:
            key = key_of(mod)
            if key not in seen:
                seen.add(key)
                out.append(mod)
    return out


def build_selection(current: dict, operation: str = '', core: bool = False,
                    picked: list = None) -> tuple:
    """What the public selector downloads: `(name, mods)`.

    `current` is `{slot: version}`. `operation` is `weekly`, `jointop` or empty:
    an evening's list, taken whole. `core` takes the Core list whole. `picked`
    is a list of mod keys from Optional, the one list chosen from mod by mod.
    Keys that are no longer in the list are ignored rather than refused,
    because the page a member ticked boxes on may be older than the list they
    download. CBA is always included.
    """
    chosen = []
    if operation in OPERATION_SLOTS and current.get(operation):
        chosen.append(current[operation]['mods'])
    if core and current.get('core'):
        chosen.append(current['core']['mods'])
    wanted = set(picked or [])
    if wanted and current.get('optional'):
        chosen.append([m for m in current['optional']['mods'] if key_of(m) in wanted])
    name = ({'weekly': 'TFP Weekly', 'jointop': 'TFP Joint Op'}[operation]
            if operation in OPERATION_SLOTS and current.get(operation) else 'TFP Custom')
    return name, with_cba(merge(chosen))


def render_preset(name: str, mods: list, dlcs: list = ()) -> str:
    """A Launcher preset, ready to be dragged onto it.

    Every value is escaped: a mod called `A & B` or one with a `<` in it would
    otherwise make a file the Launcher refuses.
    """
    esc = lambda s: html.escape(s or '', quote=True)
    rows = []
    for m in mods:
        link = _safe_link(m.get('link'))
        local = m.get('source') == 'local' or not link
        rows.append(
            '        <tr data-type="ModContainer">\n'
            '          <td data-type="DisplayName">%s</td>\n'
            '          <td>\n            <span class="from-%s">%s</span>\n          </td>\n%s'
            '        </tr>\n' % (
                esc(m['name']), 'local' if local else 'steam', 'Local' if local else 'Steam',
                '' if local else
                '          <td>\n            <a href="%s" data-type="Link">%s</a>\n          </td>\n'
                % (esc(link), esc(link))))
    dlc_rows = []
    for d in dlcs:
        link = _safe_link(d.get('link'))
        dlc_rows.append(
            '        <tr data-type="DlcContainer">\n'
            '          <td data-type="DisplayName">%s</td>\n'
            '          <td>\n            <a href="%s" data-type="Link">%s</a>\n          </td>\n'
            '        </tr>\n' % (esc(d['name']), esc(link), esc(link)))
    name = (name or 'Task Force Phalanx').strip()
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<html>\n'
        '  <!--Created by Arma 3 Launcher: https://arma3.com-->\n  <head>\n'
        '    <meta name="arma:Type" content="preset" />\n'
        '    <meta name="arma:PresetName" content="%s" />\n'
        '    <meta name="generator" content="Arma 3 Launcher - https://arma3.com" />\n'
        '    <title>Arma 3</title>\n'
        '    <style>\n'
        'body { margin: 0; padding: 0; color: #fff; background: #000; font: 95%%/1.3 Roboto, Segoe UI, Tahoma, Arial, sans-serif; }\n'
        'td { padding: 3px 30px 3px 0; }\n'
        'h1 { padding: 20px 20px 0 20px; font-weight: 200; font-size: 3em; margin: 0; }\n'
        '.before-list { padding: 5px 20px 10px 20px; }\n'
        '.mod-list, .dlc-list { background: #222222; padding: 20px; }\n'
        '.footer { padding: 20px; color: gray; }\n'
        'a { color: #D18F21; }\n.from-steam { color: #449EBD; }\n.from-local { color: gray; }\n'
        '    </style>\n  </head>\n  <body>\n'
        '    <h1>Arma 3  - Preset <strong>%s</strong></h1>\n'
        '    <p class="before-list">\n'
        '      <em>To import this preset, drag this file onto the Launcher window. Or click the MODS tab, '
        'then PRESET in the top right, then IMPORT at the bottom, and finally select this file.</em>\n'
        '    </p>\n'
        '    <div class="mod-list">\n      <table>\n%s      </table>\n    </div>\n'
        '    <div class="dlc-list">\n      <table>\n%s      </table>\n    </div>\n'
        '    <div class="footer">\n      <span>Created by Task Force Phalanx.</span>\n    </div>\n'
        '  </body>\n</html>\n' % (esc(name), esc(name), ''.join(rows), ''.join(dlc_rows)))


def filename_for(name: str) -> str:
    """A download name that is safe in a header and on every filesystem."""
    base = re.sub(r'[^A-Za-z0-9._-]+', '_', name or '').strip('._') or 'Modlist'
    return base[:60] + '.html'
