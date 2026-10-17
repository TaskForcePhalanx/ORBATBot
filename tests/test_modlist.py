"""Arma 3 Launcher presets: parsing, telling the lists apart, and writing one back.

The classification cases are the ones the old sync script got wrong: a message
saying "weekly operation" went to Joint Op because the first rule to match won.
"""
import pytest

from utils import modlist


def _preset(rows, name='Pacific Line', kind='preset', dlc=''):
    body = ''.join(rows)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<html><head>
<meta name="arma:Type" content="{kind}" />
<meta name="arma:PresetName" content="{name}" />
</head><body>
<div class="mod-list"><table>{body}</table></div>
<div class="dlc-list"><table>{dlc}</table></div>
</body></html>'''


def _mod(name, wid=None, source='steam'):
    link = (f'<td><a href="https://steamcommunity.com/sharedfiles/filedetails/?id={wid}" '
            f'data-type="Link">x</a></td>') if wid else ''
    return (f'<tr data-type="ModContainer"><td data-type="DisplayName">{name}</td>'
            f'<td><span class="from-{source}">{source}</span></td>{link}</tr>')


CBA = _mod('CBA_A3', 450814997)
ACE = _mod('ace', 463939057)


def test_parse_reads_name_mods_and_dlcs():
    dlc = ('<tr data-type="DlcContainer"><td data-type="DisplayName">Global Mobilization</td>'
           '<td><a href="https://store.steampowered.com/app/1042220" data-type="Link">x</a></td></tr>')
    p = modlist.parse_preset(_preset([CBA, ACE], dlc=dlc))
    assert p['name'] == 'Pacific Line'
    assert [m['name'] for m in p['mods']] == ['CBA_A3', 'ace']
    assert p['mods'][0]['id'] == '450814997'
    assert p['dlcs'] == [{'name': 'Global Mobilization', 'id': '1042220',
                          'link': 'https://store.steampowered.com/app/1042220'}]


def test_parse_accepts_a_plain_list_export():
    assert modlist.parse_preset(_preset([CBA], kind='list'))['type'] == 'list'


def test_a_local_mod_has_no_id_and_is_kept():
    p = modlist.parse_preset(_preset([_mod('MyMod', None, 'local')]))
    assert p['mods'][0]['id'] is None and p['mods'][0]['source'] == 'local'


def test_duplicates_collapse():
    assert len(modlist.parse_preset(_preset([CBA, CBA]))['mods']) == 1


@pytest.mark.parametrize('text', [
    '', '<html></html>', 'just words',
    '<html><head><meta name="arma:Type" content="preset"/></head><body></body></html>',
    '<html><head><meta name="arma:Type" content="other"/></head></html>',
])
def test_not_a_preset_is_refused_in_words(text):
    with pytest.raises(ValueError):
        modlist.parse_preset(text)


def test_oversized_file_is_refused():
    with pytest.raises(ValueError):
        modlist.parse_preset('x' * (modlist.MAX_BYTES + 1))


def test_non_http_links_do_not_survive():
    row = ('<tr data-type="ModContainer"><td data-type="DisplayName">Evil</td>'
           '<td><a href="javascript:alert(1)" data-type="Link">x</a></td></tr>')
    p = modlist.parse_preset(_preset([row]))
    assert p['mods'][0]['link'] == ''
    assert 'javascript' not in modlist.render_preset('x', p['mods'])


def test_render_round_trips_and_escapes():
    mods = [{'name': 'A & B <3', 'id': '1', 'source': 'steam',
             'link': 'https://steamcommunity.com/sharedfiles/filedetails/?id=1'}]
    out = modlist.render_preset('My "list"', mods)
    again = modlist.parse_preset(out)
    assert again['name'] == 'My "list"'
    assert again['mods'][0]['name'] == 'A & B <3'
    assert again['mods'][0]['id'] == '1'
    assert '<3' not in out.replace('&lt;3', '')


@pytest.mark.parametrize('content, slot', [
    ('Saturday modlist', 'weekly'),
    ('new weekly campaign list', 'weekly'),
    ('Joint Op this month', 'jointop'),
    ('joint operation list', 'jointop'),
    ('Operation Thunder', 'jointop'),
    ('JointOp', 'jointop'),
])
def test_classify_by_message(content, slot):
    assert modlist.classify(content)[0] == slot


def test_classify_nothing_matched():
    assert modlist.classify('here you go') == (None, [])


def test_classify_ambiguous_is_not_guessed():
    slot, candidates = modlist.classify('weekly, not the joint op one')
    assert slot is None and set(candidates) == {'weekly', 'jointop'}


def test_the_longer_phrase_hides_the_word_it_contains():
    # "joint operation" contains "operation": that is one slot, not two.
    assert modlist.classify('joint operation')[1] == ['jointop']


def test_words_match_whole_words_only():
    assert modlist.classify('the operational plan')[0] is None


def test_message_wins_over_filename_and_preset_name():
    assert modlist.classify('weekly', 'jointop.html', 'JO Cold War')[0] == 'weekly'


def test_filename_and_preset_name_are_the_fallback():
    assert modlist.classify('', 'Joint_Op.html')[0] is None  # "joint_op" is one token
    assert modlist.classify('', 'saturday.html')[0] == 'weekly'
    assert modlist.classify('', 'x.html', 'Weekly Pacific')[0] == 'weekly'


def test_custom_keywords_replace_the_defaults_for_that_slot_only():
    kw = {'weekly': ['pacific']}
    assert modlist.classify('pacific line', keywords=kw)[0] == 'weekly'
    assert modlist.classify('saturday', keywords=kw)[0] is None
    assert modlist.classify('joint op', keywords=kw)[0] == 'jointop'


def test_parse_keywords():
    assert modlist.parse_keywords('a, B\nb,, c') == ['a', 'B', 'c']
    assert len(modlist.parse_keywords(','.join(str(i) for i in range(50)))) == 20


def test_diff_matches_by_workshop_id():
    old = modlist.parse_preset(_preset([CBA, ACE]))['mods']
    new = modlist.parse_preset(_preset([CBA, _mod('ACE3', 463939057), _mod('New', 5)]))['mods']
    d = modlist.diff(old, new)
    assert [m['name'] for m in d['added']] == ['New']
    assert d['removed'] == []
    assert [(a['name'], b['name']) for a, b in d['renamed']] == [('ace', 'ACE3')]
    assert modlist.diff_summary(d) == '+1 mod, 1 mod renamed'
    assert modlist.diff_summary(modlist.diff(old, old)) == ''


def test_diff_summary_pluralises():
    d = modlist.diff([], [{'name': 'a', 'id': '1'}, {'name': 'b', 'id': '2'}])
    assert modlist.diff_summary(d) == '+2 mods'


def test_with_cba_adds_once_and_first():
    mods = modlist.parse_preset(_preset([ACE]))['mods']
    out = modlist.with_cba(mods)
    assert out[0]['id'] == modlist.CBA_ID and len(out) == 2
    assert modlist.with_cba(out) == out


def test_merge_keeps_a_mod_once():
    a = modlist.parse_preset(_preset([CBA, ACE]))['mods']
    b = modlist.parse_preset(_preset([CBA]))['mods']
    assert len(modlist.merge([a, b])) == 2


def test_filename_is_safe():
    assert modlist.filename_for('../../etc/pa ss"wd') == 'etc_pa_ss_wd.html'
    assert modlist.filename_for('') == 'Modlist.html'


def _lists():
    def m(name, wid):
        return {'name': name, 'id': wid, 'source': 'steam',
                'link': f'https://steamcommunity.com/sharedfiles/filedetails/?id={wid}'}
    return {
        'weekly': [m('CBA_A3', '450814997'), m('RHS', '1')],
        'jointop': [m('Jo', '2')],
        'core': [m('ace', '3'), m('acre', '4')],
        'optional': [m('Look', '5')],
    }


def test_selection_takes_an_operation_list_whole():
    out = modlist.build_selection(_lists(), 'weekly')
    assert [x['name'] for x in out] == ['CBA_A3', 'RHS']


def test_selection_adds_picks_and_cba_first():
    out = modlist.build_selection(_lists(), '', {'core': {'3'}, 'optional': {'5'}})
    assert [x['name'] for x in out] == ['CBA_A3', 'ace', 'Look']


def test_selection_dedupes_across_lists():
    out = modlist.build_selection(_lists(), 'weekly', {'core': {'450814997', '3'}})
    assert [x['name'] for x in out].count('CBA_A3') == 1


def test_selection_ignores_stale_picks_and_unknown_operations():
    assert modlist.build_selection(_lists(), 'bogus', {'core': {'999'}}) == []


def test_nothing_selected_is_empty_not_a_lone_cba():
    assert modlist.build_selection(_lists()) == []


def test_picks_for_an_operation_slot_are_not_honoured():
    # Only core and optional are chosen from; a forged `weekly` pick adds nothing.
    assert modlist.build_selection(_lists(), '', {'weekly': {'1'}}) == []
