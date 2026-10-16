"""`utils/tacmap.py` is pure and imports nothing but the standard library, which
is what makes it testable — and worth testing, because its two jobs both fail
quietly. A parse that drops the wrong thing loses somebody's planning without
saying so, and a render that forgets to escape puts whatever anybody with the
share link typed straight into the page."""

import gzip
import json
import math
import re
import pytest

from utils import tacmap


def _doc(**items):
    doc = tacmap.blank_doc()
    doc['items'] = list(items.get('items', []))
    return doc


def unit(**overrides):
    item = {'kind': 'unit', 'symbol': 'inf', 'side': 'friend', 'x': 100, 'y': 200}
    item.update(overrides)
    return item


def _parse_one(raw):
    """One item, as the document holds it after parsing — never the raw dict."""
    result = tacmap.parse(_doc(items=[raw]))
    assert result.doc['items'], result.warnings
    return result.doc['items'][0]


# -- the document -----------------------------------------------------------

def test_a_blank_map_parses_to_itself():
    result = tacmap.parse(tacmap.dumps(tacmap.blank_doc()))
    assert result.ok
    assert result.doc == tacmap.blank_doc()


def test_a_full_document_survives_a_round_trip():
    doc = _doc(items=[
        unit(label='1-1 Alpha', hq=True, rotation=45, size=1.5),
        {'kind': 'point', 'side': 'hostile', 'x': 10, 'y': 20, 'glyph': 'OBJ'},
        {'kind': 'line', 'side': 'friend', 'points': [[1, 2], [3, 4]], 'arrow': True},
        {'kind': 'area', 'side': 'neutral', 'points': [[1, 1], [9, 1], [5, 9]],
         'style': 'dashed'},
        {'kind': 'text', 'x': 5, 'y': 6, 'label': 'H-hour'},
    ])
    once = tacmap.parse(doc)
    twice = tacmap.parse(tacmap.dumps(once.doc))
    assert once.ok and twice.ok
    assert once.doc == twice.doc
    assert len(twice.doc['items']) == 5


def test_text_that_is_not_json_is_an_error_not_a_crash():
    result = tacmap.parse('{definitely not json')
    assert not result.ok
    assert result.doc == tacmap.blank_doc()


def test_a_document_that_is_not_a_mapping_is_refused():
    assert not tacmap.parse('[1, 2, 3]').ok
    assert not tacmap.parse('"a string"').ok


def test_too_many_items_is_refused_rather_than_trimmed():
    doc = _doc(items=[unit()] * (tacmap.MAX_ITEMS + 1))
    result = tacmap.parse(doc)
    assert not result.ok
    # Trimming would lose the far end of somebody's plan without saying so.
    assert str(tacmap.MAX_ITEMS) in result.errors[0]


# -- items that are not quite right ------------------------------------------

def test_an_unknown_kind_is_dropped_with_a_warning():
    result = tacmap.parse(_doc(items=[{'kind': 'hologram', 'x': 1, 'y': 2}, unit()]))
    assert result.ok
    assert len(result.doc['items']) == 1
    assert result.warnings


def test_an_unknown_side_falls_back_to_friendly():
    assert tacmap.parse(_doc(items=[unit(side='martian')])).doc['items'][0]['side'] \
        == tacmap.DEFAULT_SIDE


def test_an_item_without_a_position_is_dropped():
    result = tacmap.parse(_doc(items=[unit(x=None), unit(y='over there')]))
    assert result.doc['items'] == []
    assert len(result.warnings) == 2


def test_coordinates_that_are_not_numbers_are_not_positions():
    for bad in (float('nan'), float('inf'), '12px', {}):
        assert tacmap.parse(_doc(items=[unit(x=bad)])).doc['items'] == [], bad


def test_a_position_far_outside_the_sheet_is_pulled_back():
    result = tacmap.parse(_doc(items=[unit(x=9_000_000, y=-9_000_000)]))
    item = result.doc['items'][0]
    assert item['x'] == tacmap.DEFAULT_WIDTH * 1.25
    assert item['y'] == -tacmap.DEFAULT_HEIGHT / 4


def test_a_line_needs_two_points_and_an_area_three():
    result = tacmap.parse(_doc(items=[
        {'kind': 'line', 'points': [[1, 1]]},
        {'kind': 'area', 'points': [[1, 1], [2, 2]]},
        {'kind': 'line', 'points': [[1, 1], [2, 2]]},
    ]))
    assert len(result.doc['items']) == 1
    assert len(result.warnings) == 2


def test_points_come_back_as_pairs_however_they_were_written():
    result = tacmap.parse(_doc(items=[
        {'kind': 'line', 'points': [{'x': 1, 'y': 2}, [3, 4], 'nonsense']},
    ]))
    assert result.doc['items'][0]['points'] == [[1.0, 2.0], [3.0, 4.0]]


def test_a_nul_in_a_label_never_reaches_the_column():
    """`tac_maps.doc` is `jsonb`, and `jsonb` refuses `\\u0000` outright where
    `text` and Python's `json` both take it without complaint.

    A label is typed by anybody with an edit link, so a NUL in one used to
    store fine and would now fail every later save of that map — and it would
    have failed migration 3's conversion of the column, which fails the boot.
    `_text()` is the one place every string in a document goes through, which
    is why stripping it there covers all twelve of them.
    """
    import json
    result = tacmap.parse(_doc(items=[unit(label='a\x00b')]))
    label = result.doc['items'][0]['label']
    assert label == 'ab'
    assert '\\u0000' not in json.dumps(result.doc)


def test_control_characters_go_but_newline_and_tab_stay():
    """The rest of the C0 controls are junk in a label too. Newline and tab are
    not: `_label_svg()` and `_sqf_string()` both handle a label with newlines,
    so they are expected rather than noise.
    """
    result = tacmap.parse(_doc(items=[
        unit(label='a\x01\x02\x1fb'),
        unit(label='two\nlines\there'),
    ]))
    assert result.doc['items'][0]['label'] == 'ab'
    assert result.doc['items'][1]['label'] == 'two\nlines\there'


def test_a_label_is_cut_to_length_and_a_glyph_is_shouted():
    result = tacmap.parse(_doc(items=[
        unit(label='x' * 500),
        {'kind': 'point', 'x': 1, 'y': 1, 'glyph': 'objective'},
    ]))
    assert len(result.doc['items'][0]['label']) == tacmap.MAX_LABEL
    assert result.doc['items'][1]['glyph'] == 'OBJE'


def test_size_is_held_between_its_limits():
    items = tacmap.parse(_doc(items=[unit(size=99), unit(size=0)])).doc['items']
    assert items[0]['size'] == tacmap.MAX_SIZE
    assert items[1]['size'] == tacmap.MIN_SIZE


# -- layers ------------------------------------------------------------------

def _layered(*layers, items=()):
    doc = tacmap.blank_doc()
    if layers:
        doc['layers'] = list(layers)
    doc['items'] = list(items)
    return tacmap.parse(doc).doc


def test_a_document_always_has_at_least_one_layer():
    for raw in (None, [], 'phase one', [1, 2], [{'name': 'no id'}]):
        doc = tacmap.blank_doc()
        doc['layers'] = raw
        assert tacmap.parse(doc).doc['layers'] == [dict(tacmap.DEFAULT_LAYER)]


def test_layer_ids_are_slugged_and_never_repeat():
    doc = _layered({'id': 'Phase One!', 'name': 'Phase 1'},
                   {'id': 'phaseone', 'name': 'A second one'},
                   {'id': 'enemy', 'name': 'Feindlage', 'visible': False})
    assert [layer['id'] for layer in doc['layers']] == ['phaseone', 'enemy']
    assert doc['layers'][1]['visible'] is False


def test_an_item_on_a_layer_that_is_not_there_lands_on_the_first():
    doc = _layered({'id': 'plan', 'name': 'Plan'}, {'id': 'enemy', 'name': 'Enemy'},
                   items=[unit(layer='ghosts'), unit(layer='enemy')])
    assert doc['items'][0]['layer'] == 'plan'
    assert doc['items'][1]['layer'] == 'enemy'


def test_what_is_on_a_hidden_layer_is_kept_but_not_drawn():
    doc = _layered({'id': 'plan', 'name': 'Plan'},
                   {'id': 'enemy', 'name': 'Enemy', 'visible': False},
                   items=[unit(label='ours'), unit(label='theirs', layer='enemy')])
    # Both are still in the document — a switch is not a delete.
    assert len(doc['items']) == 2
    assert [item['label'] for item in tacmap.ordered_items(doc)] == ['ours']
    assert len(tacmap.ordered_items(doc, include_hidden=True)) == 2
    assert 'theirs' not in tacmap.render(doc)


def test_the_plan_of_a_hidden_layer_stays_out_of_the_arma_export():
    doc = _layered({'id': 'plan', 'name': 'Plan'},
                   {'id': 'enemy', 'name': 'Enemy', 'visible': False},
                   items=[unit(label='ours'), unit(label='theirs', layer='enemy')])
    script = tacmap.to_sqf(doc, prefix='map1')
    assert script.count('createMarker') == 1
    assert 'theirs' not in script


def test_a_symbol_is_drawn_over_an_area_whatever_order_they_were_added_in():
    doc = _layered(items=[
        unit(label='on top'),
        {'kind': 'area', 'side': 'hostile', 'points': [[1, 1], [9, 1], [5, 9]]},
    ])
    assert [item['kind'] for item in tacmap.ordered_items(doc)] == ['area', 'unit']


def test_two_of_the_same_kind_keep_the_order_the_document_gives_them():
    doc = _layered(items=[unit(label='first'), unit(label='second')])
    assert [item['label'] for item in tacmap.ordered_items(doc)] == ['first', 'second']


def test_a_layer_orders_before_the_kinds_inside_it():
    doc = _layered({'id': 'under', 'name': 'Under'}, {'id': 'over', 'name': 'Over'},
                   items=[
                       unit(label='on the lower layer', layer='under'),
                       {'kind': 'area', 'side': 'hostile', 'layer': 'over',
                        'points': [[1, 1], [9, 1], [5, 9]]},
                   ])
    # The area is on the upper layer, so it wins over the symbol below it.
    assert [item['kind'] for item in tacmap.ordered_items(doc)] == ['unit', 'area']


# -- colours -----------------------------------------------------------------

def test_a_line_may_carry_its_own_colour():
    doc = _layered(items=[{'kind': 'line', 'side': 'friend', 'color': '#FF8A00',
                           'points': [[1, 1], [9, 9]]}])
    assert doc['items'][0]['color'] == '#ff8a00'
    assert '#ff8a00' in tacmap.render(doc)


def test_anything_that_is_not_a_plain_hex_colour_is_dropped():
    for bad in ('red', '#fff', 'rgb(1,2,3)', 'url(#x)', '#12345g', ''):
        doc = _layered(items=[{'kind': 'line', 'color': bad,
                               'points': [[1, 1], [9, 9]]}])
        assert doc['items'][0]['color'] == '', bad


def test_without_one_a_line_is_drawn_in_ink_not_its_sides_colour():
    # This pinned the opposite until the flat-blue plan showed what it cost:
    # a boundary drawn while the Hostile side happened to be picked came out
    # pink, and one drawn on a friendly plan came out the same pale blue as
    # every other line on it. A control measure belongs to the plan.
    doc = _layered(items=[{'kind': 'line', 'side': 'hostile',
                           'points': [[1, 1], [9, 9]]}])
    assert tacmap.item_colour(doc['items'][0]) == tacmap.DEFAULT_INK


def test_a_symbol_takes_no_colour_of_its_own():
    # Whose a unit is has to keep being readable from its colour.
    doc = _layered(items=[unit(color='#ff00ff')])
    assert 'color' not in doc['items'][0]


# -- how small a symbol goes -------------------------------------------------

def test_a_symbol_shrinks_further_than_its_label_does():
    doc = _layered(items=[unit(label='1-1', size=0.05)])
    item = doc['items'][0]
    assert item['size'] == tacmap.MIN_SIZE
    assert tacmap.label_size(item['size']) == tacmap.MIN_LABEL
    assert tacmap.label_size(3) > tacmap.MIN_LABEL


# -- the background ----------------------------------------------------------

def test_a_background_has_to_be_an_http_link():
    doc = tacmap.blank_doc()
    doc['background']['url'] = 'javascript:alert(1)'
    result = tacmap.parse(doc)
    assert result.doc['background']['url'] == ''
    assert result.warnings


def test_a_normal_background_is_kept():
    doc = tacmap.blank_doc()
    doc['background'] = {'url': 'https://example.com/altis.jpg', 'opacity': 0.5}
    result = tacmap.parse(doc)
    assert result.doc['background']['url'] == 'https://example.com/altis.jpg'
    assert result.doc['background']['opacity'] == 0.5
    # A document written before tile sets existed has no kind, and is a picture.
    assert result.doc['background']['kind'] == 'image'
    assert not result.warnings


# -- tile sets ---------------------------------------------------------------

def _tiled(zoom=2, max_zoom=5, url='https://ocap.example/maps/tanoa'):
    doc = tacmap.blank_doc()
    doc['background'] = {'kind': 'tiles', 'url': url, 'opacity': 1.0,
                         'zoom': zoom, 'max_zoom': max_zoom, 'name': 'Tanoa'}
    return tacmap.parse(doc).doc


def test_a_tile_set_draws_one_image_per_tile_from_the_top_left():
    svg = tacmap.render(_tiled(zoom=2))
    assert svg.count('<image') == 16 + 1      # 4 per side, plus the backdrop
    assert 'href="https://ocap.example/maps/tanoa/2/0/0.png" x="0.0" y="0.0"' in svg
    # Column is x and row is y, counted downward — which is the only way OCAP's
    # own tiles assemble into the terrain the right way up.
    assert 'href="https://ocap.example/maps/tanoa/2/3/0.png" x="750.0" y="0.0"' in svg
    assert 'href="https://ocap.example/maps/tanoa/2/0/3.png" x="0.0" y="750.0"' in svg


def test_a_tile_is_never_stretched_past_its_own_cell():
    """The whole reason a road used to jump at a tile boundary.

    A tile drawn bigger than its cell displaces its own contents by the
    difference, which grows from nothing at its left edge to the whole
    overlap at its right — a sawtooth at every seam, and one that zooming in
    magnifies along with everything else in the SVG.
    """
    svg = tacmap.render(_tiled(zoom=2))
    for chunk in svg.split('<image')[2:]:     # [1] is the backdrop
        assert 'width="250.0" height="250.0"' in chunk, chunk[:120]


def test_a_coarse_copy_of_the_terrain_sits_under_the_grid():
    """What covers the hairline now that nothing overlaps: level 0, stretched.

    It is only ever seen through the seam between two neighbours, so one
    request and all the blur in the world are both fine.
    """
    svg = tacmap.render(_tiled(zoom=2))
    backdrop = svg.split('<image')[1]
    assert f'/{tacmap.BACKDROP_ZOOM}/0/0.png' in backdrop
    assert 'x="0" y="0" width="1000" height="1000"' in backdrop
    # It comes first, or it would be painted over the tiles it is backing.
    assert svg.index('/0/0/0.png') < svg.index('/2/0/0.png')


def test_a_sheet_already_showing_level_zero_grows_no_backdrop():
    """There is nothing coarser to put under it, and no seam to cover."""
    svg = tacmap.render(_tiled(zoom=0))
    assert svg.count('<image') == 1


def test_a_tile_zoom_is_held_to_what_the_folder_has():
    assert _tiled(zoom=9, max_zoom=3)['background']['zoom'] == 3
    # And to what this editor is willing to draw, whatever the folder says.
    assert _tiled(zoom=9, max_zoom=9)['background']['zoom'] == tacmap.MAX_TILE_ZOOM


def test_a_tile_base_keeps_no_trailing_slash():
    doc = _tiled(url='https://ocap.example/maps/tanoa/')
    assert doc['background']['url'] == 'https://ocap.example/maps/tanoa'
    assert '/maps/tanoa/2/0/0.png' in tacmap.render(doc)


def test_a_tile_set_with_no_address_is_just_an_empty_sheet():
    doc = _tiled(url='')
    assert '<image' not in tacmap.render(doc)


def test_a_terrain_this_bot_serves_itself_is_a_path_not_an_address():
    # An uploaded terrain lives at /t/{id} here, so moving the site does not
    # leave every map pointing at the old domain.
    doc = _tiled(url='/t/12')
    assert doc['background']['url'] == '/t/12'
    assert 'href="/t/12/2/0/0.png"' in tacmap.render(doc)


def test_no_other_path_is_accepted_as_a_background():
    for url in ('/t/', '/t/12/', '/tiles/12', '../t/12', 'javascript:alert(1)',
                'data:image/png;base64,AAAA', '//evil.example/t/1'):
        assert _tiled(url=url)['background']['url'] == '', url


def test_an_uploaded_terrain_settles_the_background_and_the_corners():
    settings = tacmap.terrain_settings(12, 'Tanoa', 15360, 4)
    assert settings['background'] == {
        'kind': 'tiles', 'url': '/t/12', 'opacity': 1.0, 'zoom': 4,
        'max_zoom': 4, 'name': 'Tanoa',
    }
    assert settings['arma'] == {'terrain': 'Tanoa', 'left': 0.0, 'bottom': 0.0,
                                'right': 15360.0, 'top': 15360.0}


def test_a_shallow_terrain_is_not_asked_for_a_level_it_has_not_got():
    assert tacmap.terrain_settings(1, 'Small', 4096, 2)['background']['zoom'] == 2


# -- reading OCAP's map.json -------------------------------------------------

_CHAM = {'name': 'Cham', 'worldName': 'tem_cham', 'worldSize': 8192,
         'imageSize': 16384, 'multiplier': 2, 'hasTopo': True, 'maxZoom': 6,
         'attribution': 'Temppa'}


def test_an_ocap_map_gives_both_the_background_and_the_calibration():
    settings = tacmap.ocap_settings(_CHAM, 'https://ocap.example/maps/tem_cham/')
    assert settings['background']['kind'] == 'tiles'
    assert settings['background']['url'] == 'https://ocap.example/maps/tem_cham'
    assert settings['background']['name'] == 'Cham'
    # The pyramid covers the whole terrain, so the corners are the world's.
    assert settings['arma'] == {'terrain': 'Cham', 'left': 0.0, 'bottom': 0.0,
                                'right': 8192.0, 'top': 8192.0}


def test_the_deepest_zoom_comes_from_the_image_the_folder_actually_holds():
    # 16384 px of 256 px tiles is 64 per side, which is six doublings.
    assert tacmap.ocap_settings(_CHAM, 'https://x/y')['background']['max_zoom'] == 6
    # A map.json claiming more levels than its image has is not believed.
    small = dict(_CHAM, imageSize=1024, maxZoom=6)
    assert tacmap.ocap_settings(small, 'https://x/y')['background']['max_zoom'] == 2


def test_a_map_json_without_a_world_size_is_refused_with_a_message():
    for payload in ({}, {'name': 'Cham'}, {'worldSize': 0}, {'worldSize': 'big'}):
        try:
            tacmap.ocap_settings(payload, 'https://x/y')
        except ValueError as e:
            assert 'worldSize' in str(e)
        else:
            raise AssertionError(f'{payload} should have been refused')


def test_an_imported_terrain_lands_where_arma_puts_it():
    settings = tacmap.ocap_settings(_CHAM, 'https://x/y')
    doc = tacmap.blank_doc()
    doc['background'] = settings['background']
    doc['arma'] = settings['arma']
    doc = tacmap.parse(doc).doc
    assert tacmap.to_world(doc, 500, 500) == (4096.0, 4096.0)
    assert tacmap.to_world(doc, 0, 1000) == (0.0, 0.0)


# -- drawing -----------------------------------------------------------------


def test_the_pictogram_fits_inside_every_frame():
    """The icon box is the largest one a diamond will take without clipping.

    A diamond is narrowest exactly where the pictogram is tallest, which is
    what this guards: a frame drawn as small as the rectangle cuts the arms
    off an infantry X.
    """
    half = 50.0  # the hostile diamond runs corner to corner across the box
    for y in (33, 67):
        reach = half * (1 - abs(y - 50) / half)
        assert 50 - reach <= 25 and 50 + reach >= 75


def test_an_unknown_marker_falls_back_to_the_dot():
    item = _parse_one({'kind': 'point', 'x': 10, 'y': 10, 'marker': 'spaceship'})
    assert item['marker'] == tacmap.DEFAULT_MARKER
    assert tacmap._arma_type(item) == 'mil_dot'


def test_the_civilian_side_exports_as_its_own_colour():
    doc = tacmap.blank_doc()
    doc['items'] = [{'kind': 'unit', 'side': 'civ', 'x': 10, 'y': 10,
                     'symbol': 'installation'}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='m1')
    assert 'ColorCIV' in script
    assert 'u_installation' in script


def test_a_new_item_starts_at_the_smallest_size():
    """The editor reads the starting size out of the catalog, and it is MIN_SIZE.

    Every plan drawn here came out too big and was shrunk item by item, which
    is the wrong way round: growing the two symbols that need to stand out is
    one drag, shrinking twenty is twenty. `web/static/tacmap.js` places a new
    unit, marker, label, line and area at `catalog.defaultSize`, so this is the
    one number that decides it.
    """
    catalog = tacmap.catalog()
    assert catalog['defaultSize'] == tacmap.MIN_SIZE
    assert catalog['defaultSize'] == catalog['limits']['minSize']
    # The slider has to be able to show it: its first stop is the minimum.
    assert tacmap.MIN_SIZE < tacmap.MAX_SIZE


def test_the_smallest_size_survives_a_round_trip():
    """A document saved at the default size comes back at it, not clamped or
    rounded away — `parse()` rounds to two places, so MIN_SIZE has to be a
    value that survives that."""
    doc = tacmap.parse(_doc(items=[unit(size=tacmap.DEFAULT_SIZE)])).doc
    assert doc['items'][0]['size'] == tacmap.DEFAULT_SIZE


def test_a_stored_item_with_no_size_still_draws_at_one():
    """The new-item default is not `parse()`'s fallback. Making them the same
    would silently shrink every item in an already-drawn map that happens to
    carry no size."""
    doc = tacmap.parse(_doc(items=[{'kind': 'unit', 'x': 100, 'y': 100}])).doc
    assert doc['items'][0]['size'] == 1.0


def test_a_line_is_as_many_screen_pixels_thick_as_it_says():
    """A line keeps its width at every zoom, the way Arma's map lines do — so
    the thinnest is a true 1px line, not a size-scaled smear."""
    doc = tacmap.parse(_doc(items=[{
        'kind': 'line', 'size': tacmap.DEFAULT_SIZE, 'style': 'dashed', 'weight': 1,
        'points': [[10, 10], [200, 200]],
    }])).doc
    svg = tacmap.render(doc)
    assert re.search(r'stroke-width="1" vector-effect="non-scaling-stroke"', svg)
    assert doc['items'][0]['weight'] == 1


def test_a_line_from_before_weights_keeps_about_its_thickness():
    """A line saved before it had a weight was as thick as its size made it."""
    old = tacmap.parse(_doc(items=[{'kind': 'line', 'size': 1.0,
                                    'points': [[10, 10], [200, 200]]}])).doc
    small = tacmap.parse(_doc(items=[{'kind': 'line', 'size': tacmap.DEFAULT_SIZE,
                                      'points': [[10, 10], [200, 200]]}])).doc
    assert old['items'][0]['weight'] == 4 and small['items'][0]['weight'] == 2


def test_an_ops_trace_draws_lines_as_thick_as_the_page_does():
    """resvg ignores non-scaling strokes, so a picture of a known size gets
    its widths in sheet units per pixel instead."""
    doc = tacmap.parse(_doc(items=[{'kind': 'line', 'weight': 2,
                                    'points': [[10, 10], [200, 200]]}])).doc
    svg = tacmap.render(doc, view=(0, 0, 500, 500), size=(1000, 1000))
    assert 'stroke-width="1"' in svg


def test_the_editor_is_told_both_floors():
    """`web/static/tacmap.js` mirrors line_size() and sizes its pointer targets
    from these, so they travel in the catalog rather than being written twice."""
    catalog = tacmap.catalog()
    assert catalog['minLineSize'] == tacmap.MIN_LINE_SIZE
    assert catalog['minHit'] == tacmap.MIN_HIT
    # A target smaller than the smallest symbol would defeat the point.
    assert tacmap.MIN_HIT > tacmap.UNIT_BOX * tacmap.DEFAULT_SIZE


def test_a_label_is_escaped_into_the_drawing():
    doc = tacmap.parse(_doc(items=[unit(label='Ammo <b>&</b>')])).doc
    svg = tacmap.render(doc)
    assert 'Ammo &lt;b&gt;&amp;&lt;/b&gt;' in svg
    assert '<b>' not in svg


def test_a_background_url_cannot_break_out_of_its_attribute():
    from xml.etree import ElementTree

    hostile = 'https://example.com/x.jpg" onload="alert(1)'
    doc = tacmap.blank_doc()
    doc['background']['url'] = hostile
    svg = tacmap.render(tacmap.parse(doc).doc, standalone=True)

    image = ElementTree.fromstring(svg).find('.//{http://www.w3.org/2000/svg}image')
    assert image.get('{http://www.w3.org/1999/xlink}href') == hostile \
        or image.get('href') == hostile
    assert 'onload' not in image.attrib


def test_every_kind_draws_something():
    for item in (unit(),
                 {'kind': 'point', 'x': 1, 'y': 1, 'glyph': 'OBJ'},
                 {'kind': 'line', 'points': [[1, 1], [2, 2]], 'arrow': True},
                 {'kind': 'area', 'points': [[1, 1], [9, 1], [5, 9]]},
                 {'kind': 'text', 'x': 1, 'y': 1, 'label': 'Phase 1'}):
        doc = tacmap.parse(_doc(items=[item])).doc
        assert tacmap.item_svg(doc['items'][0]).strip(), item['kind']


def _label_y(svg: str) -> float:
    return float(svg.split('<text')[1].split('y="')[1].split('"')[0])


def test_the_grid_is_only_drawn_when_it_is_asked_for():
    doc = tacmap.blank_doc()
    assert '<line' not in tacmap.render(doc)
    doc['grid'] = {'show': True, 'cols': 4, 'rows': 4}
    assert tacmap.render(tacmap.parse(doc).doc).count('<line') == 6


def test_a_standalone_render_names_the_svg_namespace():
    assert 'xmlns=' in tacmap.render(tacmap.blank_doc(), standalone=True)
    assert 'xmlns=' not in tacmap.render(tacmap.blank_doc())


# -- the Arma 3 export -------------------------------------------------------

def _altis():
    doc = tacmap.blank_doc()
    doc['arma'] = {'terrain': 'Altis', 'left': 0, 'bottom': 0,
                   'right': 30720, 'top': 30720}
    return doc


def test_the_sheet_corners_become_world_corners_with_y_flipped():
    doc = tacmap.parse(_altis()).doc
    # The sheet's top-left is the world's north-west, which is y = top.
    assert tacmap.to_world(doc, 0, 0) == (0.0, 30720.0)
    assert tacmap.to_world(doc, 1000, 1000) == (30720.0, 0.0)
    assert tacmap.to_world(doc, 500, 500) == (15360.0, 15360.0)


def test_a_cropped_map_maps_onto_its_own_corners():
    doc = _altis()
    doc['arma'] = {'terrain': 'Altis', 'left': 10000, 'bottom': 5000,
                   'right': 14000, 'top': 9000}
    doc = tacmap.parse(doc).doc
    assert tacmap.to_world(doc, 0, 1000) == (10000.0, 5000.0)
    assert tacmap.to_world(doc, 1000, 0) == (14000.0, 9000.0)


def test_inside_out_corners_are_refused_rather_than_divided_by():
    doc = _altis()
    doc['arma'] = {'terrain': '', 'left': 900, 'bottom': 0, 'right': 100, 'top': 0}
    result = tacmap.parse(doc)
    assert result.warnings
    assert result.doc['arma']['right'] > result.doc['arma']['left']
    assert result.doc['arma']['top'] > result.doc['arma']['bottom']


def test_a_unit_becomes_a_nato_marker_of_its_own_side():
    doc = _altis()
    doc['items'] = [unit(symbol='armor', side='hostile', x=500, y=500,
                         label='BMP-2')]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerType "o_armor";' in script
    assert '_m setMarkerColor "ColorEAST";' in script
    assert '_m setMarkerText "BMP-2";' in script
    assert 'createMarker [_p + "1", [15360.0, 15360.0]];' in script


def test_a_headquarters_exports_as_the_headquarters_marker():
    doc = _altis()
    doc['items'] = [unit(hq=True, symbol='med')]
    assert '"b_hq"' in tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')


def test_an_arrow_head_points_along_the_last_leg():
    doc = _doc(items=[{'kind': 'line', 'points': [[0, 100], [200, 100]], 'arrow': True}])
    item = tacmap.parse(doc).doc['items'][0]
    tip, left, right = tacmap.arrow_head(item['points'], item['size'])
    assert tip[0] > 200                       # beyond the end, pointing east
    assert left[0] < 200 and right[0] < 200   # both corners trail behind it
    assert left[1] != right[1]                # one either side of the line


def test_an_arrow_on_a_line_of_no_length_is_simply_not_drawn():
    assert tacmap.arrow_head([[10, 10], [10, 10]], 1) == []


def test_a_line_becomes_a_polyline_and_its_arrow_a_second_marker():
    doc = _altis()
    doc['items'] = [{'kind': 'line', 'side': 'friend', 'arrow': True,
                     'points': [[0, 1000], [1000, 1000]]}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerShape "POLYLINE";' in script
    assert '_m setMarkerPolyline [0.0, 0.0, 30720.0, 0.0];' in script
    assert '_m setMarkerType "mil_arrow";' in script
    # Due east along the bottom edge of the map.
    assert '_m setMarkerDir 90.0;' in script


def test_an_area_closes_its_polyline():
    doc = _altis()
    doc['items'] = [{'kind': 'area', 'side': 'friend',
                     'points': [[0, 0], [1000, 0], [1000, 1000]]}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    polyline = script.split('setMarkerPolyline [')[1].split(']')[0]
    corners = polyline.split(', ')
    assert corners[:2] == corners[-2:]      # Arma has no closed polyline


def test_the_script_clears_its_own_markers_before_drawing():
    doc = _altis()
    doc['items'] = [unit()]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert 'private _p = "map7_";' in script
    assert 'deleteMarker' in script
    assert script.index('deleteMarker') < script.index('createMarker')


def test_a_prefix_cannot_carry_anything_but_letters_and_digits():
    # The prefix is written into the script as code, so the characters that
    # would end the string and start a statement are the ones that matter.
    doc = tacmap.parse(_altis()).doc
    script = tacmap.to_sqf(doc, prefix='map7"; deleteVehicle player; //')
    assert 'private _p = "map7deleteVehicleplayer_";' in script
    assert '";' not in script.split('\n')[3].replace('_";', '')
    assert 'deleteVehicle player' not in script


def test_a_quote_in_a_label_is_doubled_the_way_sqf_wants_it():
    doc = _altis()
    doc['items'] = [unit(label='Says "hi"')]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerText "Says ""hi""";' in script


def test_an_empty_map_still_produces_a_runnable_script():
    script = tacmap.to_sqf(tacmap.parse(_altis()).doc, prefix='map7')
    assert 'createMarker' not in script
    assert 'Nothing is drawn' in script


# -- handing the document to a page ------------------------------------------

def test_json_for_a_page_cannot_close_the_script_tag():
    payload = tacmap.json_payload({'label': '</script><img src=x onerror=alert(1)>'})
    assert '</script' not in payload
    assert '<' not in payload and '>' not in payload
    import json
    assert json.loads(payload)['label'] == '</script><img src=x onerror=alert(1)>'


def test_the_summary_counts_what_is_on_the_map():
    doc = tacmap.parse(_doc(items=[
        unit(), unit(),
        {'kind': 'line', 'points': [[1, 1], [2, 2]]},
    ])).doc
    assert tacmap.summarise(doc) == '2 units · 1 line'
    assert tacmap.summarise(tacmap.blank_doc()) == 'empty'


def test_the_export_is_read_only_in_game_by_default():
    doc = _doc(items=[unit(label='A')])
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1')
    assert '_USER_DEFINED' not in script
    assert 'private _p = "map1_";' in script


def test_the_editable_export_names_markers_the_way_arma_does():
    """The engine parses `_USER_DEFINED #owner/index/channel` to decide who
    may move or delete a marker, and all three parts are numbers. Sticking
    `_USER_DEFINED ` in front of our own prefix is not that shape — it was
    tried in a mission and the markers stayed read-only."""
    doc = _doc(items=[unit(label='A')])
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1', editable=True)
    assert 'format ["_USER_DEFINED #%1/", clientOwner]' in script
    assert '_own + "1001" + _c' in script
    assert '_USER_DEFINED map1_' not in script


def test_the_editable_export_says_to_run_it_once():
    """GLOBAL EXEC runs the code everywhere, and each machine would write its
    own `clientOwner` into the name — one plan per player."""
    doc = _doc(items=[unit(label='A')])
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1', editable=True)
    assert 'LOCAL EXEC' in script
    assert 'GLOBAL EXEC would draw one set per machine' in script


def test_the_channel_is_the_last_part_of_the_name():
    doc = _doc(items=[unit(label='A')])
    for key, _name in tacmap.ARMA_CHANNELS:
        script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1',
                               editable=True, channel=key)
        assert f'private _chan = {key};' in script
    # A channel this version does not have falls back rather than writing a
    # name the engine cannot parse.
    odd = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1', editable=True,
                        channel=99)
    assert f'private _chan = {tacmap.DEFAULT_CHANNEL};' in odd


def test_two_maps_editable_markers_do_not_collide():
    """The index is numeric, so the map's prefix cannot ride inside the name —
    its digits do that job instead."""
    doc = _doc(items=[unit(label='A')])
    first = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1', editable=True)
    second = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map2', editable=True)
    assert '_own + "1001" + _c' in first
    assert '_own + "2001" + _c' in second
    assert 'tacmap_map1' in first and 'tacmap_map2' in second


def test_both_exports_delete_what_they_are_about_to_draw():
    """One by name, one by the list it wrote down — the same promise."""
    doc = _doc(items=[unit(label='A')])
    plain = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1')
    assert 'private _p = "map1_";' in plain
    assert '_x select [0, count _p] == _p' in plain

    editable = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map1', editable=True)
    assert ('{ deleteMarker _x } forEach (missionNamespace getVariable '
            '["tacmap_map1", []]);') in editable
    assert 'missionNamespace setVariable ["tacmap_map1", _n, true];' in editable
    # Every marker it draws goes into that list, or the next run leaves some.
    assert editable.count('_m = createMarker') == editable.count('_n pushBack _m;')


# ---------------------------------------------------------------------------
# The frame says the dimension and the status, the way APP-6 does
# ---------------------------------------------------------------------------


def test_a_line_has_no_status_because_style_already_says_it():
    item = _parse_one({'kind': 'line', 'points': [[1, 1], [2, 2]],
                       'status': 'planned'})
    assert 'status' not in item


def _label_baseline(svg: str) -> float:
    """Where the item's name was drawn — the one `tm-label` outside the frame."""
    match = re.search(r'<text x="[-\d.]+" y="([-\d.]+)"[^>]*class="tm-label"', svg)
    assert match, svg
    return float(match.group(1))


def test_an_air_symbol_exports_as_armas_own_air_marker():
    assert tacmap._arma_type(_parse_one(unit(dimension='air', symbol='inf'))) == 'b_air'
    # A symbol that is already an aircraft keeps its own marker.
    assert tacmap._arma_type(_parse_one(unit(dimension='air', symbol='uav'))) == 'b_uav'
    assert tacmap._arma_type(_parse_one(unit(symbol='inf'))) == 'b_inf'


# ---------------------------------------------------------------------------
# Reading an OCAP directory, so a terrain is picked rather than typed
# ---------------------------------------------------------------------------

NGINX_HTML = '''<html><head><title>Index of /images/maps/</title></head><body>
<h1>Index of /images/maps/</h1><hr><pre><a href="../">../</a>
<a href="altis/">altis/</a>                 01-Mar-2025 12:00     -
<a href="tem_cham/">tem_cham/</a>           02-Mar-2025 12:00     -
<a href="Tanoa/">Tanoa/</a>                 03-Mar-2025 12:00     -
<a href="readme.txt">readme.txt</a>         03-Mar-2025 12:00    12
</pre><hr></body></html>'''


def test_an_html_autoindex_gives_its_folders():
    assert tacmap.parse_map_index(NGINX_HTML) == ['altis', 'Tanoa', 'tem_cham']


def test_the_listing_is_read_from_bytes_too():
    """It arrives off the wire, and its encoding is not this module's business."""
    assert tacmap.parse_map_index(NGINX_HTML.encode()) == ['altis', 'Tanoa', 'tem_cham']


def test_a_file_beside_the_folders_is_not_a_terrain():
    """The trailing slash is the only thing on the page that tells them apart."""
    assert 'readme.txt' not in tacmap.parse_map_index(NGINX_HTML)


def test_an_index_that_marks_no_folders_is_taken_at_its_word():
    """A hand-written index links without slashes; refusing it helps nobody."""
    page = '<a href="altis">altis</a> <a href="tanoa">tanoa</a>'
    assert tacmap.parse_map_index(page) == ['altis', 'tanoa']


def test_nginx_json_listings_are_read_as_well():
    payload = ('[{"name":"altis","type":"directory"},'
               '{"name":"notes.txt","type":"file"},'
               '{"name":"tanoa","type":"directory"}]')
    assert tacmap.parse_map_index(payload) == ['altis', 'tanoa']


def test_a_plain_json_list_of_names_works():
    assert tacmap.parse_map_index('["tanoa", "altis"]') == ['altis', 'tanoa']


def test_a_listing_never_sends_the_reader_somewhere_else():
    """The links are somebody else's page; only a name in this folder is used."""
    page = ('<a href="https://elsewhere.example/evil/">elsewhere</a>'
            '<a href="/absolute/">absolute</a>'
            '<a href="../">parent</a>'
            '<a href="mailto:a@b.c">mail</a>'
            '<a href="tanoa/">tanoa</a>')
    assert tacmap.parse_map_index(page) == ['tanoa']


def test_a_percent_encoded_name_comes_back_readable():
    assert tacmap.parse_map_index('<a href="tem%5Fcham/">x</a>') == ['tem_cham']


def test_something_that_is_not_a_listing_yields_nothing():
    """Better an empty list the page can explain than a guess."""
    assert tacmap.parse_map_index('<h1>403 Forbidden</h1>') == []
    assert tacmap.parse_map_index('') == []
    assert tacmap.parse_map_index(b'\x00\x01\x02') == []


def test_a_listing_longer_than_any_map_directory_is_cut_off():
    page = ''.join(f'<a href="m{n}/">m{n}</a>' for n in range(400))
    assert len(tacmap.parse_map_index(page)) == tacmap.MAX_INDEX_ENTRIES


# ---------------------------------------------------------------------------
# What a line may be painted
# ---------------------------------------------------------------------------


def test_every_palette_colour_survives_the_parser():
    """The palette is offered by the editor, so it has to be storable."""
    for value, label in tacmap.LINE_COLOURS:
        assert tacmap._colour(value) == value, label
        item = _parse_one({'kind': 'line', 'points': [[1, 1], [2, 2]],
                           'color': value})
        assert item['color'] == value, label


def test_the_palette_is_in_the_catalog_for_the_editor():
    palette = tacmap.catalog()['lineColours']
    assert len(palette) == len(tacmap.LINE_COLOURS)
    assert {entry['label'] for entry in palette} >= {
        'Red', 'Yellow', 'Green', 'Blue', 'Pink', 'Purple'}


def test_a_symbol_still_cannot_take_a_colour_of_its_own():
    """Whose a unit is has to stay readable from its colour."""
    item = _parse_one(unit(color=tacmap.LINE_COLOURS[0][0]))
    assert 'color' not in item


def test_a_line_carries_no_arrow_unless_it_was_asked_for():
    """An arrow says "this way", and most lines on a plan say no such thing."""
    item = _parse_one({'kind': 'line', 'points': [[1, 1], [2, 2]]})
    assert item['arrow'] == ''
    assert '<polygon' not in tacmap.item_svg(item)
    # A line saved when the arrow was a plain tick had it at the end.
    pointed = _parse_one({'kind': 'line', 'points': [[1, 1], [2, 2]], 'arrow': True})
    assert pointed['arrow'] == 'end'
    assert tacmap.item_svg(pointed).count('<polygon') == 1
    both = _parse_one({'kind': 'line', 'points': [[1, 1], [9, 9]], 'arrow': 'both'})
    assert tacmap.item_svg(both).count('<polygon') == 2


# ---------------------------------------------------------------------------
# Place names
# ---------------------------------------------------------------------------
#
# The terrain's own town names. They come in from two sources that are not ours
# to dictate — an archive's `locations` list and the clipboard dump out of a
# running mission — so the parser is the thing that has to be forgiving, and
# the geometry is the thing that has to be exact: a name half a kilometre off
# is worse than no name, because it reads as authoritative.


def _placed(*places):
    """A whole-terrain document, 1000 m square, with `places` to draw on it."""
    doc = tacmap.blank_doc()
    doc['arma'] = {'terrain': 'test', 'left': 0.0, 'bottom': 0.0,
                   'right': 1000.0, 'top': 1000.0}
    return doc, list(places)


def test_the_grad_meh_shape_is_read():
    """{"name": …, "pos": [x, y, elevation]} — what an archive may carry."""
    places, warnings = tacmap.parse_places(
        '[{"name": "Air Station Mike-26", "pos": [4278.85, 3855.6, -217.8]}]')
    assert warnings == []
    assert places == [{'name': 'Air Station Mike-26', 'kind': 'namelocal',
                       'x': 4278.9, 'y': 3855.6}]


def test_the_clipboard_shape_is_read():
    """What places_sqf() writes: name, kind and a flat x/y."""
    places, warnings = tacmap.parse_places(
        '{"terrain": "altis", "locations": '
        '[{"name": "Kavala", "kind": "NameCity", "x": 3500, "y": 13300}]}')
    assert warnings == []
    assert places[0]['kind'] == 'namecity'
    assert (places[0]['x'], places[0]['y']) == (3500.0, 13300.0)


def test_a_bare_map_json_is_unwrapped():
    places, _ = tacmap.parse_places(
        {'worldName': 'tanoa', 'locations': [{'name': 'Katkoula', 'pos': [1, 2]}]})
    assert [place['name'] for place in places] == ['Katkoula']


def test_unnamed_helpers_are_dropped_and_counted():
    """Arma terrains are full of FlatArea/Invisible entries that position
    things and have no name. They are not an error, but silence would look
    like data loss."""
    places, warnings = tacmap.parse_places(
        '[{"name": "", "pos": [1, 2]}, {"name": "Real", "pos": [3, 4]}]')
    assert [place['name'] for place in places] == ['Real']
    assert 'skipped' in warnings[0]


def test_one_skipped_helper_is_singular():
    _, warnings = tacmap.parse_places('[{"name": "", "pos": [1, 2]}]')
    assert '1 unnamed location was skipped' in warnings[0]


def test_an_entry_with_no_position_is_dropped():
    places, _ = tacmap.parse_places('[{"name": "Nowhere"}]')
    assert places == []


def test_broken_json_is_a_message_not_an_exception():
    places, warnings = tacmap.parse_places('not json at all')
    assert places == []
    assert 'valid JSON' in warnings[0]


def test_json_without_a_locations_list_says_so():
    places, warnings = tacmap.parse_places('{"worldName": "altis"}')
    assert places == []
    assert 'locations' in warnings[0]


def test_an_unknown_location_type_still_lands_somewhere():
    """A modded terrain may use a type this table has never heard of. Losing
    the name over that would be worse than filing it under the default."""
    places, _ = tacmap.parse_places('[{"name": "X", "kind": "SomeModType", "x": 1, "y": 2}]')
    assert places[0]['kind'] == tacmap.DEFAULT_PLACE_KIND


def test_the_place_list_is_capped():
    raw = [{'name': f'P{n}', 'x': 1, 'y': 2} for n in range(tacmap.MAX_PLACES + 50)]
    places, warnings = tacmap.parse_places(raw)
    assert len(places) == tacmap.MAX_PLACES
    assert 'first' in warnings[0]


def test_every_kind_belongs_to_a_real_group():
    known = {key for key, _ in tacmap.PLACE_GROUPS}
    for kind, spec in tacmap.PLACE_KINDS.items():
        assert spec['group'] in known, kind
        assert spec['rank'] in tacmap.PLACE_SIZE, kind


# --- the geometry ---------------------------------------------------------

def test_from_world_is_the_inverse_of_to_world():
    doc, _ = _placed()
    for point in ((0, 0), (100, 200), (640, 480)):
        assert tacmap.from_world(doc, *tacmap.to_world(doc, *point)) == \
            (float(point[0]), float(point[1]))


def test_the_vertical_axis_flips():
    """Arma's y grows north and the sheet's grows down. A place at the top of
    the world belongs at the top of the sheet, which is y = 0."""
    doc, _ = _placed()
    _, y = tacmap.from_world(doc, 500, 1000)
    assert y == 0
    _, bottom = tacmap.from_world(doc, 500, 0)
    assert bottom == doc['height']


def test_a_place_outside_a_cropped_sheet_is_left_off():
    doc, places = _placed({'name': 'Far', 'kind': 'namecity', 'x': 99999, 'y': 0})
    assert tacmap.visible_places(doc, places) == []


def test_a_place_inside_the_sheet_is_kept():
    doc, places = _placed({'name': 'Middle', 'kind': 'namecity', 'x': 500, 'y': 500})
    shown = tacmap.visible_places(doc, places)
    assert len(shown) == 1
    assert shown[0]['sheet_x'] == doc['width'] / 2


# --- what is shown --------------------------------------------------------

def test_no_group_filter_means_every_group():
    doc, places = _placed({'name': 'A', 'kind': 'namecity', 'x': 500, 'y': 500},
                          {'name': 'B', 'kind': 'hill', 'x': 500, 'y': 400})
    assert len(tacmap.visible_places(doc, places)) == 2


def test_a_group_filter_keeps_only_that_group():
    doc, places = _placed({'name': 'A', 'kind': 'namecity', 'x': 500, 'y': 500},
                          {'name': 'B', 'kind': 'hill', 'x': 500, 'y': 400})
    doc['places'] = {'show': True, 'groups': ['settlement'], 'scale': 1.0}
    assert [p['name'] for p in tacmap.visible_places(doc, places)] == ['A']


def test_switching_them_off_shows_none():
    doc, places = _placed({'name': 'A', 'kind': 'namecity', 'x': 500, 'y': 500})
    doc['places'] = {'show': False, 'groups': [], 'scale': 1.0}
    assert tacmap.visible_places(doc, places) == []


def test_the_biggest_name_is_drawn_last():
    """Where two collide, the one people navigate by should survive."""
    doc, places = _placed({'name': 'Rock', 'kind': 'rockarea', 'x': 500, 'y': 500},
                          {'name': 'Capital', 'kind': 'namecitycapital', 'x': 500, 'y': 500})
    assert [p['name'] for p in tacmap.visible_places(doc, places)] == ['Rock', 'Capital']


def test_place_counts_are_per_group():
    counts = tacmap.place_counts([{'name': 'A', 'kind': 'namecity', 'x': 1, 'y': 1},
                                  {'name': 'B', 'kind': 'hill', 'x': 1, 'y': 1}])
    assert counts['settlement'] == 1 and counts['terrain'] == 1


# --- rendering ------------------------------------------------------------

def test_places_are_rendered_into_the_map():
    doc, places = _placed({'name': 'Kavala', 'kind': 'namecity', 'x': 500, 'y': 500})
    assert 'Kavala' in tacmap.render(doc, places=places)


def test_a_map_with_no_places_renders_no_group():
    doc, _ = _placed()
    assert 'tm-places' not in tacmap.render(doc, places=[])


def test_places_are_drawn_under_the_plan():
    """A symbol somebody placed must never sit behind a village name."""
    doc, places = _placed({'name': 'Kavala', 'kind': 'namecity', 'x': 500, 'y': 500})
    doc['items'] = [_parse_one(unit(label='Alpha'))]
    svg = tacmap.render(doc, places=places)
    assert svg.index('tm-places') < svg.index('tm-items')


def test_a_place_name_is_escaped():
    """The names arrive from a file somebody pasted in."""
    doc, places = _placed({'name': '<script>x</script>', 'kind': 'namecity',
                           'x': 500, 'y': 500})
    svg = tacmap.render(doc, places=places)
    assert '<script>' not in svg
    assert '&lt;script&gt;' in svg


def test_a_bigger_rank_gets_bigger_type():
    doc, places = _placed({'name': 'Cap', 'kind': 'namecitycapital', 'x': 500, 'y': 500})
    big = tacmap.places_svg(doc, places)
    doc2, small_places = _placed({'name': 'Cap', 'kind': 'rockarea', 'x': 500, 'y': 500})
    small = tacmap.places_svg(doc2, small_places)
    size = lambda svg: float(re.search(r'font-size="([\d.]+)"', svg).group(1))
    assert size(big) > size(small)


def test_the_scale_setting_changes_the_type_size():
    doc, places = _placed({'name': 'Cap', 'kind': 'namecity', 'x': 500, 'y': 500})
    plain = tacmap.places_svg(doc, places)
    doc['places'] = {'show': True, 'groups': [], 'scale': 2.0}
    doubled = tacmap.places_svg(doc, places)
    size = lambda svg: float(re.search(r'font-size="([\d.]+)"', svg).group(1))
    assert size(doubled) > size(plain)


def test_every_name_is_drawn_twice_for_legibility():
    """Pale halo under dark text, the same as everything else that sits on
    terrain here — these land on bright sand and dark jungle alike."""
    doc, places = _placed({'name': 'Kavala', 'kind': 'namecity', 'x': 500, 'y': 500})
    assert tacmap.places_svg(doc, places).count('>Kavala<') == 2


def test_places_settings_survive_a_parse():
    doc = tacmap.parse({'places': {'show': False, 'groups': ['settlement'],
                                   'scale': 1.5}}).doc
    assert doc['places'] == {'show': False, 'groups': ['settlement'], 'scale': 1.5}


def test_an_unknown_group_in_the_settings_is_dropped():
    doc = tacmap.parse({'places': {'groups': ['settlement', 'nonsense']}}).doc
    assert doc['places']['groups'] == ['settlement']


def test_a_document_with_no_places_setting_shows_them():
    """An older map, saved before this existed, should gain the names rather
    than silently keep hiding them."""
    assert tacmap.parse('{}').doc['places']['show'] is True


# --- the dump script ------------------------------------------------------

def test_the_dump_script_uses_sqf_quote_escaping():
    """SQF spells a literal quote as "" and has no backslash escape. A \\" in
    there is a syntax error that costs the whole script."""
    script = tacmap.places_sqf()
    assert '\\' not in script
    assert 'splitString """" joinString' in script


def test_the_dump_script_is_balanced():
    script = tacmap.places_sqf()
    assert script.count('{') == script.count('}')
    assert script.count('[') == script.count(']')


def test_the_dump_script_reads_the_terrain_config():
    script = tacmap.places_sqf()
    assert 'CfgWorlds' in script
    assert 'copyToClipboard' in script


def test_the_dump_script_emits_the_shape_the_parser_reads():
    """The two halves have to agree, and nothing else checks that they do."""
    script = tacmap.places_sqf()
    for field in ('""name""', '""kind""', '""x""', '""y""', '""locations""'):
        assert field in script, field


def test_a_map_on_an_uploaded_terrain_names_it():
    doc = tacmap.blank_doc()
    doc['background'] = {**doc['background'], 'kind': 'tiles', 'url': '/t/7'}
    assert tacmap.terrain_id(doc) == 7


def test_a_map_on_an_ocap_server_has_no_stored_terrain():
    doc = tacmap.blank_doc()
    doc['background'] = {**doc['background'], 'kind': 'tiles',
                         'url': 'https://ocap.example/maps/tanoa'}
    assert tacmap.terrain_id(doc) is None


def test_an_image_background_has_no_terrain():
    doc = tacmap.blank_doc()
    doc['background'] = {**doc['background'], 'kind': 'image', 'url': '/t/7'}
    assert tacmap.terrain_id(doc) is None


def test_a_terrain_url_with_anything_after_it_is_not_matched():
    """`/t/7/../9` must not read as terrain 7."""
    doc = tacmap.blank_doc()
    doc['background'] = {**doc['background'], 'kind': 'tiles', 'url': '/t/7/0/0/0.png'}
    assert tacmap.terrain_id(doc) is None


def test_nothing_offered_is_not_a_malformed_list():
    """An upload that left the paste box empty has no place names and no
    complaint either — the form sends None, not an empty string."""
    assert tacmap.parse_places(None) == ([], [])


def test_an_empty_paste_box_is_silent_too():
    assert tacmap.parse_places('   ') == ([], [])


# ---------------------------------------------------------------------------
# Reading the names out of an OCAP / grad_meh export
# ---------------------------------------------------------------------------
#
# The names really are in the OCAP data, just not in map.json: OCAP builds a
# terrain from a grad_meh export, which writes them beside the tiles as
# geojson/locations/<type>.geojson.gz. These pin the reading of that, because
# the alternative is asking somebody to go into Arma once per terrain.


def _fc(*features):
    return json.dumps({'type': 'FeatureCollection', 'features': list(features)})


def _feature(name, x, y, **properties):
    return {'type': 'Feature',
            'properties': {'name': name, **properties},
            'geometry': {'type': 'Point', 'coordinates': [x, y]}}


def test_a_gzipped_locations_file_is_read():
    """grad_meh gzips each file, and a server may hand it over still gzipped."""
    raw = gzip.compress(_fc(_feature('Kavala', 3500.5, 13300.25)).encode())
    places, _ = tacmap.places_from_geojson(raw, 'namecity')
    assert places == [{'name': 'Kavala', 'kind': 'namecity',
                       'x': 3500.5, 'y': 13300.2}]


def test_a_server_that_decompressed_it_for_us_is_also_read():
    """Content-Encoding may have been unwrapped on the way out. Which happened
    is not worth a branch at the call site."""
    places, _ = tacmap.places_from_geojson(
        _fc(_feature('Kavala', 1, 2)).encode(), 'namecity')
    assert places[0]['name'] == 'Kavala'


def test_a_bare_feature_array_is_read_too():
    """grad_meh's spec says each file holds an array of features; a
    FeatureCollection wrapper is also in the wild."""
    places, _ = tacmap.places_from_geojson(
        json.dumps([_feature('Pyrgos', 1, 2)]), 'namecity')
    assert places[0]['name'] == 'Pyrgos'


def test_the_file_name_is_the_kind():
    """The type is the file name, so nothing has to be guessed from the data."""
    assert tacmap.geojson_kind('geojson/locations/namevillage.geojson.gz') == 'namevillage'
    assert tacmap.geojson_kind('hill.geojson') == 'hill'
    assert tacmap.geojson_kind('NameCityCapital.json.gz') == 'namecitycapital'


def test_an_unknown_type_file_still_lands():
    assert tacmap.geojson_kind('somemodtype.geojson.gz') == tacmap.DEFAULT_PLACE_KIND


def test_unnamed_locations_are_skipped_and_counted():
    places, warnings = tacmap.places_from_geojson(
        _fc(_feature('', 1, 2), _feature('Real', 3, 4)), 'hill')
    assert [p['name'] for p in places] == ['Real']
    assert 'skipped' in warnings[0]


def test_a_feature_with_no_point_is_dropped():
    broken = {'type': 'Feature', 'properties': {'name': 'X'},
              'geometry': {'type': 'Polygon', 'coordinates': [[[1, 2]]]}}
    places, _ = tacmap.places_from_geojson(_fc(broken), 'hill')
    assert places == []


def test_broken_gzip_is_a_message_not_an_exception():
    places, warnings = tacmap.places_from_geojson(b'\x1f\x8b' + b'rubbish', 'hill')
    assert places == []
    assert 'gzip' in warnings[0]


def test_html_instead_of_geojson_is_a_message():
    """A server that answers a 404 page with a 200 must not look like a feed."""
    places, warnings = tacmap.places_from_geojson(b'<html>Not found</html>', 'hill')
    assert places == []
    assert warnings


def test_geojson_without_features_says_so():
    places, warnings = tacmap.places_from_geojson('{"type":"FeatureCollection"}', 'hill')
    assert places == []
    assert 'features' in warnings[0]


def test_merging_drops_the_same_place_named_twice():
    """A village that is also a flat area appears in two files; drawing the
    name twice is bolder and no more informative."""
    places, _ = tacmap.merge_places([
        [{'name': 'Kavala', 'kind': 'namecity', 'x': 100.0, 'y': 200.0}],
        [{'name': 'kavala', 'kind': 'namelocal', 'x': 100.0, 'y': 200.0}],
    ])
    assert len(places) == 1


def test_merging_keeps_two_places_that_share_a_name_elsewhere():
    places, _ = tacmap.merge_places([
        [{'name': 'Mill', 'kind': 'namelocal', 'x': 100.0, 'y': 200.0}],
        [{'name': 'Mill', 'kind': 'namelocal', 'x': 900.0, 'y': 800.0}],
    ])
    assert len(places) == 2


def test_merging_is_capped():
    groups = [[{'name': f'P{n}', 'kind': 'hill', 'x': n, 'y': n}
               for n in range(tacmap.MAX_PLACES + 20)]]
    places, warnings = tacmap.merge_places(groups)
    assert len(places) == tacmap.MAX_PLACES
    assert warnings


def test_every_probe_kind_is_a_known_kind():
    """A probe for a type this table cannot file would fetch and discard."""
    for kind in tacmap.PROBE_KINDS:
        assert kind in tacmap.PLACE_KINDS, kind


# --- the scope ------------------------------------------------------------

def _tiles_doc(url):
    doc = tacmap.blank_doc()
    doc['background'] = {**doc['background'], 'kind': 'tiles', 'url': url}
    return doc


def test_an_uploaded_terrain_scopes_by_its_row():
    assert tacmap.place_scope(_tiles_doc('/t/7')) == 't:7'


def test_an_ocap_terrain_scopes_by_its_world_name():
    """A map on somebody else's OCAP folder has no row here, and the folder's
    world name is the only identity it has."""
    assert tacmap.place_scope(
        _tiles_doc('https://ocap.example/images/maps/tem_cham')) == 'tem_cham'


def test_the_scope_ignores_case_and_a_trailing_slash():
    assert tacmap.place_scope(_tiles_doc('https://ocap.example/maps/Tanoa/')) == 'tanoa'


def test_two_maps_on_the_same_ocap_terrain_share_a_scope():
    """That is the whole point: import once, every plan on it gains them."""
    assert (tacmap.place_scope(_tiles_doc('https://a.example/maps/altis'))
            == tacmap.place_scope(_tiles_doc('https://b.example/images/maps/altis')))


def test_a_map_with_no_terrain_has_no_scope():
    assert tacmap.place_scope(tacmap.blank_doc()) == ''
    assert tacmap.place_scope(_tiles_doc('')) == ''


def test_only_location_files_are_worth_fetching():
    """A grad_meh geojson/ folder also holds roads, houses and objects — the
    whole vector map, tens of megabytes, carrying no names at all. Fetching
    those to find that out is the one way this import costs real bandwidth."""
    assert tacmap.is_location_file('namecity.geojson.gz')
    assert tacmap.is_location_file('locations/hill.geojson')
    assert not tacmap.is_location_file('roads.geojson.gz')
    assert not tacmap.is_location_file('houses.geojson.gz')
    assert not tacmap.is_location_file('objects.geojson.gz')


def test_every_known_kind_is_recognised_as_a_location_file():
    for kind in tacmap.PLACE_KINDS:
        assert tacmap.is_location_file(f'{kind}.geojson.gz'), kind


def test_a_grad_meh_meta_json_is_read_as_places():
    """The public grad_meh export's meta.json is the fallback source when a
    unit's OCAP serves tiles only. Its locations carry a name and a position
    and no type."""
    meta = json.dumps({
        'worldName': 'tem_cham', 'worldSize': 8192,
        'gridOffsetX': 0, 'gridOffsetY': 0,
        'locations': [{'name': 'Chamville', 'pos': [4000.5, 4000.25, 12.0]},
                      {'name': 'Port Cham', 'pos': [1200, 6400, 3.0]}],
    })
    places, warnings = tacmap.parse_places(meta)
    assert [p['name'] for p in places] == ['Chamville', 'Port Cham']
    assert places[0]['x'] == 4000.5
    assert warnings == []


# ---------------------------------------------------------------------------
# Colour: the item's own, not its side's
# ---------------------------------------------------------------------------
#
# A friendly plan used to come out in one flat blue, because a line, an area
# and a task marker all inherited `AFFILIATIONS[side]['fill']`. Only a unit
# does now — its frame is the one thing whose colour carries meaning.

def test_a_line_with_no_colour_is_ink_not_the_side():
    for side in tacmap.AFFILIATIONS:
        item = {'kind': 'line', 'side': side, 'color': ''}
        assert tacmap.item_colour(item) == tacmap.DEFAULT_INK


def test_a_marker_with_no_colour_is_ink_not_the_side():
    for side in tacmap.AFFILIATIONS:
        item = {'kind': 'point', 'side': side, 'color': ''}
        assert tacmap.item_colour(item) == tacmap.DEFAULT_INK


def test_a_unit_still_takes_its_side():
    for side, colours in tacmap.AFFILIATIONS.items():
        item = {'kind': 'unit', 'side': side}
        assert tacmap.item_colour(item) == colours['fill']


def test_a_picked_colour_wins_for_everything_but_a_unit():
    for kind in ('line', 'area', 'point', 'text'):
        item = {'kind': kind, 'side': 'friend', 'color': '#e03b3b'}
        assert tacmap.item_colour(item) == '#e03b3b'


def test_a_marker_keeps_a_colour_through_parse():
    # It was dropped before: `color` was only read for a line, an area and a
    # label, so a marker had no way to be anything but its side's colour.
    result = tacmap.parse({'items': [
        {'kind': 'point', 'x': 50, 'y': 50, 'color': '#f07f2a'},
    ]})
    assert result.doc['items'][0]['color'] == '#f07f2a'


def test_the_backing_pass_goes_the_other_way_from_the_ink():
    assert tacmap.ink_backing('#1b1f24') == tacmap._INK_LIGHT
    assert tacmap.ink_backing('#f5f5f5') == tacmap._INK_DARK
    # Nothing readable to measure falls to the light pass rather than raising.
    assert tacmap.ink_backing('') == tacmap._INK_LIGHT
    assert tacmap.ink_backing('rebeccapurple') == tacmap._INK_LIGHT


# ---------------------------------------------------------------------------
# … and the same colour in Arma
# ---------------------------------------------------------------------------

def test_every_palette_colour_maps_to_a_stock_arma_name():
    for value, _label in tacmap.LINE_COLOURS:
        name = tacmap.arma_colour({'kind': 'line', 'side': 'friend', 'color': value})
        assert name in tacmap.ARMA_COLOURS


def test_the_obvious_palette_colours_map_to_their_own_name():
    wanted = {
        '#e03b3b': 'ColorRed', '#f07f2a': 'ColorOrange', '#f2c832': 'ColorYellow',
        '#3fb950': 'ColorGreen', '#3d8ee8': 'ColorBlue', '#e060b8': 'ColorPink',
        '#f5f5f5': 'ColorWhite', '#1b1f24': 'ColorBlack',
    }
    for value, name in wanted.items():
        item = {'kind': 'line', 'side': 'friend', 'color': value}
        assert tacmap.arma_colour(item) == name


def test_a_side_colour_is_never_reached_by_a_picked_colour():
    # ColorEAST is a dark red and ColorCIV a purple, so without holding them
    # out of the pool an orange line could arrive in Arma claiming a side.
    for value, _label in tacmap.LINE_COLOURS:
        name = tacmap.arma_colour({'kind': 'line', 'side': 'friend', 'color': value})
        assert name not in {colour for _prefix, colour in tacmap.ARMA_SIDES.values()}


def test_a_unit_exports_its_side_whatever_else_is_set():
    for side, (_prefix, colour) in tacmap.ARMA_SIDES.items():
        item = {'kind': 'unit', 'side': side, 'color': '#e03b3b'}
        assert tacmap.arma_colour(item) == colour


def test_an_uncoloured_line_exports_black_rather_than_its_side():
    item = {'kind': 'line', 'side': 'friend', 'color': ''}
    assert tacmap.arma_colour(item) == 'ColorBlack'


def test_a_free_label_is_black_like_arma_marker_text():
    # Arma writes marker text in the marker's colour, black by default — not
    # big white lettering over a halo. The page and the export must agree.
    item = {'kind': 'text', 'side': 'friend', 'color': ''}
    assert tacmap.item_colour(item) == tacmap.DEFAULT_INK
    assert tacmap.arma_colour(item) == 'ColorBlack'


def test_a_free_label_is_drawn_at_marker_text_size():
    doc = tacmap.parse(_doc(items=[{'kind': 'text', 'x': 10, 'y': 10, 'label': 'Jedi',
                                    'size': tacmap.DEFAULT_SIZE}])).doc
    svg = tacmap.render(doc)
    size = float(re.search(r'class="tm-label" font-size="([\d.]+)"', svg).group(1))
    assert size == tacmap.label_size(tacmap.DEFAULT_SIZE)
    assert 'fill="#ffffff"' not in svg.split('class="tm-label"')[1].split('>')[0]


def test_a_line_has_no_outline():
    """Drawn once, in its own colour — the backing pass is gone."""
    doc = _layered(items=[{'kind': 'line', 'style': 'dashed',
                           'points': [[1, 1], [9, 9]]}])
    svg = tacmap.item_svg(doc['items'][0])
    assert svg.count('<polyline') == 1 and 'stroke-dasharray' in svg


def test_dotted_lines_are_round_dots_as_wide_as_the_line():
    assert tacmap.dash_pattern('dotted', 3)[0] < 0.1
    assert tacmap.dash_pattern('solid', 3) is None


# -- shapes: rectangles and ellipses -----------------------------------------

def shape(**overrides):
    item = {'kind': 'shape', 'shape': 'rect', 'x': 500, 'y': 400, 'w': 200,
            'h': 100, 'rotation': 30, 'fill': 'hatch', 'label': 'OBJ GOLD'}
    item.update(overrides)
    return item


def test_a_shape_keeps_its_geometry_and_fill():
    item = _parse_one(shape(shape='ellipse', color='#e03b3b', style='dashed'))
    assert (item['shape'], item['fill'], item['style'], item['color']) == \
        ('ellipse', 'hatch', 'dashed', '#e03b3b')
    assert (item['x'], item['y'], item['w'], item['h'], item['rotation']) == \
        (500, 400, 200, 100, 30)


def test_a_shape_falls_back_to_what_it_can_draw():
    item = _parse_one(shape(shape='triangle', fill='plaid', style='wavy', w=0.1,
                            h=10 ** 9, rotation=9999))
    assert (item['shape'], item['fill'], item['style']) == \
        (tacmap.DEFAULT_SHAPE, tacmap.DEFAULT_FILL, 'solid')
    assert item['w'] == tacmap.MIN_SHAPE
    assert item['h'] == 1000 * 1.5          # never more than half as much again as the sheet
    assert item['rotation'] == 360


def test_a_shape_without_a_position_is_dropped():
    result = tacmap.parse(_doc(items=[shape(x=None)]))
    assert not result.doc['items'] and result.warnings


def test_shapes_are_drawn_with_the_areas_under_everything_else():
    assert tacmap.KIND_ORDER['shape'] == tacmap.KIND_ORDER['area']
    doc = tacmap.parse(_doc(items=[unit(), shape()])).doc
    assert [i['kind'] for i in tacmap.ordered_items(doc)] == ['shape', 'unit']


def test_a_shape_is_drawn_in_its_own_frame_and_turned_into_place():
    svg = tacmap.item_svg(_parse_one(shape()))
    assert 'transform="translate(500.0 400.0) rotate(30.0)"' in svg
    assert '<rect x="-100.0" y="-50.0" width="200.0" height="100.0"' in svg
    assert 'OBJ GOLD' in svg
    ellipse = tacmap.item_svg(_parse_one(shape(shape='ellipse', fill='light')))
    assert '<ellipse cx="0" cy="0" rx="100.0" ry="50.0"' in ellipse
    assert 'fill-opacity="0.22"' in ellipse
    outline = tacmap.item_svg(_parse_one(shape(fill='none')))
    assert 'fill-opacity' not in outline and '<path' not in outline


def test_hatching_stays_inside_the_shape():
    for kind in ('rect', 'ellipse'):
        segments = tacmap.hatch_segments(kind, 200, 100, 10)
        assert len(segments) > 5
        for x1, y1, x2, y2 in segments:
            for x, y in ((x1, y1), (x2, y2)):
                if kind == 'rect':
                    assert abs(x) <= 100.01 and abs(y) <= 50.01
                else:
                    assert abs((x / 100) ** 2 + (y / 50) ** 2 - 1) < 0.01
            assert abs((y2 - y1) - (x2 - x1)) < 0.02         # 45°
    assert tacmap.hatch_segments('rect', 0, 100, 10) == []


def test_hatching_is_drawn_once_in_the_shapes_colour():
    svg = tacmap.item_svg(_parse_one(shape()))
    paths = re.findall(r'<path d="[^"]+" stroke="([^"]+)"', svg)
    assert paths == [tacmap.DEFAULT_INK]


def test_shape_corners_turn_with_the_shape():
    corners = tacmap.shape_corners({'x': 0, 'y': 0, 'w': 200, 'h': 100, 'rotation': 90})
    assert sorted(corners) == sorted([(50.0, -100.0), (50.0, 100.0),
                                      (-50.0, 100.0), (-50.0, -100.0)])


def test_a_shape_exports_as_an_arma_area_marker():
    doc = tacmap.parse(_doc(items=[shape(color='#e03b3b')])).doc
    doc['arma'] = {'terrain': '', 'left': 0, 'bottom': 0, 'right': 8192, 'top': 8192}
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert '_m setMarkerShape "RECTANGLE";' in sqf
    assert '_m setMarkerSize [819.2, 409.6];' in sqf   # half-sizes, in metres
    assert '_m setMarkerDir 30.0;' in sqf
    assert '_m setMarkerBrush "FDiagonal";' in sqf
    assert '_m setMarkerColor "ColorRed";' in sqf
    assert 'createMarker [_p + "1", [4096.0, 4915.2]]' in sqf


def test_every_fill_has_an_arma_brush():
    assert {value['brush'] for value in tacmap.SHAPE_FILLS.values()} == \
        {'Border', 'Solid', 'FDiagonal'}
    for fill in tacmap.SHAPE_FILLS:
        doc = tacmap.parse(_doc(items=[shape(fill=fill, shape='ellipse')])).doc
        sqf = tacmap.to_sqf(doc, prefix='tm7', editable=True)
        assert f'setMarkerBrush "{tacmap.SHAPE_FILLS[fill]["brush"]}"' in sqf
        assert '_m setMarkerShape "ELLIPSE";' in sqf
        assert sqf.count('createMarker') == sqf.count('_n pushBack _m;')


def test_the_editor_is_told_what_shapes_there_are():
    catalog = tacmap.catalog()
    assert [s['key'] for s in catalog['shapes']] == list(tacmap.SHAPES)
    assert [f['key'] for f in catalog['fills']] == list(tacmap.SHAPE_FILLS)
    assert catalog['minShape'] == tacmap.MIN_SHAPE
    assert tacmap.summarise({'items': [shape(), shape()]}) == '2 shapes'


# -- the grey mask and AOs ----------------------------------------------------

def ao_box(**overrides):
    return shape(ao=True, fill='hatch', **overrides)


def test_the_mask_is_off_until_asked_for_and_kept_in_range():
    assert tacmap.blank_doc()['mask'] == {'on': False, 'opacity': 0.55}
    doc = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': 1, 'opacity': 7}}).doc
    assert doc['mask'] == {'on': True, 'opacity': tacmap.MAX_MASK_OPACITY}


def test_ao_is_kept_on_areas_and_shapes_only():
    assert _parse_one(ao_box())['ao'] is True
    area = {'kind': 'area', 'ao': True, 'points': [[0, 0], [10, 0], [10, 10]]}
    assert _parse_one(area)['ao'] is True
    assert 'ao' not in _parse_one(unit(ao=True))


def test_an_ao_is_a_border_with_nothing_in_it():
    svg = tacmap.item_svg(_parse_one(ao_box()))
    assert '<path' not in svg and 'fill-opacity' not in svg      # its hatch is ignored
    area = tacmap.item_svg(_parse_one({'kind': 'area', 'ao': True,
                                       'points': [[0, 0], [10, 0], [10, 10]]}))
    assert 'fill-opacity' not in area


def test_the_mask_cuts_out_every_visible_ao():
    doc = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': True},
                        'layers': [{'id': 'plan', 'name': 'Plan', 'visible': True},
                                   {'id': 'off', 'name': 'Off', 'visible': False}],
                        'items': [ao_box(), ao_box(x=100, layer='off'), shape(x=700)]}).doc
    svg = tacmap.mask_svg(doc)
    assert svg.count('<rect x="-100.0"') == 1          # one hole: the visible AO only
    assert 'mask="url(#tm-aomask-' in svg and 'pointer-events="none"' in svg
    assert tacmap.mask_svg({**doc, 'mask': {'on': False, 'opacity': 0.5}}) == ''


def test_the_mask_sits_between_the_terrain_and_the_plan():
    doc = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': True},
                        'items': [ao_box(), unit()]}).doc
    svg = tacmap.render(doc)
    assert svg.index('id="tm-back"') < svg.index('id="tm-mask"') < svg.index('class="tm-items"')


def test_two_masks_on_one_page_do_not_share_an_id():
    one = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': True}, 'items': [ao_box()]}).doc
    two = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': True},
                        'items': [ao_box(x=100)]}).doc
    ids = [re.search(r'mask id="([^"]+)"', tacmap.mask_svg(d)).group(1) for d in (one, two)]
    assert ids[0] != ids[1]
    assert ids[0] == re.search(r'mask id="([^"]+)"', tacmap.mask_svg(one)).group(1)


def test_an_ao_exports_as_a_border_and_the_mask_is_said_to_stay_behind():
    doc = tacmap.parse({**tacmap.blank_doc(), 'mask': {'on': True},
                        'items': [ao_box()]}).doc
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert '_m setMarkerBrush "Border";' in sqf
    assert 'grey mask outside the AOs is not exported' in sqf


# -- telling AOs apart ---------------------------------------------------------

def test_each_ao_gets_a_colour_no_other_ao_is_using():
    items = [_parse_one(ao_box()), _parse_one(ao_box(x=300)), _parse_one(ao_box(x=600))]
    items[1]['color'] = tacmap.AO_COLOURS[0]                # picked by hand, kept
    assert tacmap.colour_aos(items) == 2
    colours = [i['color'] for i in items]
    assert len(set(colours)) == 3 and colours[1] == tacmap.AO_COLOURS[0]
    assert set(colours) <= set(tacmap.AO_COLOURS)
    assert tacmap.colour_aos(items) == 0                    # nothing left to colour


def test_a_line_in_an_ao_colour_does_not_use_it_up_and_a_full_palette_wraps():
    line = _parse_one({'kind': 'line', 'color': tacmap.AO_COLOURS[0],
                       'points': [[0, 0], [10, 10]]})
    assert tacmap.next_ao_colour([line]) == tacmap.AO_COLOURS[0]
    many = [_parse_one(ao_box(x=10 * n, color=c)) for n, c in enumerate(tacmap.AO_COLOURS)]
    assert tacmap.next_ao_colour(many) in tacmap.AO_COLOURS
    assert tacmap.catalog()['aoColours'] == list(tacmap.AO_COLOURS)


def test_an_ao_name_hangs_in_a_tab_at_its_top_left_corner():
    box = _parse_one(ao_box(label='North', color='#3d8ee8', rotation=0))
    assert tacmap.ao_corner(box) == (box['x'] - box['w'] / 2, box['y'] - box['h'] / 2)
    svg = tacmap.item_svg(box)
    assert 'class="tm-aotab"' in svg and 'fill="#3d8ee8"' in svg and '>North</text>' in svg
    assert 'text-anchor="middle"' not in svg                # not in the centre any more
    area = _parse_one({'kind': 'area', 'ao': True, 'label': 'South',
                       'points': [[50, 40], [90, 10], [120, 80], [30, 90]]})
    assert tacmap.ao_corner(area) == (50, 40)
    assert tacmap.ao_tab(_parse_one(ao_box(label=''))) is None


def test_overlapping_aos_put_their_names_in_different_places():
    one = _parse_one(ao_box(label='North'))
    two = _parse_one(ao_box(label='South', x=one['x'] + 60, y=one['y'] + 40))
    assert tacmap.ao_corner(one) != tacmap.ao_corner(two)


def test_a_tab_reads_on_a_light_colour_and_a_dark_one():
    light = tacmap.item_svg(_parse_one(ao_box(label='A', color='#f2c832')))
    dark = tacmap.item_svg(_parse_one(ao_box(label='A', color='#1b1f24')))
    text = re.compile(r'<text[^>]*fill="([^"]+)"')
    assert text.search(light).group(1) != text.search(dark).group(1)


def test_a_turned_ao_hangs_its_tab_on_the_corner_now_nearest_the_top_left():
    turned = _parse_one(ao_box(label='North'))              # turned 30°
    assert tacmap.ao_corner(turned) in tacmap.shape_corners(turned)
    assert tacmap.ao_corner(turned) == min(tacmap.shape_corners(turned),
                                           key=lambda p: p[0] + p[1])


def test_an_elliptical_ao_hangs_its_tab_on_the_ellipse_itself():
    for turn in (0, 30, 75):
        oval = _parse_one(ao_box(shape='ellipse', label='South', rotation=turn))
        x, y = tacmap.ao_corner(oval)
        a, b, t = oval['w'] / 2, oval['h'] / 2, math.radians(turn)
        lx = (x - oval['x']) * math.cos(t) + (y - oval['y']) * math.sin(t)
        ly = -(x - oval['x']) * math.sin(t) + (y - oval['y']) * math.cos(t)
        assert abs(lx * lx / (a * a) + ly * ly / (b * b) - 1) < 0.01     # on the outline
        assert x + y < oval['x'] + oval['y']                                # towards top left
    upright = _parse_one(ao_box(shape='ellipse', rotation=0, w=200, h=100))
    assert tacmap.ao_corner(upright) == (round(500 - 200 ** 2 / 4 / math.hypot(100, 50), 2),
                                         round(400 - 50 ** 2 / math.hypot(100, 50), 2))


def test_the_first_six_ao_colours_stay_distinct_in_arma():
    names = [tacmap.arma_colour({'kind': 'shape', 'color': c, 'side': 'friendly'})
             for c in tacmap.AO_COLOURS[:6]]
    assert len(set(names)) == 6


# -- dashed lines and arrows in Arma ----------------------------------------
#
# Arma draws every line marker solid and at one width (checked in game: brush
# and size change nothing on a polyline), so a dashed line goes across as its
# dashes, one short line marker each.

def _arma_doc(*items):
    doc = tacmap.parse(_doc(items=list(items))).doc
    doc['arma'] = {'terrain': '', 'left': 0, 'bottom': 0, 'right': 1000, 'top': 1000}
    return doc


def test_dash_runs_cut_a_line_into_its_dashes():
    runs = tacmap.dash_runs([(0, 0), (100, 0)], 'dashed')
    on, off = tacmap.EXPORT_DASH['dashed']
    assert len(runs) == 5
    assert runs[0] == [(0, 0), (on, 0)]
    assert runs[1][0] == (on + off, 0)
    # Round a corner, a dash bends with the line rather than cutting it.
    bent = tacmap.dash_runs([(0, 0), (10, 0), (10, 10)], 'dashed')
    assert bent[0] == [(0, 0), (10, 0), (10, 4.0)]
    assert tacmap.dash_runs([(0, 0), (5, 5)], 'solid') == [[(0, 0), (5, 5)]]


def test_a_long_dashed_line_never_costs_more_than_the_cap():
    runs = tacmap.dash_runs([(0, 0), (100000, 0)], 'dotted')
    assert len(runs) <= tacmap.MAX_DASHES + 1


def test_a_dashed_line_exports_as_many_line_markers():
    doc = _arma_doc({'kind': 'line', 'style': 'dashed', 'points': [[0, 500], [100, 500]]})
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert sqf.count('setMarkerShape "POLYLINE"') == 5
    assert sqf.count('createMarker') == 5


def test_arrows_go_on_whichever_ends_were_asked_for():
    doc = _arma_doc({'kind': 'line', 'arrow': 'both', 'points': [[0, 500], [100, 500]]})
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert sqf.count('"mil_arrow"') == 2
    assert 'setMarkerDir 90' in sqf and 'setMarkerDir 270' in sqf


def test_a_dashed_shape_keeps_its_fill_and_dashes_its_outline():
    doc = _arma_doc(shape(style='dashed', fill='light', rotation=0))
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert sqf.count('POLYLINE') > 4
    assert '_m setMarkerShape "RECTANGLE";' in sqf
    outline_only = tacmap.to_sqf(_arma_doc(shape(style='dashed', fill='none')), prefix='tm7')
    assert 'RECTANGLE' not in outline_only


def test_every_dash_is_remembered_by_an_editable_export():
    doc = _arma_doc({'kind': 'line', 'style': 'dotted', 'arrow': 'end',
                     'points': [[0, 500], [100, 500]]})
    sqf = tacmap.to_sqf(doc, prefix='tm7', editable=True)
    assert sqf.count('createMarker') == sqf.count('_n pushBack _m;')


# -- measuring --------------------------------------------------------------

def test_lengths_and_grid_references_come_from_the_arma_corners():
    doc = _arma_doc()
    doc['arma'] = {'terrain': '', 'left': 0, 'bottom': 0, 'right': 8192, 'top': 8192}
    # 1000 sheet units across an 8192 m terrain
    assert tacmap.length_m(doc, [(0, 0), (100, 0)]) == 819.2
    assert tacmap.length_m(doc, [(0, 0), (100, 0), (100, 100)], closed=True) == \
        round(819.2 * 2 + math.hypot(819.2, 819.2), 1)
    # A point 450 m east and 1120 m north of the south-west corner
    x, y = 450 / 8.192, 1000 - 1120 / 8.192
    assert tacmap.grid_ref(doc, x, y) == '004011'
    assert tacmap.grid_ref(doc, x, y, digits=8) == '00450112'   # 10 m squares


# -- phase lines: names at the ends ------------------------------------------

def test_a_phase_line_is_named_at_both_ends():
    item = _parse_one({'kind': 'line', 'label': 'PL AMBER', 'label_at': 'ends',
                       'points': [[100, 500], [900, 500]]})
    spots = tacmap.line_label_spots(item)
    assert [s[2] for s in spots] == ['end', 'start']       # each reads away from the line
    assert spots[0][0] < 100 < 900 < spots[1][0]
    assert tacmap.item_svg(item).count('PL AMBER') == 2
    assert _parse_one({'kind': 'line', 'label_at': 'sideways',
                       'points': [[1, 1], [2, 2]]})['label_at'] == 'middle'


def test_a_line_name_rides_on_its_own_marker_in_arma():
    doc = _arma_doc({'kind': 'line', 'label': 'PL AMBER', 'label_at': 'ends',
                     'points': [[100, 500], [900, 500]]})
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    assert sqf.count('setMarkerText "PL AMBER"') == 2
    assert sqf.count(f'"{tacmap.LINE_LABEL_MARKER}"') == 2
    editable = tacmap.to_sqf(doc, prefix='tm7', editable=True)
    assert editable.count('createMarker') == editable.count('_n pushBack _m;')


# -- back from Arma ----------------------------------------------------------

def test_sqf_arrays_read_back_with_their_doubled_quotes():
    assert tacmap.parse_sqf_value('[["a", 1, true, [2.5, -3]], "say ""hi"""]') == \
        [['a', 1.0, True, [2.5, -3.0]], 'say "hi"']
    with pytest.raises(tacmap.SQFError):
        tacmap.parse_sqf_value('[1, 2')


def _dump(*rows):
    return '[' + ','.join(rows) + ']'


def test_a_mission_s_markers_come_back_as_the_map_s_own():
    doc = _arma_doc()
    doc['arma'] = {'terrain': '', 'left': 0, 'bottom': 0, 'right': 1000, 'top': 1000}
    text = _dump(
        '["_USER_DEFINED #2/0/1","ICON","mil_objective",[500,500],"OBJ GOLD","ColorRed",[1,1],0,"Solid",[]]',
        '["m2","ICON","b_inf",[100,900],"1-1","ColorWEST",[1,1],45,"Solid",[]]',
        '["m3","ICON","o_hq",[200,800],"","ColorEAST",[1,1],0,"Solid",[]]',
        '["m4","POLYLINE","",[0,0],"PL AMBER","ColorBlack",[1,1],0,"Solid",[100,100,900,100]]',
        '["m5","POLYLINE","",[0,0],"","Default",[1,1],0,"Solid",[100,100,200,100,200,200,100,100]]',
        '["m6","RECTANGLE","",[500,500],"","ColorGreen",[50,25],30,"FDiagonal",[]]',
        '["m7","ICON","rhs_flag_vdv",[300,300],"","Default",[1,1],0,"Solid",[]]',
        '["m9","ICON","loc_Church",[320,300],"","Default",[1,1],0,"Solid",[]]',
        '["m8","ICON","mil_dot",[5000,5000],"far","Default",[1,1],0,"Solid",[]]',
    )
    items, notes = tacmap.import_arma_markers(doc, text)
    kinds = [i['kind'] for i in items]
    assert kinds == ['point', 'unit', 'unit', 'line', 'area', 'shape', 'point', 'point']
    assert items[0]['marker'] == 'mil_objective' and items[0]['color'] == '#e03b3b'
    assert (items[1]['side'], items[1]['symbol'], items[1]['rotation']) == ('friend', 'inf', 45.0)
    assert (items[2]['side'], items[2]['symbol']) == ('hostile', 'hq')
    assert items[3]['points'] == [[100.0, 900.0], [900.0, 900.0]]
    assert len(items[4]['points']) == 3                      # closed polyline → area
    assert (items[5]['w'], items[5]['h'], items[5]['fill']) == (100.0, 50.0, 'hatch')
    assert items[6]['label'] == 'rhs_flag_vdv'                # a mod's: a named dot
    assert items[7]['marker'] == 'loc_church'                 # every vanilla marker is ours
    assert any('outside' in n for n in notes)
    only, _ = tacmap.import_arma_markers(doc, text, players_only=True)
    assert len(only) == 1
    # What comes back parses as a document, unchanged in substance.
    doc['items'] = items
    assert tacmap.parse(doc).ok


def test_a_round_trip_through_arma_keeps_the_plan():
    doc = _arma_doc({'kind': 'unit', 'symbol': 'armor', 'side': 'hostile', 'x': 400, 'y': 300},
                    {'kind': 'line', 'points': [[100, 100], [300, 200]], 'color': '#3d8ee8'})
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    # What the dump would say about those markers, built from the script.
    assert '"o_armor"' in sqf and 'ColorBlue' in sqf


def test_the_export_report_says_what_does_not_survive():
    doc = _arma_doc({'kind': 'line', 'style': 'dashed', 'weight': 4,
                     'points': [[0, 0], [100, 0]]},
                    {'kind': 'area', 'points': [[0, 0], [9, 0], [9, 9]]})
    sqf = tacmap.to_sqf(doc, prefix='tm7')
    report = tacmap.export_report(doc, sqf)
    assert report['markers'] == sqf.count('createMarker')
    text = ' '.join(report['notes'])
    assert 'dashes' in text and 'thickness' in text and 'outlines' in text


# -- the full marker set -----------------------------------------------------
#
# Read out of a running game's CfgMarkers: every class a unit or a marker may
# export as has to be one of these, or the mission draws nothing for it.

VANILLA_UNIT_MARKERS = {
    f'{side}_{kind}' for side in 'bon' for kind in (
        'unknown', 'inf', 'motor_inf', 'mech_inf', 'armor', 'recon', 'air', 'plane', 'uav',
        'naval', 'med', 'art', 'mortar', 'hq', 'support', 'maint', 'service', 'installation',
        'antiair', 'Ordnance')
} | {'c_unknown', 'c_car', 'c_ship', 'c_air', 'c_plane', 'u_installation'}


def test_every_marker_has_a_drawing_a_group_and_its_own_class():
    names = [m['arma'] for m in tacmap.MARKERS.values()]
    assert len(names) == len(set(names))
    for key, marker in tacmap.MARKERS.items():
        assert marker['icon'] and marker['group'] in tacmap.MARKER_GROUPS, key
    # The whole of vanilla's Military, hand-drawn and Zeus sets.
    for name in ('mil_ambush', 'mil_arrow2', 'hd_join', 'loc_PowerWind', 'group_11',
                 'respawn_para', 'MinefieldAP', 'flag_UK', 'RedCrystal'):
        assert name in names


def test_a_locked_layer_stays_locked_and_others_carry_nothing_new():
    doc = tacmap.parse({'layers': [{'id': 'plan', 'name': 'Plan', 'visible': True},
                                   {'id': 'enemy', 'name': 'Enemy', 'visible': True,
                                    'locked': True}], 'items': []}).doc
    assert doc['layers'][1]['locked'] is True
    assert 'locked' not in doc['layers'][0]


# -- the game's own marker pictures -----------------------------------------
#
# Made-up PNG bytes here: the real pictures are Bohemia's and never go in the
# repository.

def _icon_zip(files):
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return out.getvalue()


PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20


# -- drawn from the game's own pictures ---------------------------------------

def test_every_unit_exports_as_a_marker_arma_has():
    for side in tacmap.AFFILIATIONS:
        for symbol in tacmap.SYMBOLS:
            item = {'kind': 'unit', 'side': side, 'symbol': symbol}
            assert tacmap._arma_type(item) in VANILLA_UNIT_MARKERS, item


def test_every_marker_and_unit_knows_where_the_game_keeps_its_picture():
    for key, marker in tacmap.MARKERS.items():
        assert key == marker['arma'].lower() and marker['icon'].lower().startswith('\\a3\\')
    assert set(tacmap.UNIT_CLASSES) >= {name.lower() for name in VANILLA_UNIT_MARKERS}
    for echelon in tacmap.ECHELONS.values():
        assert echelon['arma'].lower() in tacmap.MARKERS


def test_an_old_map_reads_as_the_arma_markers_it_always_exported_as():
    items = tacmap.parse(_doc(items=[
        unit(symbol='mech', dimension='land'),
        unit(symbol='inf', hq=True, dimension='land'),
        unit(symbol='air', dimension='land'),               # APP-6 fixed wing
        unit(symbol='inf', dimension='air'),
        unit(symbol='truck', side='civ', dimension='land'),
        {'kind': 'point', 'x': 1, 'y': 1, 'marker': 'objective'},
        {'kind': 'point', 'x': 1, 'y': 1, 'marker': 'loc_church'},
        {'kind': 'point', 'x': 1, 'y': 1, 'glyph': 'obj'},
    ])).doc['items']
    assert [i.get('symbol') for i in items[:5]] == ['mech_inf', 'hq', 'plane', 'heli', 'car']
    assert [tacmap._arma_type(i) for i in items[:5]] == ['b_mech_inf', 'b_hq', 'b_plane',
                                                         'b_air', 'c_car']
    assert [i['marker'] for i in items[5:]] == ['mil_objective', 'loc_church', 'mil_objective']
    # Parsing again changes nothing.
    again = tacmap.parse(_doc(items=items)).doc['items']
    assert [i.get('symbol') or i.get('marker') for i in again] == \
        [i.get('symbol') or i.get('marker') for i in items]


def test_an_unknown_symbol_is_drawn_as_unknown():
    result = tacmap.parse(_doc(items=[unit(symbol='deathstar')]))
    assert result.doc['items'][0]['symbol'] == 'unknown'
    assert result.warnings


def test_the_catalog_says_which_picture_each_symbol_is_on_every_side():
    catalog = tacmap.catalog()
    assert [entry['key'] for entry in catalog['symbols']] == list(tacmap.SYMBOLS)
    inf = next(entry for entry in catalog['symbols'] if entry['key'] == 'inf')
    assert inf['classes'] == {'friend': 'b_inf', 'hostile': 'o_inf', 'neutral': 'n_inf',
                              'civ': 'c_unknown', 'unknown': 'n_inf'}
    assert next(e for e in catalog['echelons'] if e['key'] == 'platoon')['picture'] == 'group_3'
    assert '?' in catalog['missingPicture']


def test_a_marker_is_drawn_from_its_picture_in_its_colour():
    doc = tacmap.parse(_doc(items=[
        {'kind': 'point', 'marker': 'mil_dot', 'x': 10, 'y': 10, 'color': '#e03b3b'},
        {'kind': 'point', 'marker': 'mil_box', 'x': 20, 'y': 20}])).doc
    svg = tacmap.render(doc, marker_images={'mil_dot': '/g/1/mi/mil_dot.png'})
    assert '<image id="tmk-mil_dot" href="/g/1/mi/mil_dot.png"' in svg
    # Tinted the way Arma does it: the picture multiplied by the colour.
    assert '<use href="#tmk-mil_dot" filter="url(#tmt-e03b3b)"/>' in svg
    assert '<filter id="tmt-e03b3b"' in svg and 'values="0.8784 0 0 0 0 0 0.2314' in svg
    assert svg.count('stroke-dasharray="8 6"') == 1          # the box has no picture
    assert '<image' not in tacmap.render(doc)


def test_a_unit_is_its_sides_marker_with_its_size_stacked_on_it():
    item = _parse_one(unit(side='hostile', symbol='armor', echelon='platoon'))
    svg = tacmap.item_svg(item, icons={'o_armor', 'group_3'})
    colour = tacmap.AFFILIATIONS['hostile']['fill']
    tint = colour.lstrip('#')
    assert f'<use href="#tmk-o_armor" filter="url(#tmt-{tint})"/>' in svg
    assert f'<use href="#tmk-group_3" filter="url(#tmt-{tint})"/>' in svg
    script = tacmap.to_sqf(tacmap.parse(_doc(items=[item])).doc, prefix='m1')
    assert 'setMarkerType "o_armor"' in script and 'setMarkerType "group_3"' in script


def test_a_size_marker_on_a_unit_comes_back_as_its_size():
    doc = _arma_doc()
    doc['arma'] = {'terrain': '', 'left': 0, 'bottom': 0, 'right': 1000, 'top': 1000}
    items, _ = tacmap.import_arma_markers(doc, _dump(
        '["m1","ICON","b_inf",[100,900],"1 Plt","ColorWEST",[1,1],0,"Solid",[]]',
        '["m2","ICON","group_3",[100,900],"","ColorWEST",[1,1],0,"Solid",[]]',
        '["m3","ICON","group_4",[500,500],"","ColorWEST",[1,1],0,"Solid",[]]'))
    assert [(i['kind'], i.get('echelon'), i.get('marker')) for i in items] == \
        [('unit', 'platoon', None), ('point', None, 'group_4')]


def test_a_flag_is_its_own_colours_in_both_places():
    item = _parse_one({'kind': 'point', 'marker': 'flag_uk', 'x': 10, 'y': 10,
                       'color': '#e03b3b'})
    svg = tacmap.item_svg(item, icons={'flag_uk'})
    assert '<use href="#tmk-flag_uk"/>' in svg and 'filter=' not in svg
    assert tacmap.arma_colour(item) == 'Default'
    assert '<image id="tmk-flag_uk"' in tacmap.defs({'flag_uk': '/x.png'})


def test_marker_pictures_are_matched_by_where_the_game_keeps_them():
    icons, notes = tacmap.read_marker_icons(_icon_zip({
        'military/dot_ca.png': PNG, 'handdrawn/flag_ca.png': PNG,
        'nato/b_inf.png': PNG, 'a3/ui_f/data/map/mapcontrol/Church_CA.png': PNG,
        'loc_Bunker.png': PNG, 'flags/UK_ca.png': PNG, 'group_3.png': PNG,
        'military/nonsense_ca.png': PNG, 'military/box_ca.png': b'not a png',
        'readme.txt': b'hi',
    }))
    assert sorted(icons) == ['b_inf', 'flag_uk', 'group_3', 'hd_flag', 'loc_bunker',
                             'loc_church', 'mil_dot']
    assert any('left out' in note for note in notes)
    with pytest.raises(ValueError):
        tacmap.read_marker_icons(b'not a zip')


def test_the_coverage_lists_what_is_still_to_upload():
    rows = {row['group']: row for row in tacmap.picture_coverage({'mil_dot', 'b_inf'})}
    assert rows['Military']['have'] == 1 and rows['NATO units']['have'] == 1
    assert sum(row['total'] for row in rows.values()) == \
        len(tacmap.MARKERS) + len(tacmap.UNIT_CLASSES)
    assert any(path.endswith('objective_CA.paa') for path in rows['Military']['missing'])



def test_names_are_written_the_way_arma_writes_marker_text():
    doc = tacmap.parse(_doc(items=[
        {'kind': 'point', 'marker': 'mil_dot', 'x': 100, 'y': 100, 'label': 'OBJ GOLD',
         'color': '#e03b3b'},
        unit(label='1 Plt', side='hostile'),
        {'kind': 'line', 'points': [[0, 0], [100, 0]], 'label': 'PL AMBER',
         'label_at': 'ends', 'color': '#3d8ee8'}])).doc
    svg = tacmap.render(doc)
    # Beside the marker, in its colour, in a narrow face with a thin shadow.
    assert 'text-anchor="start"' in svg and 'fill="#e03b3b"' in svg and '>OBJ GOLD<' in svg
    assert f'fill="{tacmap.AFFILIATIONS["hostile"]["fill"]}"' in svg
    assert svg.count('fill="#3d8ee8"') >= 2 and 'Purista' in svg


def test_the_text_scale_sizes_every_name_and_is_kept_in_bounds():
    small = tacmap.parse(_doc(items=[unit(label='A', size=1.0)])).doc
    big = dict(small, text_scale=2.0)
    assert 'font-size="14.0"' in tacmap.render(small)
    assert 'font-size="28.0"' in tacmap.render(big)
    assert tacmap.parse(dict(_doc(), text_scale=99)).doc['text_scale'] == tacmap.MAX_TEXT_SCALE
    assert tacmap.parse(_doc()).doc['text_scale'] == tacmap.DEFAULT_TEXT_SCALE


def test_a_marker_turns_on_the_map_and_in_arma():
    item = _parse_one({'kind': 'point', 'marker': 'mil_arrow', 'x': 10, 'y': 10,
                       'rotation': 90})
    assert 'rotate(90.0)' in tacmap.item_svg(item, icons={'mil_arrow'})
    script = tacmap.to_sqf(tacmap.parse(_doc(items=[item])).doc, prefix='m1')
    assert 'setMarkerDir 90.0' in script


# -- how big a save may be --------------------------------------------------

def _maxed_document(kind):
    """The largest document `parse()` will accept, in one item kind.

    Every slot of every cap used: 400 items, each a full-length label on the
    deepest layer, and for the two kinds that carry geometry a freehand line at
    the point cap.
    """
    items = []
    for i in range(tacmap.MAX_ITEMS):
        item = {'layer': 'L%d' % (i % tacmap.MAX_LAYERS),
                'label': 'W' * tacmap.MAX_LABEL, 'color': '#ff8800'}
        if kind in ('line', 'area'):
            item.update(kind=kind, points=[
                {'x': round(1000 + j * 0.37, 2), 'y': round(900 - j * 0.41, 2)}
                for j in range(tacmap.MAX_POINTS)])
            item.update(weight=6, style='dashed', arrow='both') if kind == 'line' \
                else item.update(fill='hatch', ao=True)
        else:
            item.update(kind='unit', x=1234.56, y=789.01, side='friendly',
                        symbol='infantry', echelon='platoon', size=1.0, rotation=45)
        items.append(item)
    return {
        'layers': [{'id': 'L%d' % n, 'name': 'L' * tacmap.MAX_LAYER_NAME,
                    'visible': True} for n in range(tacmap.MAX_LAYERS)],
        'background': {'kind': 'tiles', 'zoom': 4,
                       'url': 'https://ocap.example.com/maps/tem_cham'},
        'arma': {'x1': 0, 'y1': 0, 'x2': 8192, 'y2': 8192},
        'items': items,
    }


def test_the_body_cap_clears_the_largest_document_the_parser_accepts():
    """`MAX_DOC_BYTES` refuses a save unread, so it has to sit above anything
    the editor can legitimately produce — otherwise it refuses real work.

    This is the check that was missing the first time the number was chosen:
    it was set from a guess at a third of the true ceiling, which would have
    turned a full map into "that map is too large to save".
    """
    for kind in ('line', 'area', 'unit'):
        doc = _maxed_document(kind)
        assert len(tacmap.parse(doc).doc['items']) == tacmap.MAX_ITEMS, kind
        size = len(json.dumps(doc).encode())
        assert size < tacmap.MAX_DOC_BYTES, (
            '%s: a maxed-out document is %.2f MB, over the %.2f MB cap'
            % (kind, size / 1048576, tacmap.MAX_DOC_BYTES / 1048576))


def test_the_body_cap_still_bounds_what_one_request_can_spend():
    """It is a cap, not a formality: the website runs inside the bot, so the
    body is buffered in the process holding the Discord connection."""
    biggest = max(len(json.dumps(_maxed_document(k)).encode())
                  for k in ('line', 'area', 'unit'))
    assert tacmap.MAX_DOC_BYTES < 16 * 1024 * 1024
    assert tacmap.MAX_DOC_BYTES >= 2 * biggest, 'leave room, but not a tenfold margin'
