"""Reading the website's menu: the shape of a Google Sites page, trimmed.

The fixture follows the page the parser was written against: the menu twice
(desktop, mobile), then once more under a "More" entry with its own level
numbers, external links wrapped in a google.com redirect, a title.
"""

from utils import sitenav

BASE = 'https://www.example.org/'


def _a(level, href, text, more=False):
    flag = ' data-in-more-submenu="true"' if more else ''
    return (f'<a data-navtype="1" href="{href}" data-url="{href}"{flag} '
            f'data-level="{level}">{text}</a>')


def _menu(more=False):
    return ''.join([
        _a(1, '/home', 'Home', more),
        _a(1, '/ops', 'Operations &amp; <b>Co</b>', more),
        _a(2, '/ops/signup', 'Signup', more),
        _a(2, 'https://www.google.com/url?q=https%3A%2F%2Fbot.example.org%2Fstats&sa=D&usg=x', 'Stats', more),
        _a(3, '/ops/deep', 'Deep', more),
        _a(2, 'javascript:alert(1)', 'Bad', more),
    ])


PAGE = ('<html><head><title> [TFP] </title></head><body>' + _menu() + _menu()
        + _a(1, '', 'More') + _menu(more=True) + '</body></html>')


def test_parse_reads_one_copy_with_its_tree():
    nav = sitenav.parse_nav(PAGE, BASE)
    assert nav['title'] == '[TFP]'
    home, ops = nav['items']
    assert home['href'] == 'https://www.example.org/home' and home['children'] == []
    assert ops['label'] == 'Operations & Co'
    assert [c['label'] for c in ops['children']] == ['Signup', 'Stats']
    assert ops['children'][1]['children'][0]['label'] == 'Deep'


def test_google_redirect_wrapper_is_removed():
    nav = sitenav.parse_nav(PAGE, BASE)
    assert nav['items'][1]['children'][1]['href'] == 'https://bot.example.org/stats'


def test_non_http_links_are_dropped():
    nav = sitenav.parse_nav(PAGE, BASE)
    assert 'Bad' not in [c['label'] for c in nav['items'][1]['children']]
    assert sitenav.real_link('javascript:alert(1)', BASE) is None
    assert sitenav.real_link('', BASE) is None


def test_no_menu_is_an_empty_list_not_an_error():
    assert sitenav.parse_nav('<html><title>x</title></html>', BASE)['items'] == []
    assert sitenav.parse_nav('<<<', BASE)['items'] == []


def test_render_escapes_and_marks_the_current_page():
    nav = {'title': '<x>', 'items': [{'label': '<b>', 'href': 'https://a.org/p"q', 'children': []},
                                     {'label': 'Stats', 'href': 'https://bot.org/stats', 'children': []}]}
    out = sitenav.render_nav(nav, here=('bot.org', '/stats'), logo='/l.png', home='https://a.org/')
    assert '<b>' not in out and '&lt;b&gt;' in out and '&lt;x&gt;' in out
    assert 'p"q' not in out
    assert out.count('class=here') == 1 and 'class=here><a href="https://bot.org/stats"' in out


def test_render_of_nothing_is_nothing():
    assert sitenav.render_nav({'title': 'x', 'items': []}) == ''
