"""The unit's website menu, read off the website so a page here can wear it.

The homepage is a Google Sites page, and Sites has no API for its menu — but the
menu is in the HTML it serves, as plain anchors carrying `data-level`, in order,
so reading it needs a parser and no browser. **The format is Google's and
undocumented**, so two things keep a change on their side from doing damage:
`parse_nav()` answers an empty list rather than a guess when it does not find a
menu, and the caller keeps the last menu that did parse. Standard library only.

What the page contains, as of the page this was written against: the menu is
there **twice** (desktop and mobile) and then once more under a "More" overflow
entry, every copy with `data-level` 1..n for how deep it sits; external links
are wrapped in `https://www.google.com/url?q=<the real link>&…`.
"""

from html import escape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

MAX_ITEMS = 200


class _Anchors(HTMLParser):
    """Every menu anchor in page order: `{level, href, text, more}`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self._cur, self.title, self._in_title = [], None, '', False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title':
            self._in_title = True
        elif tag == 'a' and 'data-navtype' in a and (a.get('data-level') or '').isdigit():
            self._cur = {'level': int(a['data-level']), 'href': a.get('href'),
                         'more': 'data-in-more-submenu' in a, 'text': ''}

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._cur is not None:
            self._cur['text'] += data

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif tag == 'a' and self._cur is not None:
            self.items.append(self._cur)
            self._cur = None


def real_link(href: str, base: str):
    """An absolute http(s) link for a menu entry, or None.

    Sites wraps links that leave the site in a redirect through google.com; the
    wrapper is taken off, because a menu on another site should not send the
    visitor through Google first.
    """
    if not href:
        return None
    url = urljoin(base, href)
    parts = urlparse(url)
    if parts.netloc.endswith('google.com') and parts.path == '/url':
        target = (parse_qs(parts.query).get('q') or [''])[0]
        url, parts = target, urlparse(target)
    return url if parts.scheme in ('http', 'https') and parts.netloc else None


def parse_nav(page: str, base: str) -> dict:
    """`{'title': the site's name, 'items': [{label, href, children: [...]}]}`.

    Only the first copy of the menu is read: it ends where its first entry comes
    round again, which is the mobile copy starting. An entry with no link of its
    own (the "More" overflow) is left out, and so is one whose link is not http(s).
    Returns `items == []` when nothing menu-like is there.
    """
    parser = _Anchors()
    try:
        parser.feed(page)
    except Exception:  # a page this broken has no menu worth showing
        return {'title': '', 'items': []}
    first, seen = None, []
    for item in parser.items[:MAX_ITEMS * 4]:
        if item['more']:
            break
        key = (item['level'], item['href'])
        if first is None:
            first = key
        elif key == first:
            break
        seen.append(item)
    root, stack = [], []   # stack of (level, node)
    for item in seen:
        node = {'label': ' '.join(item['text'].split()), 'href': real_link(item['href'], base),
                'children': []}
        if not node['label'] or node['href'] is None:
            continue
        while stack and stack[-1][0] >= item['level']:
            stack.pop()
        (stack[-1][1]['children'] if stack else root).append(node)
        stack.append((item['level'], node))
    return {'title': ' '.join(parser.title.split()), 'items': root[:MAX_ITEMS]}


CSS = """
.sitenav{position:sticky;top:0;z-index:400;font:14px/1.2 'Open Sans',sans-serif;background:#fff;color:#222;border-top:6px solid #424242;
  border-bottom:1px solid #ddd;display:flex;align-items:center;gap:16px;padding:0 16px;flex-wrap:wrap}
.sitenav a{color:inherit;text-decoration:none}
.sitenav .snbrand{display:flex;align-items:center;gap:10px;padding:8px 0;font-size:16px;margin-right:auto;font-family:'Oswald',sans-serif;font-weight:600}
.sitenav .snbrand img{width:34px;height:34px;border-radius:50%;object-fit:contain}
.sitenav ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap}
.sitenav li{position:relative}
.sitenav li>a{display:block;padding:16px 12px}
.sitenav li:hover>a,.sitenav li:focus-within>a{background:#f0f0f0}
.sitenav li.here>a{background:#212121;color:#fff}
.sitenav li ul{display:none;position:absolute;top:100%;right:0;z-index:50;min-width:200px;flex-direction:column;
  background:#fff;border:1px solid #ddd;box-shadow:0 4px 10px rgba(0,0,0,.15)}
.sitenav li:hover>ul,.sitenav li:focus-within>ul{display:flex}
.sitenav li ul li>a{padding:10px 14px;white-space:nowrap}
.sitenav li ul ul{top:0;right:100%}
"""


def render_nav(nav: dict, here: tuple = ('', ''), logo: str = '', home: str = '', extra: str = '') -> str:
    """The menu as one self-contained block: its style, then the markup.

    No script: a sub-menu opens on hover or keyboard focus. `here` is the
    `(host, path)` of the page being shown; the entry that links to it is drawn
    as the current one. Every string from the parsed page is escaped, and only
    http(s) links get this far. `extra` is markup the caller has already made safe,
    placed at the right-hand end of the bar.
    """
    def is_here(url):
        parts = urlparse(url)
        return bool(here[0]) and (parts.netloc, parts.path.rstrip('/')) == (here[0], here[1].rstrip('/'))

    def entries(items):
        out = []
        for item in items:
            url = item['href']
            sub = f"<ul>{entries(item['children'])}</ul>" if item['children'] else ''
            out.append(f'<li{" class=here" if is_here(url) else ""}><a href="{escape(url, quote=True)}">'
                       f'{escape(item["label"])}</a>{sub}</li>')
        return ''.join(out)

    if not nav.get('items'):
        return ''
    img = f'<img src="{escape(logo, quote=True)}" alt="">' if logo else ''
    brand = (f'<a class="snbrand" href="{escape(home, quote=True)}">{img}'
             f'<span>{escape(nav.get("title") or "")}</span></a>') if home else ''
    return f'<style>{CSS}</style><nav class="sitenav">{brand}<ul>{entries(nav["items"])}</ul>{extra}</nav>'
