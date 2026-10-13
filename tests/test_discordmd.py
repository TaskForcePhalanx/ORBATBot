"""utils/discordmd.py — a drop's text as Discord will show it."""

from datetime import datetime, timezone

from utils import discordmd as md


def test_nothing_typed_becomes_html():
    html = md.render('<script>alert(1)</script> & <b>bold</b>')
    assert '<script>' not in html and '&lt;script&gt;' in html and '&amp;' in html
    assert '<b>' not in html


def test_headings_subtext_quotes_and_lists():
    html = md.render('# Big\n## Mid\n### Small\n-# Part 1/2\n> quoted\n> still\n'
                     '- one\n- two\n3. three\n4. four')
    assert '<div class="dc-h1">Big</div>' in html and 'dc-h2' in html and 'dc-h3' in html
    assert '<div class="dc-sub">Part 1/2</div>' in html
    assert '<blockquote class="dc-quote">quoted<br>still</blockquote>' in html
    assert '<ul class="dc-list"><li>one</li><li>two</li></ul>' in html
    assert '<ol class="dc-list" start="3">' in html


def test_emphasis_and_its_limits():
    html = md.render_inline('**b** *i* __u__ ~~s~~ ||spoil|| snake_case_name 2*3*4')
    assert '<strong>b</strong>' in html and '<em>i</em>' in html and '<u>u</u>' in html
    assert '<s>s</s>' in html and 'dc-spoiler' in html
    assert 'snake_case_name' in html                # an underscore inside a word is a word
    assert r'\*' not in md.render_inline(r'\*not italic\*') and '<em>' not in md.render_inline(r'\*x\*')


def test_code_is_left_alone():
    html = md.render('`**not bold**`\n```\n# not a heading\n<tag>\n```')
    assert '<code class="dc-code">**not bold**</code>' in html
    assert '<pre class="dc-pre"><code># not a heading\n&lt;tag&gt;</code></pre>' in html


def test_links_are_http_only():
    html = md.render_inline('[site](https://example.com/a_b) [bad](javascript:alert(1)) https://x.org/p')
    assert 'href="https://example.com/a_b"' in html and 'rel="noopener noreferrer"' in html
    assert 'href="javascript' not in html and 'href="https://x.org/p"' in html


def test_timestamps_carry_their_moment_and_a_fallback():
    ts = int(datetime(2026, 11, 14, 19, 0, tzinfo=timezone.utc).timestamp())
    html = md.render_inline(f'<t:{ts}:F>', tz='Europe/London')
    assert f'data-ts="{ts}"' in html and 'data-style="F"' in html
    assert 'Saturday, 14 November 2026 19:00' in html
    assert md.timestamp_text(ts, 't', 'Europe/Berlin') == '20:00'
    now = datetime(2026, 10, 24, 19, 0, tzinfo=timezone.utc)
    assert md.timestamp_text(ts, 'R', now=now) == 'in 3 weeks'
    assert md.timestamp_text(ts, 'R', now=datetime(2026, 11, 15, 19, tzinfo=timezone.utc)) == '1 day ago'


def test_mentions_show_names_and_never_raw_ids():
    names = {'channels': {'111111': 'orbat'}, 'roles': {'222222': ('Rifleman', '#e03b3b')}}
    html = md.render_inline('<#111111> <@&222222> <#999999> @everyone', names)
    assert '#orbat' in html and '@Rifleman' in html and '--dc-role:#e03b3b' in html
    assert '#unknown-channel' in html and '@everyone' in html
    assert md.mention_ids('<#1234567> <@&7654321> <@!5555555>') == {
        'channels': {'1234567'}, 'roles': {'7654321'}, 'users': {'5555555'}}


def test_a_role_colour_cannot_inject_css():
    html = md.render_inline('<@&222222>', {'roles': {'222222': ('R', 'red;background:url(x)')}})
    assert 'style=' not in html


def test_blank_lines_are_kept_inside_and_trimmed_at_the_ends():
    html = md.render('\n\nfirst\n\nsecond\n\n')
    assert html.count('dc-gap') == 1


def test_a_real_drop_renders():
    from utils import campaign as cp
    text = '# WARNING ORDER — OPERATION X\n**DTG:** now\n\n> OOC: sign up in <#123456>'
    assert cp.split_message(text) == [text]
    html = md.render(text, {'channels': {'123456': 'orbat'}})
    assert 'WARNING ORDER' in html and '#orbat' in html
