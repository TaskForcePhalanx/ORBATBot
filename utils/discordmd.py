"""Discord message markdown → HTML, for previewing a post as Discord shows it.

The campaign planner's drop preview used to be the raw text in a box —
exactly what the bot sends, `**`, `<t:…>` and all. That is the honest
version and stays; this is the other one, for seeing what readers will see:
headings, bold, quotes, lists, timestamps as dates, channel and role
mentions as pills.

It covers what Discord's own renderer does with a message's content —
not embeds — and is deliberately forgiving rather than exact: a preview
that is a little off is useful, one that raises is not. Three rules matter:

* **It escapes first and marks up second.** The text is what somebody typed
  into a planner field, so nothing in it may become HTML of its own. Anything
  this module generates (code, links, mentions, timestamps) is swapped out for
  a placeholder before the rest is escaped and swapped back in after.
* **Links are http(s) only**, and open in a new tab without a referrer — a
  `javascript:` URL in a masked link stays text.
* **A timestamp carries its moment** (`data-ts`, `data-style`) so a page with
  JavaScript can redraw it in the reader's own zone, the way Discord does,
  and a fallback written in the zone it was given for a page without.

Standard library only, like `utils/campaign.py`, which is why it is tested.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

__all__ = ['render', 'render_inline', 'timestamp_text', 'mention_ids']

_PH = '\x00{}\x00'
_PH_RE = re.compile('\x00(\\d+)\x00')

_TIMESTAMP = re.compile(r'<t:(-?\d{1,13})(?::([tTdDfFR]))?>')
_CHANNEL = re.compile(r'<#(\d{5,25})>')
_ROLE = re.compile(r'<@&(\d{5,25})>')
_USER = re.compile(r'<@!?(\d{5,25})>')
_MASKED = re.compile(r'\[([^\[\]\n]{1,300})\]\(\s*<?(https?://[^\s()<>]+)>?\s*\)')
_URL = re.compile(r'<?(https?://[^\s<>]+[^\s<>.,;:!?\'")\]])>?')
_CODE = re.compile(r'(`{1,2})(?!`)(.+?)(?<!`)\1(?!`)')
_ESCAPED = re.compile(r'\\([*_~|`>#\-\[\]()\\<])')
_EVERYONE = re.compile(r'@(everyone|here)\b')

# Emphasis, applied to text that is already escaped. Order matters: the
# triple and double forms have to go before the single ones they contain.
_EMPHASIS = (
    (re.compile(r'\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*'), r'<strong><em>\1</em></strong>'),
    (re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*'), r'<strong>\1</strong>'),
    (re.compile(r'__(?=\S)(.+?)(?<=\S)__'), r'<u>\1</u>'),
    (re.compile(r'(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])'), r'<em>\1</em>'),
    (re.compile(r'(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])'), r'<em>\1</em>'),
    (re.compile(r'~~(?=\S)(.+?)(?<=\S)~~'), r'<s>\1</s>'),
    (re.compile(r'\|\|(.+?)\|\|'), r'<span class="dc-spoiler">\1</span>'),
)

_DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December')


def _zone(tz: str | None):
    try:
        return ZoneInfo(tz) if tz else timezone.utc
    except Exception:       # an unknown zone is a preview detail, not an error
        return timezone.utc


def timestamp_text(ts: int, style: str = 'f', tz: str | None = None,
                   now: datetime | None = None) -> str:
    """What Discord shows for `<t:ts:style>`, written in `tz` (British
    English, as the unit reads it). Fixed English names rather than
    `strftime('%A')`, which follows the process locale."""
    try:
        moment = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(_zone(tz))
    except (OverflowError, OSError, ValueError):
        return f'<t:{ts}>'
    day = f'{moment.day} {_MONTHS[moment.month - 1]} {moment.year}'
    hm = moment.strftime('%H:%M')
    if style == 't':
        return hm
    if style == 'T':
        return moment.strftime('%H:%M:%S')
    if style == 'd':
        return moment.strftime('%d/%m/%Y')
    if style == 'D':
        return day
    if style == 'F':
        return f'{_DAYS[moment.weekday()]}, {day} {hm}'
    if style == 'R':
        return _relative(moment, now)
    return f'{day} {hm}'


def _relative(moment: datetime, now: datetime | None) -> str:
    now = now or datetime.now(timezone.utc)
    seconds = (moment - now).total_seconds()
    ahead = seconds >= 0
    seconds = abs(seconds)
    for unit, size in (('year', 365 * 86400), ('month', 30 * 86400), ('day', 86400),
                       ('hour', 3600), ('minute', 60)):
        if seconds >= size:
            count = int(seconds // size)
            break
    else:
        return 'in a few seconds' if ahead else 'a few seconds ago'
    if unit == 'day' and count >= 7 and count < 30:
        unit, count = 'week', count // 7
    words = f'{count} {unit}' + ('s' if count != 1 else '')
    return f'in {words}' if ahead else f'{words} ago'


def mention_ids(text: str) -> dict:
    """The channel, role and user ids a text mentions, so a caller can look
    their names up before rendering: `{'channels', 'roles', 'users'}`."""
    return {'channels': set(_CHANNEL.findall(text or '')),
            'roles': set(_ROLE.findall(text or '')),
            'users': set(_USER.findall(text or ''))}


class _Inline:
    """One inline pass: placeholders out, escape, emphasis, placeholders back."""

    def __init__(self, names: dict, tz: str | None, now: datetime | None):
        self.names = names or {}
        self.tz = tz
        self.now = now
        self.held: list[str] = []

    def hold(self, html: str) -> str:
        self.held.append(html)
        return _PH.format(len(self.held) - 1)

    def run(self, text: str, links: bool = True) -> str:
        text = _ESCAPED.sub(lambda m: self.hold(escape(m.group(1))), text)
        text = _CODE.sub(lambda m: self.hold(
            f'<code class="dc-code">{escape(m.group(2).strip() or m.group(2))}</code>'), text)
        text = _TIMESTAMP.sub(self._timestamp, text)
        text = _CHANNEL.sub(self._channel, text)
        text = _ROLE.sub(self._role, text)
        text = _USER.sub(self._user, text)
        text = _EVERYONE.sub(lambda m: self.hold(
            f'<span class="dc-mention">@{m.group(1)}</span>'), text)
        if links:
            text = _MASKED.sub(self._masked, text)
            text = _URL.sub(lambda m: self.hold(self._a(m.group(1), escape(m.group(1)))), text)
        text = escape(text, quote=False)
        for pattern, html in _EMPHASIS:
            text = pattern.sub(html, text)
        return self.restore(text)

    def restore(self, text: str) -> str:
        # Held pieces can hold placeholders of their own (a masked link's text).
        for _ in range(4):
            if '\x00' not in text:
                break
            text = _PH_RE.sub(lambda m: self.held[int(m.group(1))], text)
        return text

    @staticmethod
    def _a(url: str, inner: str) -> str:
        return (f'<a class="dc-link" href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{inner}</a>')

    def _masked(self, m) -> str:
        inner = _Inline(self.names, self.tz, self.now).run(m.group(1), links=False)
        return self.hold(self._a(m.group(2), inner))

    def _timestamp(self, m) -> str:
        ts, style = m.group(1), m.group(2) or 'f'
        text = timestamp_text(int(ts), style, self.tz, self.now)
        return self.hold(f'<span class="dc-ts" data-ts="{ts}" data-style="{style}">'
                         f'{escape(text)}</span>')

    def _channel(self, m) -> str:
        name = self.names.get('channels', {}).get(m.group(1)) or 'unknown-channel'
        return self.hold(f'<span class="dc-mention">#{escape(name)}</span>')

    def _role(self, m) -> str:
        role = self.names.get('roles', {}).get(m.group(1))
        name, colour = (role if isinstance(role, tuple) else (role, None)) if role else ('unknown-role', None)
        style = ''
        if colour and re.fullmatch(r'#[0-9a-fA-F]{6}', colour):
            style = f' style="--dc-role:{colour}"'
        return self.hold(f'<span class="dc-mention dc-role"{style}>@{escape(name)}</span>')

    def _user(self, m) -> str:
        name = self.names.get('users', {}).get(m.group(1)) or 'unknown-user'
        return self.hold(f'<span class="dc-mention">@{escape(name)}</span>')


def render_inline(text: str, names: dict = None, tz: str = None, now: datetime = None) -> str:
    return _Inline(names, tz, now).run(text)


_HEADING = re.compile(r'^(#{1,3}) +(\S.*)$')
_SUBTEXT = re.compile(r'^-# +(\S.*)$')
_QUOTE = re.compile(r'^> ?(.*)$')
_BULLET = re.compile(r'^( *)[-*] +(\S.*)$')
_NUMBER = re.compile(r'^( *)(\d{1,9})\. +(\S.*)$')
_FENCE = re.compile(r'^```(\w*)\s*$')


def render(text: str, names: dict = None, tz: str = None, now: datetime = None) -> str:
    """One message's content as HTML, inside a `dc-content` div.

    `names` maps the ids in mentions to what to show:
    `{'channels': {id: name}, 'roles': {id: name or (name, '#rrggbb')},
    'users': {id: name}}`. `tz` is the zone timestamps are written in for a
    page that will not redraw them; `now` anchors relative ones.
    """
    inline = lambda s: render_inline(s, names, tz, now)
    lines = (text or '').replace('\r\n', '\n').split('\n')
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush():
        if para:
            out.append('<div class="dc-p">' + '<br>'.join(para) + '</div>')
            para.clear()

    while i < len(lines):
        line = lines[i]
        # A fenced block runs to the closing fence, or to the end if none.
        fence = _FENCE.match(line.strip()) if line.lstrip().startswith('```') else None
        if fence is not None:
            flush()
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != '```':
                body.append(lines[i])
                i += 1
            out.append('<pre class="dc-pre"><code>' + escape('\n'.join(body)) + '</code></pre>')
            i += 1
            continue
        if line.startswith('>>> '):
            flush()
            rest = [line[4:]] + lines[i + 1:]
            out.append('<blockquote class="dc-quote">' + '<br>'.join(inline(r) for r in rest)
                       + '</blockquote>')
            break
        if _QUOTE.match(line) and not line.startswith('>>'):
            flush()
            quoted = []
            while i < len(lines) and _QUOTE.match(lines[i]) and not lines[i].startswith('>>'):
                quoted.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append('<blockquote class="dc-quote">' + '<br>'.join(inline(q) for q in quoted)
                       + '</blockquote>')
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f'<div class="dc-h{level}">{inline(heading.group(2))}</div>')
            i += 1
            continue
        sub = _SUBTEXT.match(line)
        if sub:
            flush()
            out.append(f'<div class="dc-sub">{inline(sub.group(1))}</div>')
            i += 1
            continue
        if _BULLET.match(line) or _NUMBER.match(line):
            flush()
            ordered = bool(_NUMBER.match(line))
            items = []
            start = int(_NUMBER.match(line).group(2)) if ordered else 1
            while i < len(lines):
                m = (_NUMBER if ordered else _BULLET).match(lines[i])
                if not m:
                    break
                body = m.group(3) if ordered else m.group(2)
                deep = ' class="dc-deep"' if len(m.group(1)) >= 2 else ''
                items.append(f'<li{deep}>{inline(body)}</li>')
                i += 1
            if ordered:
                attr = f' start="{start}"' if start != 1 else ''
                out.append(f'<ol class="dc-list"{attr}>' + ''.join(items) + '</ol>')
            else:
                out.append('<ul class="dc-list">' + ''.join(items) + '</ul>')
            continue
        if not line.strip():
            flush()
            out.append('<div class="dc-gap"></div>')
            i += 1
            continue
        para.append(inline(line))
        i += 1
    flush()
    # Discord trims blank lines at either end of a message.
    while out and out[0] == '<div class="dc-gap"></div>':
        out.pop(0)
    while out and out[-1] == '<div class="dc-gap"></div>':
        out.pop()
    return '<div class="dc-content">' + ''.join(out) + '</div>'
