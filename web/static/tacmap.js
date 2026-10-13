/* The tactical map editor.
 *
 * The rest of this site has no JavaScript on purpose. A map is the one thing
 * that cannot be a form: placing a symbol on terrain and dragging it where it
 * belongs is the feature, and a page of numeric coordinate fields would be
 * worse than the paper map it replaces. So this file exists — vanilla, no
 * build step, no dependency, served from the bot's own container like
 * everything else.
 *
 * What it is NOT is a second renderer. Every frame, icon and arrowhead is
 * defined once by `utils/tacmap.py` and emitted into the page's <defs>; the
 * composition below only places <use> elements at coordinates, and mirrors
 * `item_svg()` in that module element for element. If one side changes, the
 * other has to change with it — the map drawn here and the map rendered for
 * somebody with scripting off are meant to be the same map.
 *
 * With scripting off the server-rendered SVG is still there and still readable.
 * That is the fallback, and it is why this file replaces the drawing layer
 * rather than building the whole map from nothing.
 */
(function () {
  'use strict';

  var app = document.getElementById('tmapp');
  if (!app) return;

  var svg = app.querySelector('svg.tacmap');
  var docNode = document.getElementById('tmdoc');
  var catalogNode = document.getElementById('tmcatalog');
  if (!svg || !docNode || !catalogNode) return;

  var catalog = JSON.parse(catalogNode.textContent);
  // The terrain's own town names. They belong to the terrain rather than to
  // this document, so they arrive separately and are never saved back — the
  // document only carries how much of them to show.
  var placesNode = document.getElementById('tmplaces');
  var places = placesNode ? JSON.parse(placesNode.textContent) : [];
  var state = {
    doc: JSON.parse(docNode.textContent),
    tool: 'select',
    side: 'friend',
    symbol: 'inf',
    // No text on a new marker until somebody picks some.
    glyph: '',
    marker: catalog.defaultMarker,
    echelon: '',
    layer: '',
    // What a line, an area or a marker is painted when it is drawn.
    // '' means the default ink — it used to mean the side's colour, which
    // is what made every friendly plan come out one flat blue.
    color: '',
    style: 'solid',
    // How thick a new line is, in screen pixels.
    weight: catalog.defaultWeight,
    // How a new rectangle or ellipse is filled.
    fill: catalog.defaultFill,
    // What the AO tool draws: freehand, a rectangle or an ellipse.
    aoForm: 'rect',
    // A rectangle or ellipse being dragged out, and a selected one being
    // resized or turned by its handles.
    box: null,
    handle: null,
    selected: -1,
    draft: null,
    drag: null,
    pan: null,
    undo: [],
    dirty: false
  };
  var editable = app.dataset.editable === '1';
  // The game's own pictures this server has uploaded, by picture key (see
  // icon_masks() in utils/tacmap.py). Only these markers and units are
  // offered; anything else on a map is drawn as a placeholder.
  var iconKeys = {};
  try {
    JSON.parse(app.dataset.icons || '[]').forEach(function (key) { iconKeys[key] = true; });
  } catch (e) { /* none */ }

  var colours = {};
  catalog.sides.forEach(function (side) { colours[side.key] = side; });
  var symbols = {};
  catalog.symbols.forEach(function (symbol) { symbols[symbol.key] = symbol; });
  var markers = {};
  catalog.markers.forEach(function (marker) { markers[marker.key] = marker; });
  var echelons = {};
  catalog.echelons.forEach(function (entry) { echelons[entry.key] = entry; });

  function unitPicture(item) {
    // Mirrors unit_picture() in utils/tacmap.py.
    var symbol = symbols[item.symbol] || symbols.inf;
    return symbol.classes[item.side] || symbol.classes.friend;
  }

  var layer = svg.querySelector('.tm-items');
  var back = svg.querySelector('#tm-back');
  // The grey cover outside the AOs. The server renders it between the sheet
  // and the plan; a page from before it existed gets the group added here.
  var maskLayer = svg.querySelector('#tm-mask');
  if (!maskLayer) {
    maskLayer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    maskLayer.setAttribute('id', 'tm-mask');
    layer.parentNode.insertBefore(maskLayer, layer);
  }
  var overlay = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  overlay.setAttribute('class', 'tm-overlay');
  svg.appendChild(overlay);

  var view = readViewBox();

  /* -- drawing, mirroring utils/tacmap.py ---------------------------------- */

  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function attr(value) { return esc(value).replace(/"/g, '&quot;'); }

  function round(value) { return Math.round(value * 100) / 100; }

  function labelSize(size) {
    return Math.round(Math.max(catalog.minLabel, 14 * size) * 10) / 10;
  }

  // The mirror of tacmap.line_size(): a line may be drawn as small as the
  // slider goes, but what says "this is a line" has to survive it. Width,
  // dashes and arrow head all read this, so they stay in proportion.
  function lineSize(size) {
    return Math.max(size, catalog.minLineSize);
  }

  // The map's one text size, as Arma has one size for marker text.
  function textScale() { return Number(state.doc.text_scale) || catalog.textScale['default']; }

  function fontSize(size) {
    return Math.round(labelSize(size) * textScale() * 10) / 10;
  }

  function label(text, x, y, size, fill, anchor) {
    // Mirrors _label_svg() in utils/tacmap.py: Arma's way of writing marker
    // text — a narrow face in the marker's colour, a thin opposite shadow.
    if (!text) return '';
    var font = fontSize(size);
    var colour = fill || catalog.defaultInk;
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="' +
      (anchor || 'middle') + '" class="tm-label" font-size="' + font + '"' +
      ' font-family="' + attr(catalog.armaFont) + '" font-weight="600"' +
      ' fill="' + colour + '" stroke="' + inkBacking(colour) + '"' +
      ' stroke-opacity="0.75" stroke-width="' + round(font * 0.14) + '"' +
      ' paint-order="stroke" stroke-linejoin="round">' + esc(text) + '</text>';
  }

  function itemColour(item) {
    // Mirrors item_colour() in utils/tacmap.py. Only a unit takes its side's
    // colour — that is how the frame says whose it is. A line, an area or a
    // task marker takes the colour somebody picked, and the default ink when
    // nobody did, so a friendly plan is not one flat blue.
    if (item.kind === 'unit') return colours[item.side].fill;
    // A free label is chrome, not a control measure — white over its halo.
    if (item.kind === 'text') return item.color || '#ffffff';
    return item.color || catalog.defaultInk;
  }

  function inkBacking(colour) {
    // Mirrors ink_backing(): the pass drawn under the ink so it holds up over
    // bright sand and dark jungle alike.
    var rgb = /^#([0-9a-f]{6})$/i.exec((colour || '').trim());
    if (!rgb) return catalog.inkLight;
    var value = parseInt(rgb[1], 16);
    var luma = (0.299 * ((value >> 16) & 255) + 0.587 * ((value >> 8) & 255) +
      0.114 * (value & 255)) / 255;
    return luma > 0.5 ? catalog.inkDark : catalog.inkLight;
  }

  var FONT = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif';

  // Mirrors next_ao_colour(): the first AO colour no other AO on the map is
  // drawn in, so overlapping AOs never share a border.
  function nextAOColour(skip) {
    var used = {};
    state.doc.items.forEach(function (entry) {
      if (entry !== skip && entry.ao && (entry.kind === 'area' || entry.kind === 'shape')) {
        used[itemColour(entry).toLowerCase()] = true;
      }
    });
    var list = catalog.aoColours || [];
    for (var i = 0; i < list.length; i++) {
      if (!used[list[i]]) return list[i];
    }
    return list.length ? list[Object.keys(used).length % list.length] : '';
  }

  // Mirrors ao_corner() and ao_tab(): the AO's name in a tab of its own
  // colour, hung inside the corner nearest the sheet's top left. The width is
  // estimated from the character count, as the server does, so both draw
  // the same box.
  function aoCorner(item) {
    var points;
    if (item.kind === 'shape') {
      var a = item.w / 2;
      var b = item.h / 2;
      var turn = (item.rotation || 0) * Math.PI / 180;
      var cos = Math.cos(turn);
      var sin = Math.sin(turn);
      if (item.shape === 'ellipse') {
        // No corner: the point on the ellipse furthest towards the top left.
        var dx = cos + sin;
        var dy = cos - sin;
        var reach = Math.sqrt(a * a * dx * dx + b * b * dy * dy) || 1;
        var px = -a * a * dx / reach;
        var py = -b * b * dy / reach;
        return [round(item.x + px * cos - py * sin), round(item.y + px * sin + py * cos)];
      }
      points = [[-a, -b], [a, -b], [a, b], [-a, b]].map(function (d) {
        return [round(item.x + d[0] * cos - d[1] * sin), round(item.y + d[0] * sin + d[1] * cos)];
      });
    } else {
      points = item.points;
    }
    var best = points[0];
    points.forEach(function (p) {
      var s = p[0] + p[1];
      var t = best[0] + best[1];
      if (s < t || (s === t && p[1] < best[1])) best = p;
    });
    return [round(best[0]), round(best[1])];
  }

  function aoTabSVG(item) {
    if (!item.label) return '';
    var font = fontSize(item.size);
    var pad = round(font * 0.4);
    var corner = aoCorner(item);
    var w = round(item.label.length * font * 0.6 + 2 * pad);
    var h = round(font * 1.45);
    var colour = itemColour(item);
    var contrast = inkBacking(colour);
    return '<g class="tm-aotab"><rect x="' + corner[0] + '" y="' + corner[1] +
      '" width="' + w + '" height="' + h + '" rx="' + round(font * 0.2) +
      '" fill="' + colour + '" stroke="' + contrast + '" stroke-width="' +
      round(font * 0.12) + '"/><text x="' + round(corner[0] + pad) + '" y="' +
      round(corner[1] + h * 0.72) + '" text-anchor="start" font-size="' + font +
      '" font-weight="700" font-family="' + attr(catalog.armaFont) + '" fill="' + contrast + '">' +
      esc(item.label) + '</text></g>';
  }

  // Mirrors tint_filter() in utils/tacmap.py: Arma multiplies a marker's
  // picture by its colour. The server put a filter in the defs for every
  // colour on the map when it was drawn; a new colour gets one here.
  var svgDefs = svg.querySelector('defs') ||
    svg.insertBefore(document.createElementNS('http://www.w3.org/2000/svg', 'defs'), svg.firstChild);

  function tintId(colour) {
    var hex = String(colour || '').replace('#', '').toLowerCase();
    if (!/^[0-9a-f]{6}$/.test(hex)) hex = 'ffffff';
    if (!document.getElementById('tmt-' + hex)) {
      var parts = [0, 2, 4].map(function (at) {
        return Math.round(parseInt(hex.slice(at, at + 2), 16) / 255 * 10000) / 10000;
      });
      var filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
      filter.setAttribute('id', 'tmt-' + hex);
      filter.setAttribute('color-interpolation-filters', 'sRGB');
      filter.innerHTML = '<feColorMatrix type="matrix" values="' + parts[0] + ' 0 0 0 0 0 ' +
        parts[1] + ' 0 0 0 0 0 ' + parts[2] + ' 0 0 0 0 0 1 0"/>';
      svgDefs.appendChild(filter);
    }
    return 'tmt-' + hex;
  }

  function pictureSVG(key, colour) {
    // Mirrors _picture_svg() in utils/tacmap.py: the game's picture tinted
    // with the item's colour, a flag as it is, a placeholder when this
    // server has none.
    if (!iconKeys[key]) return catalog.missingPicture;
    if (markers[key] && markers[key].paint) return '<use href="#tmk-' + key + '"/>';
    return '<use href="#tmk-' + key + '" filter="url(#' + tintId(colour) + ')"/>';
  }

  function unitSVG(item) {
    // Mirrors _unit_svg() in utils/tacmap.py.
    var colour = itemColour(item);
    var scale = catalog.unitBox * item.size / 100;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') rotate(' +
      (item.rotation || 0) + ') scale(' + scale + ') translate(-50,-50)';
    var parts = [pictureSVG(unitPicture(item), colour)];
    var echelon = echelons[item.echelon];
    if (echelon && iconKeys[echelon.picture]) parts.push(pictureSVG(echelon.picture, colour));
    return '<g transform="' + transform + '">' + parts.join('') + '</g>' +
      label(item.label, item.x + catalog.unitBox * item.size * catalog.unitLabelGap,
        item.y + fontSize(item.size) * 0.35, item.size, colour, 'start');
  }

  function markerKey(item) {
    return markers[item.marker] ? item.marker : catalog.defaultMarker;
  }

  function pointSVG(item) {
    // Mirrors _point_svg() in utils/tacmap.py.
    var scale = catalog.pointBox * item.size / 100 * 1.55;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') rotate(' +
      (item.rotation || 0) + ') scale(' + scale + ') translate(-50,-50)';
    var name = ((item.glyph || '') + ' ' + item.label).trim();
    var ink = itemColour(item);
    return '<g transform="' + transform + '">' + pictureSVG(markerKey(item), ink) +
      '</g>' + label(name, item.x + catalog.pointBox * item.size * catalog.pointLabelGap,
        item.y + fontSize(item.size) * 0.35, item.size, ink, 'start');
  }

  // Mirrors arrow_head() in utils/tacmap.py — a polygon rather than a marker,
  // because a marker cannot take the colour of the line it sits on. Sized
  // from the line's thickness, so a 1px line gets a fine head.
  function arrowHead(points, weight, atStart) {
    var from = atStart ? points[1] : points[points.length - 2];
    var to = atStart ? points[0] : points[points.length - 1];
    var length = Math.hypot(to[0] - from[0], to[1] - from[1]);
    if (!length) return null;
    var ax = (to[0] - from[0]) / length;
    var ay = (to[1] - from[1]) / length;
    var back = 8 + 3 * weight;
    var wide = 3 + 1.5 * weight;
    var tip = weight / 2;
    return [
      [round(to[0] + ax * tip), round(to[1] + ay * tip)],
      [round(to[0] - ax * back - ay * wide), round(to[1] - ay * back + ax * wide)],
      [round(to[0] - ax * back + ay * wide), round(to[1] - ay * back - ax * wide)]
    ];
  }

  function weightOf(item) {
    return item.weight || catalog.defaultWeight;
  }

  // Mirrors dash_pattern() and _stroke(): so many screen pixels at every
  // zoom, as Arma's own map lines are — the thinnest is a true 1px line —
  // and drawn once, with no backing pass under it.
  function dashPattern(style, weight) {
    if (style === 'dashed') return [3 * weight + 6, 2 * weight + 4];
    if (style === 'dotted') return [0.01, 2 * weight + 3];
    return null;
  }

  function strokeAttrs(colour, weight, style) {
    var dash = dashPattern(style, weight);
    return 'stroke="' + colour + '" stroke-width="' + weight +
      '" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"' +
      (dash ? ' stroke-dasharray="' + dash[0] + ' ' + dash[1] + '"' : '');
  }

  var ARROW_ENDS = { end: [false], start: [true], both: [true, false] };

  // Mirrors line_label_spots(): above the middle corner, or off the ends,
  // reading away from the line — a phase line is named at both ends.
  function lineLabelSpots(item) {
    if (!item.label) return [];
    var points = item.points;
    var place = item.label_at || 'middle';
    if (place === 'middle') {
      var middle = points[Math.floor(points.length / 2)];
      return [[middle[0], middle[1] - 10 * item.size, 'middle']];
    }
    var font = fontSize(item.size);
    var gap = font * 0.6;
    var ends = { ends: ['start', 'end'], end: ['end'], start: ['start'] }[place] || [];
    return ends.map(function (end) {
      var tip = end === 'start' ? points[0] : points[points.length - 1];
      var back = end === 'start' ? points[1] : points[points.length - 2];
      var dx = tip[0] - back[0];
      var dy = tip[1] - back[1];
      var length = Math.hypot(dx, dy) || 1;
      var x = tip[0] + dx / length * gap;
      var y = tip[1] + dy / length * gap + font * 0.35;
      var anchor = dx > Math.abs(dy) * 0.3 ? 'start'
        : (-dx > Math.abs(dy) * 0.3 ? 'end' : 'middle');
      if (anchor === 'middle') y += font * (dy > 0 ? 0.6 : -0.6);
      return [round(x), round(y), anchor];
    });
  }

  function shapeSVG(item) {
    var colour = itemColour(item);
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    var weight = weightOf(item);
    var stroke = strokeAttrs(colour, weight, item.style);
    if (item.kind === 'area') {
      var centre = item.points.reduce(function (sum, point) {
        return [sum[0] + point[0] / item.points.length, sum[1] + point[1] / item.points.length];
      }, [0, 0]);
      return '<polygon points="' + points + '" ' +
        (item.ao ? 'fill="none"' : 'fill="' + colour + '" fill-opacity="0.22"') +
        ' ' + stroke + '/>' +
        (item.ao ? aoTabSVG(item) : label(item.label, centre[0], centre[1], item.size,
          itemColour(item)));
    }
    var parts = ['<polyline points="' + points + '" fill="none" ' + stroke + '/>'];
    var ends = item.arrow === true ? [false] : (ARROW_ENDS[item.arrow] || []);
    ends.forEach(function (atStart) {
      var head = arrowHead(item.points, weight, atStart);
      if (head) {
        parts.push('<polygon points="' + head.map(function (corner) {
          return corner[0] + ',' + corner[1];
        }).join(' ') + '" fill="' + colour + '"/>');
      }
    });
    lineLabelSpots(item).forEach(function (spot) {
      parts.push(label(item.label, spot[0], spot[1], item.size, itemColour(item), spot[2]));
    });
    return parts.join('');
  }

  // Mirrors hatch_segments() in utils/tacmap.py: diagonal lines cut to the
  // rectangle or the ellipse as geometry, so both renderers draw the same ones
  // and the hatching takes the shape's own colour.
  function hatchSegments(shape, w, h, gap) {
    var a = w / 2;
    var b = h / 2;
    if (a <= 0 || b <= 0 || gap <= 0) return [];
    var out = [];
    var step = gap * Math.SQRT2;
    var count = Math.floor((a + b) / step);
    for (var k = -count; k <= count; k++) {
      var c = k * step;
      var x1, x2;
      if (shape === 'ellipse') {
        var qa = 1 / (a * a) + 1 / (b * b);
        var qb = 2 * c / (b * b);
        var qc = c * c / (b * b) - 1;
        var disc = qb * qb - 4 * qa * qc;
        if (disc <= 0) continue;
        var root = Math.sqrt(disc);
        x1 = (-qb - root) / (2 * qa);
        x2 = (-qb + root) / (2 * qa);
      } else {
        x1 = Math.max(-a, -b - c);
        x2 = Math.min(a, b - c);
        if (x2 <= x1) continue;
      }
      out.push([round(x1), round(x1 + c), round(x2), round(x2 + c)]);
    }
    return out;
  }

  function figureOutline(item, a, b) {
    return item.shape === 'ellipse'
      ? '<ellipse cx="0" cy="0" rx="' + a + '" ry="' + b + '"'
      : '<rect x="' + (-a) + '" y="' + (-b) + '" width="' + round(2 * a) +
        '" height="' + round(2 * b) + '"';
  }

  function figureTurn(item) {
    return '<g transform="translate(' + item.x + ' ' + item.y + ') rotate(' +
      (item.rotation || 0) + ')">';
  }

  // Mirrors _figure_svg(): drawn centred on 0,0 and turned into place, so the
  // hatching and the outline turn together.
  function figureSVG(item) {
    var colour = itemColour(item);
    var drawn = lineSize(item.size);
    var weight = weightOf(item);
    var a = round(item.w / 2);
    var b = round(item.h / 2);
    var outline = figureOutline(item, a, b);
    // An AO is the clear ground itself: a border and nothing in it.
    var fillKind = item.ao ? 'none' : item.fill;
    var fill = fillKind === 'light'
      ? 'fill="' + colour + '" fill-opacity="0.22"' : 'fill="none"';
    var parts = [];
    if (fillKind === 'hatch') {
      var path = hatchSegments(item.shape, 2 * a, 2 * b, catalog.hatchGap * drawn)
        .map(function (s) { return 'M' + s[0] + ',' + s[1] + 'L' + s[2] + ',' + s[3]; })
        .join('');
      if (path) {
        parts.push('<path d="' + path + '" stroke="' + colour + '" stroke-width="' +
          Math.max(1, weight / 2) + '" vector-effect="non-scaling-stroke"/>');
      }
    }
    parts.push(outline + ' ' + fill + ' ' + strokeAttrs(colour, weight, item.style) + '/>');
    return figureTurn(item) + parts.join('') + '</g>' +
      (item.ao ? aoTabSVG(item) : label(item.label, item.x, item.y, item.size, itemColour(item)));
  }

  function itemSVG(item) {
    if (item.kind === 'shape') return figureSVG(item);
    if (item.kind === 'unit') return unitSVG(item);
    if (item.kind === 'point') return pointSVG(item);
    if (item.kind === 'text') {
      return label(item.label || ' ', item.x, item.y, item.size * 1.6, item.color || '#ffffff');
    }
    return shapeSVG(item);
  }

  function hitSVG(item) {
    // A hair-thin line is impossible to grab, so every line and area carries an
    // invisible fat copy of itself for the pointer to land on. A task marker
    // needs one too: it is line art with no fill, so without this a click in
    // the middle of an X or a circle lands on the terrain behind it.
    // Both targets have a floor, because both used to be sized from the item
    // alone: at the small end the thing you could see became a thing you
    // could not click. A unit is a filled frame and needs no help at size 1,
    // but at the bottom of the slider it is six units across, so it gets the
    // same disc — never smaller than what it already had.
    if (item.kind === 'point' || item.kind === 'unit') {
      var box = item.kind === 'unit' ? catalog.unitBox * 0.5 : catalog.pointBox * 0.8;
      return '<circle cx="' + round(item.x) + '" cy="' + round(item.y) + '" r="' +
        round(Math.max(box * item.size, catalog.minHit / 2)) +
        '" fill="#000" fill-opacity="0"/>';
    }
    if (item.kind === 'shape') {
      // The outline, fat and invisible, the same as a line's. A light fill
      // catches a click by itself; an outline or a hatching would otherwise
      // only be grabbable on its hair-thin strokes.
      return figureTurn(item) + figureOutline(item, round(item.w / 2), round(item.h / 2)) +
        ' fill="none" stroke="#000" stroke-opacity="0" stroke-width="' +
        round(Math.max(20 * item.size, catalog.minHit)) + '"/></g>';
    }
    if (item.kind !== 'line' && item.kind !== 'area') return '';
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    return '<polyline points="' + points + '" fill="none" stroke="#000"' +
      ' stroke-opacity="0" stroke-width="' +
      round(Math.max(20 * item.size, catalog.minHit)) + '"/>';
  }

  function emptySheet() {
    return '<rect x="0" y="0" width="' + state.doc.width + '" height="' +
      state.doc.height + '" fill="#20262e"/>';
  }

  function tilesSVG() {
    var background = state.doc.background;
    var perSide = Math.pow(2, background.zoom);
    var width = state.doc.width / perSide;
    var height = state.doc.height / perSide;
    var tiles = [];
    // Mirrors _tiles_svg() in utils/tacmap.py: a coarse copy of the terrain
    // under the grid, then every tile at its own exact size. Stretching a tile
    // to overlap its neighbour is what made features jump at a boundary.
    if (background.zoom > catalog.backdropZoom) {
      tiles.push('<image href="' + attr(background.url + '/' +
        catalog.backdropZoom + '/0/0.png') +
        '" x="0" y="0" width="' + state.doc.width +
        '" height="' + state.doc.height + '" preserveAspectRatio="none"/>');
    }
    for (var column = 0; column < perSide; column++) {
      for (var row = 0; row < perSide; row++) {
        tiles.push('<image href="' + attr(background.url + '/' + background.zoom +
          '/' + column + '/' + row + '.png') +
          '" x="' + round(column * width) + '" y="' + round(row * height) +
          '" width="' + round(width) + '" height="' + round(height) +
          '" preserveAspectRatio="none"/>');
      }
    }
    return emptySheet() + '<g opacity="' + background.opacity + '">' +
      tiles.join('') + '</g>';
  }

  function backgroundSVG() {
    var background = state.doc.background;
    if (!background.url) return emptySheet();
    if (background.kind === 'tiles') return tilesSVG();
    return '<image href="' + attr(background.url) + '" x="0" y="0" width="' +
      state.doc.width + '" height="' + state.doc.height + '" opacity="' +
      background.opacity + '" preserveAspectRatio="none"/>';
  }

  // Arma's world metres as a point on the sheet — the mirror of
  // tacmap.from_world(). The sheet's y grows down and Arma's grows north, so
  // the vertical axis flips.
  function fromWorld(wx, wy) {
    var arma = state.doc.arma || {};
    var spanX = (arma.right - arma.left) || 1;
    var spanY = (arma.top - arma.bottom) || 1;
    return [(wx - arma.left) / spanX * state.doc.width,
            (arma.top - wy) / spanY * state.doc.height];
  }

  // The mirror of tacmap.places_svg(). Kept in step with it by hand, the same
  // bargain itemSVG() makes: the shapes live in the defs, and what is written
  // twice is the composition.
  function placesSVG() {
    var shows = state.doc.places || {};
    if (!shows.show || !places.length) return '';
    var groups = shows.groups || [];
    var scale = Math.max(0.4, Math.min(Number(shows.scale) || 1, 3));
    var shown = [];
    places.forEach(function (place) {
      var spec = catalog.placeKinds[place.kind] || catalog.placeKinds[catalog.defaultPlaceKind];
      if (groups.length && groups.indexOf(spec.group) < 0) return;
      var point = fromWorld(place.x, place.y);
      if (point[0] < 0 || point[0] > state.doc.width) return;
      if (point[1] < 0 || point[1] > state.doc.height) return;
      shown.push({ name: place.name, rank: spec.rank, x: point[0], y: point[1] });
    });
    // Biggest last, so where two collide the one people navigate by survives.
    shown.sort(function (a, b) { return a.rank - b.rank; });

    return '<g class="tm-places" pointer-events="none">' + shown.map(function (place) {
      var size = Math.max((catalog.placeSizes[place.rank] || 7) * scale, 5.4);
      var common = 'x="' + round(place.x) + '" y="' + round(place.y) +
        '" text-anchor="middle" font-size="' + round(size) +
        '" font-family="inherit"' + (place.rank >= 4 ? ' letter-spacing="1.2"' : '');
      return '<text ' + common + ' stroke="#ffffff" stroke-width="' +
        round(size / 4) + '" stroke-linejoin="round" fill="none" opacity="0.85">' +
        esc(place.name) + '</text>' +
        '<text ' + common + ' fill="#1b2027">' + esc(place.name) + '</text>';
    }).join('') + '</g>';
  }

  function gridSVG() {
    var grid = state.doc.grid || {};
    if (!grid.show) return '';
    var lines = [];
    var column;
    for (column = 1; column < grid.cols; column++) {
      var x = round(state.doc.width * column / grid.cols);
      lines.push('<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + state.doc.height + '"/>');
    }
    var row;
    for (row = 1; row < grid.rows; row++) {
      var y = round(state.doc.height * row / grid.rows);
      lines.push('<line x1="0" y1="' + y + '" x2="' + state.doc.width + '" y2="' + y + '"/>');
    }
    return '<g stroke="#ffffff" stroke-opacity="0.28" stroke-width="1.5">' +
      lines.join('') + '</g>';
  }

  function boxDraftSVG() {
    var box = boxGeometry(state.box);
    if (!box) return '';
    var paint = colours[state.side];
    return figureTurn(box) + figureOutline(box, round(box.w / 2), round(box.h / 2)) +
      ' fill="none" stroke="' + paint.fill + '" stroke-width="4" stroke-dasharray="10 8"/></g>';
  }

  function draftSVG() {
    if (state.ruler) return rulerSVG();
    if (state.box) return boxDraftSVG();
    if (!state.draft || state.draft.points.length === 0) return '';
    var paint = colours[state.side];
    var points = state.draft.points.concat(
      state.draft.cursor ? [state.draft.cursor] : []
    ).map(function (point) { return round(point[0]) + ',' + round(point[1]); }).join(' ');
    var tag = state.draft.kind === 'area' ? 'polygon' : 'polyline';
    return '<' + tag + ' points="' + points + '" fill="none" stroke="' + paint.fill +
      '" stroke-width="2" stroke-dasharray="8 5" vector-effect="non-scaling-stroke"' +
      ' stroke-linecap="round" pointer-events="none"/>';
  }

  function layers() {
    if (!state.doc.layers || !state.doc.layers.length) {
      state.doc.layers = [{ id: 'plan', name: 'Plan', visible: true }];
    }
    return state.doc.layers;
  }

  function layerRank(id) {
    var found = layers().findIndex(function (entry) { return entry.id === id; });
    return found < 0 ? 0 : found;
  }

  function hiddenLayers() {
    var hidden = {};
    layers().forEach(function (entry) { if (!entry.visible) hidden[entry.id] = true; });
    return hidden;
  }

  // Mirrors ordered_items() in utils/tacmap.py: layer, then kind, then the
  // document's own order — so a symbol is never buried under an area drawn
  // after it, and Bring to front still decides between two of the same kind.
  function drawOrder() {
    var hidden = hiddenLayers();
    var order = [];
    state.doc.items.forEach(function (item, index) {
      if (!hidden[item.layer]) order.push(index);
    });
    return order.sort(function (a, b) {
      var first = state.doc.items[a];
      var second = state.doc.items[b];
      return (layerRank(first.layer) - layerRank(second.layer)) ||
        ((catalog.kindOrder[first.kind] || 0) - (catalog.kindOrder[second.kind] || 0)) ||
        (a - b);
    });
  }

  // Mirrors mask_svg() in utils/tacmap.py: grey over the whole sheet, every
  // visible AO cut out of it. A <mask> rather than an even-odd path, so two
  // AOs that overlap merge instead of the overlap turning grey again.
  function maskSVG() {
    var mask = state.doc.mask;
    if (!mask || !mask.on) return '';
    var holes = drawOrder().map(function (index) {
      var item = state.doc.items[index];
      if (!item.ao || (item.kind !== 'area' && item.kind !== 'shape')) return '';
      if (item.kind === 'shape') {
        return figureTurn(item) + figureOutline(item, round(item.w / 2), round(item.h / 2)) +
          '/></g>';
      }
      return '<polygon points="' + item.points.map(function (point) {
        return point[0] + ',' + point[1];
      }).join(' ') + '"/>';
    }).join('');
    var pad = Math.max(state.doc.width, state.doc.height);
    var box = 'x="' + (-pad) + '" y="' + (-pad) + '" width="' + (state.doc.width + 2 * pad) +
      '" height="' + (state.doc.height + 2 * pad) + '"';
    return '<defs><mask id="tm-aomask-live" maskUnits="userSpaceOnUse" ' + box + '>' +
      '<rect ' + box + ' fill="#fff"/><g fill="#000">' + holes + '</g></mask></defs>' +
      '<rect ' + box + ' fill="' + catalog.maskColour + '" fill-opacity="' + mask.opacity +
      '" mask="url(#tm-aomask-live)" pointer-events="none"/>';
  }

  function render() {
    maskLayer.innerHTML = maskSVG();
    layer.innerHTML = drawOrder().map(function (index) {
      var item = state.doc.items[index];
      return '<g class="tm-item" data-i="' + index + '">' + itemSVG(item) + hitSVG(item) + '</g>';
    }).join('');
    back.innerHTML = backgroundSVG() + gridSVG() + placesSVG();
    overlay.innerHTML = draftSVG();
    drawSelection();
  }

  // Every selected item: the one picked last (state.selected) and any
  // picked with it (state.extra), so several can be moved, copied or
  // deleted together.
  function selection() {
    if (state.selected < 0) return [];
    return [state.selected].concat((state.extra || []).filter(function (index) {
      return index !== state.selected && index < state.doc.items.length;
    }));
  }

  function drawSelection() {
    Array.prototype.forEach.call(overlay.querySelectorAll('.tm-ring'),
      function (node) { node.remove(); });
    var picked = selection();
    if (!picked.length) {
      drawHandles();
      return;
    }
    picked.forEach(function (index) {
      var group = layer.querySelector('[data-i="' + index + '"]');
      if (!group) return;
      var box = group.getBBox();
      var ring = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      ring.setAttribute('class', 'tm-ring');
      ring.setAttribute('x', box.x - 6);
      ring.setAttribute('y', box.y - 6);
      ring.setAttribute('width', box.width + 12);
      ring.setAttribute('height', box.height + 12);
      overlay.appendChild(ring);
    });
    // Handles only for one thing at a time — several are moved as a group.
    if (picked.length === 1) drawHandles();
    else Array.prototype.forEach.call(overlay.querySelectorAll('.tm-handle, .tm-stalk'),
      function (node) { node.remove(); });
  }

  /* -- a shape's handles ----------------------------------------------------- */

  // Corners in the shape's own frame, in the order the handles are numbered.
  var CORNERS = [[-1, -1], [1, -1], [1, 1], [-1, 1]];

  function turnPoint(item, dx, dy) {
    var turn = (item.rotation || 0) * Math.PI / 180;
    return [item.x + dx * Math.cos(turn) - dy * Math.sin(turn),
            item.y + dx * Math.sin(turn) + dy * Math.cos(turn)];
  }

  function rotateHandleGap() { return view.w / 28; }

  function drawHandles() {
    Array.prototype.forEach.call(overlay.querySelectorAll('.tm-handle, .tm-stalk'),
      function (node) { node.remove(); });
    var item = selected();
    if (!editable || !item) return;
    if (item.kind === 'line' || item.kind === 'area') {
      drawVertexHandles(item);
      return;
    }
    if (item.kind !== 'shape') return;
    var a = item.w / 2;
    var b = item.h / 2;
    var r = round(view.w / 110);
    var top = turnPoint(item, 0, -b);
    var knob = turnPoint(item, 0, -b - rotateHandleGap());
    var html = '<line class="tm-stalk" x1="' + round(top[0]) + '" y1="' + round(top[1]) +
      '" x2="' + round(knob[0]) + '" y2="' + round(knob[1]) + '"/>';
    CORNERS.forEach(function (corner, index) {
      var spot = turnPoint(item, corner[0] * a, corner[1] * b);
      html += '<rect class="tm-handle" data-h="' + index + '" x="' + round(spot[0] - r) +
        '" y="' + round(spot[1] - r) + '" width="' + 2 * r + '" height="' + 2 * r + '"/>';
    });
    html += '<circle class="tm-handle tm-turn" data-h="turn" cx="' + round(knob[0]) +
      '" cy="' + round(knob[1]) + '" r="' + round(r * 1.2) + '"/>';
    overlay.insertAdjacentHTML('beforeend', html);
  }

  // What a box being dragged out is: the two corners give the size, Shift
  // makes it a square or a circle. Drawn upright; the handles turn it after.
  function boxGeometry(box) {
    if (!box) return null;
    var dx = box.to[0] - box.from[0];
    var dy = box.to[1] - box.from[1];
    if (box.square) {
      var side = Math.max(Math.abs(dx), Math.abs(dy));
      dx = dx < 0 ? -side : side;
      dy = dy < 0 ? -side : side;
    }
    return { kind: 'shape', shape: box.shape, x: round(box.from[0] + dx / 2),
             y: round(box.from[1] + dy / 2), w: round(Math.abs(dx)),
             h: round(Math.abs(dy)), rotation: 0 };
  }

  // Resizing keeps the opposite corner where it is, in the shape's own frame,
  // so a turned box grows along its own sides rather than the sheet's.
  function resizeTo(start, corner, here, square) {
    var item = JSON.parse(start);
    var a = item.w / 2;
    var b = item.h / 2;
    var sx = CORNERS[corner][0];
    var sy = CORNERS[corner][1];
    var fixed = turnPoint(item, -sx * a, -sy * b);
    var turn = (item.rotation || 0) * Math.PI / 180;
    var px = here.x - fixed[0];
    var py = here.y - fixed[1];
    var lx = px * Math.cos(turn) + py * Math.sin(turn);
    var ly = -px * Math.sin(turn) + py * Math.cos(turn);
    if (square) {
      var side = Math.max(Math.abs(lx), Math.abs(ly));
      lx = lx < 0 ? -side : side;
      ly = ly < 0 ? -side : side;
    }
    var least = catalog.minShape;
    if (Math.abs(lx) < least) lx = lx < 0 ? -least : least;
    if (Math.abs(ly) < least) ly = ly < 0 ? -least : least;
    item.w = round(Math.abs(lx));
    item.h = round(Math.abs(ly));
    var centre = turnPoint({ x: fixed[0], y: fixed[1], rotation: item.rotation }, lx / 2, ly / 2);
    item.x = round(centre[0]);
    item.y = round(centre[1]);
    return item;
  }

  // The knob sits straight above the shape, so the angle is measured from
  // there; Shift snaps to 15° for the boxes that should sit square to a road.
  function turnTo(start, here, snap) {
    var item = JSON.parse(start);
    var angle = Math.atan2(here.x - item.x, -(here.y - item.y)) * 180 / Math.PI;
    if (snap) angle = Math.round(angle / 15) * 15;
    item.rotation = Math.round(angle * 10) / 10;
    return item;
  }

  /* -- a line's corners: drag, add, delete --------------------------------- */

  // Every corner of a selected line or area is a handle to drag; the dot in
  // the middle of each segment adds a corner there when it is dragged; Alt+
  // click or right-click on a corner deletes it. Redrawing a line to move
  // one corner was the only way before.
  function drawVertexHandles(item) {
    var r = round(view.w / 140);
    var points = item.points;
    var html = '';
    var count = item.kind === 'area' ? points.length : points.length - 1;
    for (var i = 0; i < count; i++) {
      var a = points[i];
      var b = points[(i + 1) % points.length];
      html += '<circle class="tm-handle tm-mid" data-h="m' + i + '" cx="' +
        round((a[0] + b[0]) / 2) + '" cy="' + round((a[1] + b[1]) / 2) + '" r="' +
        round(r * 0.7) + '"><title>Drag to add a corner here</title></circle>';
    }
    points.forEach(function (point, index) {
      html += '<circle class="tm-handle tm-vertex" data-h="v' + index + '" cx="' +
        round(point[0]) + '" cy="' + round(point[1]) + '" r="' + r +
        '"><title>Drag to move · Alt+click or right-click to delete</title></circle>';
    });
    overlay.insertAdjacentHTML('beforeend', html);
  }

  svg.addEventListener('contextmenu', function (event) {
    var handle = event.target.closest ? event.target.closest('.tm-vertex') : null;
    if (!handle || !selected()) return;
    event.preventDefault();
    removeVertex(state.selected, Number(handle.dataset.h.slice(1)));
  });

  function removeVertex(index, vertex) {
    var item = state.doc.items[index];
    if (!item || !item.points) return;
    var least = item.kind === 'area' ? 3 : 2;
    if (item.points.length <= least) {
      say('A ' + (item.kind === 'area' ? 'area needs three' : 'line needs two') +
          ' corners — delete the whole thing instead.', 'warn');
      return;
    }
    snapshot();
    item.points.splice(vertex, 1);
    touched();
    render();
    fillInspector();
  }

  /* -- measuring: metres, and where you are on Arma's grid ------------------ */

  // Mirrors metres_per_unit(), length_m() and grid_ref() in utils/tacmap.py:
  // the sheet's Arma corners are what turn sheet units into the ground.
  function metresPerUnit() {
    var arma = state.doc.arma || {};
    return [Math.abs((arma.right - arma.left) || 0) / state.doc.width,
            Math.abs((arma.top - arma.bottom) || 0) / state.doc.height];
  }

  function lengthM(points, closed) {
    var scale = metresPerUnit();
    var path = closed ? points.concat([points[0]]) : points;
    var total = 0;
    for (var i = 1; i < path.length; i++) {
      total += Math.hypot((path[i][0] - path[i - 1][0]) * scale[0],
                          (path[i][1] - path[i - 1][1]) * scale[1]);
    }
    return total;
  }

  function metres(value) {
    if (value >= 10000) return (value / 1000).toFixed(1) + ' km';
    if (value >= 1000) return (value / 1000).toFixed(2) + ' km';
    return Math.round(value) + ' m';
  }

  function toWorld(x, y) {
    var arma = state.doc.arma || {};
    return [arma.left + x / state.doc.width * (arma.right - arma.left),
            arma.top - y / state.doc.height * (arma.top - arma.bottom)];
  }

  function pad(value, width) {
    var text = String(Math.max(0, Math.floor(value)) % Math.pow(10, width));
    while (text.length < width) text = '0' + text;
    return text;
  }

  function gridRef(x, y) {
    var world = toWorld(x, y);
    return pad(world[0] / 100, 3) + ' ' + pad(world[1] / 100, 3);
  }

  // Bearing clockwise from north, in degrees — the sheet's y grows down.
  function bearing(a, b) {
    var scale = metresPerUnit();
    var angle = Math.atan2((b[0] - a[0]) * scale[0], -(b[1] - a[1]) * scale[1]) * 180 / Math.PI;
    return Math.round((angle + 360) % 360);
  }

  var readout = document.getElementById('tmreadout');

  // The corner of the map: the grid reference under the pointer, and while
  // something is being drawn or measured, how long it is.
  function showReadout(point, extra) {
    if (!readout) return;
    var text = 'Grid ' + gridRef(point.x, point.y);
    if (extra) text += ' · ' + extra;
    readout.textContent = text;
  }

  function draftMeasure() {
    var draft = state.draft;
    if (!draft || !draft.points.length) return '';
    var points = draft.points.concat(draft.cursor ? [draft.cursor] : []);
    var last = points.length > 1 ? lengthM(points.slice(-2)) : 0;
    var parts = [];
    if (points.length > 1) parts.push('segment ' + metres(last) + ' at ' +
      bearing(points[points.length - 2], points[points.length - 1]) + '°');
    parts.push('total ' + metres(lengthM(points, draft.kind === 'area')));
    return parts.join(' · ');
  }

  /* -- snapping ---------------------------------------------------------- */

  var snapBox = document.getElementById('tmsnap');
  state.snap = false;
  try { state.snap = window.localStorage.getItem('tmsnap') === '1'; } catch (e) { /* none */ }
  if (snapBox) {
    snapBox.checked = state.snap;
    snapBox.addEventListener('change', function () {
      state.snap = snapBox.checked;
      try { window.localStorage.setItem('tmsnap', state.snap ? '1' : '0'); } catch (e) { /* none */ }
    });
  }

  // With Snap on, a corner being placed or dragged lands on the nearest
  // corner of another line or area, a shape's corner or centre, or a symbol
  // or marker — within a dozen screen pixels — so boundaries meet properly.
  function snapPoint(point, skipIndex, skipVertex) {
    if (!state.snap) return null;
    var reach = view.w / svg.getBoundingClientRect().width * 12;
    var best = null;
    var bestDistance = reach;
    function offer(x, y) {
      var distance = Math.hypot(x - point.x, y - point.y);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = [round(x), round(y)];
      }
    }
    var hidden = hiddenLayers();
    state.doc.items.forEach(function (item, index) {
      if (hidden[item.layer]) return;
      if (item.points) {
        item.points.forEach(function (corner, vertex) {
          if (index === skipIndex && (skipVertex === undefined || vertex === skipVertex)) return;
          offer(corner[0], corner[1]);
        });
      } else if (item.kind === 'shape') {
        offer(item.x, item.y);
        CORNERS.forEach(function (corner) {
          var spot = turnPoint(item, corner[0] * item.w / 2, corner[1] * item.h / 2);
          offer(spot[0], spot[1]);
        });
      } else if (index !== skipIndex) {
        offer(item.x, item.y);
      }
    });
    if (state.draft) {
      // Closing a line on its own first corner.
      var first = state.draft.points[0];
      if (first && state.draft.points.length > 2) offer(first[0], first[1]);
    }
    return best;
  }

  function snapMark(spot) {
    var old = overlay.querySelector('.tm-snapmark');
    if (old) old.remove();
    if (!spot) return;
    var r = round(view.w / 90);
    overlay.insertAdjacentHTML('beforeend', '<circle class="tm-snapmark" cx="' + spot[0] +
      '" cy="' + spot[1] + '" r="' + r + '" pointer-events="none"/>');
  }

  /* -- the ruler: measure without drawing --------------------------------- */

  function rulerSVG() {
    var ruler = state.ruler;
    if (!ruler) return '';
    var a = ruler.from;
    var b = ruler.to;
    return '<g class="tm-ruler" pointer-events="none"><line x1="' + a[0] + '" y1="' + a[1] +
      '" x2="' + b[0] + '" y2="' + b[1] + '" stroke="#ffd166" stroke-width="2"' +
      ' stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="' + a[0] + '" cy="' + a[1] + '" r="' + round(view.w / 250) +
      '" fill="#ffd166"/></g>';
  }

  function rulerText() {
    var ruler = state.ruler;
    return metres(lengthM([ruler.from, ruler.to])) + ' at ' + bearing(ruler.from, ruler.to) + '°';
  }

  /* -- the sheet, zoom and pan --------------------------------------------- */

  function readViewBox() {
    var parts = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    if (parts.length !== 4 || parts.some(isNaN)) {
      return { x: 0, y: 0, w: state.doc.width, h: state.doc.height };
    }
    return { x: parts[0], y: parts[1], w: parts[2], h: parts[3] };
  }

  function applyView() {
    svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h);
  }

  function resetView() {
    view = { x: 0, y: 0, w: state.doc.width, h: state.doc.height };
    applyView();
  }

  function at(event) {
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var matrix = svg.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    return point.matrixTransform(matrix.inverse());
  }

  svg.addEventListener('wheel', function (event) {
    event.preventDefault();
    var pointer = at(event);
    var factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
    var width = Math.min(Math.max(view.w * factor, state.doc.width / 20), state.doc.width * 3);
    var scale = width / view.w;
    view.x = pointer.x - (pointer.x - view.x) * scale;
    view.y = pointer.y - (pointer.y - view.y) * scale;
    view.w = width;
    view.h = view.h * scale;
    applyView();
  }, { passive: false });

  /* -- layers: a switch each, for whoever is looking ------------------------ */

  var layerPanel = document.getElementById('tmlayers');
  var layerList = document.getElementById('tmlayerlist');

  // The campaign planner keeps two kinds of layer in step with itself: one
  // per AO on a campaign map (cpao<id>) and one per phase on an AO map
  // (cpp<id>). Renaming one here was overwritten by the next sync, and
  // deleting one either came back on the next save or — for an AO — came
  // back twice, which is the duplicated layer. So their names are the
  // planner's, an AO layer's delete deletes the AO, and a phase layer has
  // none.
  function plannerLayer(id) {
    if (/^cpao\d+$/.test(id)) return 'ao';
    if (/^cpp\d+$/.test(id)) return 'phase';
    return null;
  }

  // The layer new things are drawn on has to be one the map still has.
  function keepLayerChoice() {
    var list = layers();
    if (!list.some(function (entry) { return entry.id === state.layer; })) {
      state.layer = list[0].id;
    }
  }

  // Everything the layer list feeds, redrawn from the document — after a
  // delete, an undo, or a save that came back changed. The panel's rows hold
  // the layer objects they were drawn from, so a panel left standing after
  // the document was replaced edits layers that are no longer saved, and its
  // delete buttons count from a list that no longer exists.
  function refreshLayers() {
    keepLayerChoice();
    drawLayers();
    fillLayerPickers();
  }

  function drawLayers() {
    if (!layerPanel) return;
    // One layer and no way to add another is nothing worth a panel — and with
    // nothing else in it, the sidebar is only taking room from the map.
    layerPanel.hidden = !editable && layers().length < 2;
    var aside = app.querySelector('.tmside');
    if (aside && !editable) aside.hidden = layerPanel.hidden;
    layerList.innerHTML = '';
    layers().forEach(function (entry, index) {
      var row = document.createElement('li');
      row.className = 'tmlayer';

      var shown = document.createElement('input');
      shown.type = 'checkbox';
      shown.checked = entry.visible !== false;
      shown.title = 'Show this layer';
      shown.addEventListener('change', function () {
        entry.visible = shown.checked;
        if (editable) touched();
        render();
      });
      row.appendChild(shown);

      var kind = plannerLayer(entry.id);
      if (editable) row.appendChild(layerControls(entry, index));
      if (editable && kind) {
        var fixed = document.createElement('span');
        fixed.className = 'tmlayerfixed';
        fixed.textContent = entry.name;
        fixed.title = kind === 'ao'
          ? 'This AO\'s layer belongs to the campaign planner. Rename the AO by ' +
            'relabelling its outline, or on the campaign page.'
          : 'This phase\'s layer belongs to the campaign planner. It is named after ' +
            'the phase, and goes when nothing is drawn on it and the phase is no ' +
            'longer fought in this AO.';
        row.appendChild(fixed);
        if (kind === 'ao' && layers().length > 1) {
          var dropAO = document.createElement('button');
          dropAO.type = 'button';
          dropAO.className = 'linkish';
          dropAO.textContent = '✕';
          dropAO.title = 'Delete this AO from the campaign, with its outline';
          dropAO.addEventListener('click', function () {
            if (!window.confirm('Delete the ' + entry.name + ' AO? It goes from the ' +
                                'campaign when you save, with everything on this layer.')) {
              return;
            }
            removeLayer(index, true);
          });
          row.appendChild(dropAO);
        }
      } else if (editable) {
        var name = document.createElement('input');
        name.className = 'tmlayername';
        name.value = entry.name;
        name.maxLength = catalog.limits.layerName;
        name.addEventListener('input', function () {
          entry.name = name.value;
          touched();
          fillLayerPickers();
        });
        row.appendChild(name);

        if (layers().length > 1) {
          var drop = document.createElement('button');
          drop.type = 'button';
          drop.className = 'linkish';
          drop.textContent = '✕';
          drop.title = 'Delete this layer — what is on it moves to the first one';
          drop.addEventListener('click', function () { removeLayer(index); });
          row.appendChild(drop);
        }
      } else {
        var text = document.createElement('span');
        text.textContent = entry.name;
        row.appendChild(text);
      }
      layerList.appendChild(row);
    });
  }

  // Up, down and lock, in front of the layer's name. Order is drawing
  // order: a layer lower in the list is drawn over the ones above it.
  function layerControls(entry, index) {
    var box = document.createElement('span');
    box.className = 'tmlayerctl';
    var list = layers();
    [['↑', -1, 'Draw this layer under the one above'],
     ['↓', 1, 'Draw this layer over the one below']].forEach(function (spec) {
      var target = index + spec[1];
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'tmlayerbtn';
      button.textContent = spec[0];
      button.title = spec[2];
      button.disabled = target < 0 || target >= list.length;
      button.addEventListener('click', function () {
        snapshot();
        var moved = list.splice(index, 1)[0];
        list.splice(target, 0, moved);
        touched();
        refreshLayers();
        render();
      });
      box.appendChild(button);
    });
    var lock = document.createElement('button');
    lock.type = 'button';
    lock.className = 'tmlayerbtn' + (entry.locked ? ' on' : '');
    lock.textContent = entry.locked ? '🔒' : '🔓';
    lock.title = entry.locked ? 'Locked — nothing on it can be picked up. Click to unlock.'
      : 'Lock: keep everything on this layer from being picked up or moved';
    lock.addEventListener('click', function () {
      if (entry.locked) delete entry.locked;
      else entry.locked = true;
      // Whatever is selected on a layer that just locked is let go.
      var keep = selection().filter(function (i) { return !isLocked(state.doc.items[i]); });
      state.selected = keep.length ? keep[keep.length - 1] : -1;
      state.extra = keep.slice(0, -1);
      touched();
      drawLayers();
      render();
      fillInspector();
    });
    box.appendChild(lock);
    return box;
  }

  function isLocked(item) {
    if (!item) return false;
    return layers().some(function (entry) { return entry.id === item.layer && entry.locked; });
  }

  function removeLayer(index, withItems) {
    var list = layers();
    if (list.length < 2) return;
    var going = list[index];
    var keep = list[index === 0 ? 1 : 0].id;
    snapshot();
    if (withItems) {
      // An AO's layer: deleting it is deleting the AO, outline and all.
      // Moving its outline to the first layer instead is what the planner
      // read as a brand new AO of the same name.
      state.doc.items = state.doc.items.filter(function (item) {
        return item.layer !== going.id;
      });
    } else {
      // Never take the items with it: a layer is a switch, not a folder
      // anybody meant to throw work into.
      state.doc.items.forEach(function (item) {
        if (item.layer === going.id) item.layer = keep;
      });
    }
    list.splice(index, 1);
    state.selected = -1;
    touched();
    refreshLayers();
    render();
    fillInspector();
  }

  /* -- more room: the map takes the whole screen ---------------------------- */

  var fullButton = document.getElementById('tmfull');
  if (fullButton) {
    fullButton.addEventListener('click', function () {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else if (app.requestFullscreen) {
        app.requestFullscreen().catch(function () { app.classList.toggle('tm-tall'); });
      } else {
        app.classList.toggle('tm-tall');
      }
    });
    document.addEventListener('fullscreenchange', function () {
      fullButton.textContent = document.fullscreenElement ? '✕ Close' : '⛶ Full screen';
    });
  }

  /* -- everything below is the editor and only runs when it may draw -------- */

  if (!editable) {
    drawLayers();
    render();
    return;
  }

  var status = document.getElementById('tmstatus');
  var inspector = document.getElementById('tminspector');

  function say(text, kind) {
    status.textContent = text || '';
    status.title = text || '';
    status.className = 'tmstatus' + (kind ? ' ' + kind : '');
  }

  function snapshot() {
    state.undo.push(JSON.stringify(state.doc));
    if (state.undo.length > 40) state.undo.shift();
  }

  function touched() {
    state.dirty = true;
    state.edits = (state.edits || 0) + 1;
    say('Unsaved changes', 'warn');
    scheduleAutosave();
  }

  // Autosave: a moment after the last change, unless something is being
  // drawn or dragged right now, or the last save ran into somebody else's —
  // then it waits for the person to choose. Remembered per browser; on by
  // default.
  var autosaveBox = document.getElementById('tmautosave');
  var autosaveTimer = null;
  state.autosave = true;
  try { state.autosave = window.localStorage.getItem('tmautosave') !== '0'; } catch (e) { /* none */ }
  if (autosaveBox) {
    autosaveBox.checked = state.autosave;
    autosaveBox.addEventListener('change', function () {
      state.autosave = autosaveBox.checked;
      try { window.localStorage.setItem('tmautosave', state.autosave ? '1' : '0'); } catch (e) { /* none */ }
      if (state.autosave && state.dirty) scheduleAutosave();
    });
  }

  function scheduleAutosave() {
    if (!autosaveBox || !state.autosave || state.blocked) return;
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(function autosaveTick() {
      if (!state.dirty || state.blocked) return;
      if (state.draft || state.drag || state.handle || state.box || state.marquee) {
        autosaveTimer = setTimeout(autosaveTick, 800);
        return;
      }
      save();
    }, 1500);
  }

  function selected() {
    return state.selected >= 0 ? state.doc.items[state.selected] : null;
  }

  function select(index) {
    state.selected = index;
    state.extra = [];
    render();
    fillInspector();
  }

  // Shift+click: add to the selection, or take back out of it.
  function toggleSelected(index) {
    var picked = selection();
    var at0 = picked.indexOf(index);
    if (at0 >= 0) picked.splice(at0, 1);
    else picked.push(index);
    state.selected = picked.length ? picked[picked.length - 1] : -1;
    state.extra = picked.slice(0, -1);
    render();
    fillInspector();
  }

  function add(item) {
    if (state.doc.items.length >= catalog.limits.items) {
      say('That is as much as one map holds (' + catalog.limits.items + ' items).', 'err');
      return;
    }
    snapshot();
    state.doc.items.push(item);
    touched();
    select(state.doc.items.length - 1);
  }

  function newItem(kind, point) {
    var item = { kind: kind, side: state.side, layer: state.layer, label: '',
                 note: '', size: catalog.defaultSize };
    if (kind === 'unit') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.symbol = state.symbol;
      item.rotation = 0;
      item.echelon = state.echelon;
    } else if (kind === 'point') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.glyph = state.glyph;
      item.marker = state.marker;
      // The toolbar swatch reaches markers too now, for the same reason it
      // reaches lines: an objective is the plan's, not one side's.
      item.color = state.color;
    } else if (kind === 'text') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.label = 'Text';
    }
    return item;
  }

  /* -- pointer handling ----------------------------------------------------- */

  svg.addEventListener('pointerleave', function () { lastPointer = null; });

  svg.addEventListener('pointerdown', function (event) {
    if (event.button !== 0) return;
    var point = at(event);
    var group = event.target.closest ? event.target.closest('.tm-item') : null;

    // Dragging is the only way to draw a line, and it is the gesture Arma's
    // own map uses. Ctrl does it from any tool — including Select, so you
    // never have to leave what you are doing — and the Line and Area tools do
    // it without Ctrl. Clicking a line out corner by corner is gone: it was a
    // mode you had to finish before anything else worked.
    var handle = event.target.closest ? event.target.closest('.tm-handle') : null;
    // Handles answer the Select tool. With a drawing tool in hand, a click
    // on a corner starts something new there — which is what Snap is for.
    if (handle && selected() && state.tool === 'select' && !state.draft) {
      event.preventDefault();
      var which = handle.dataset.h;
      if (which.charAt(0) === 'v' && event.altKey) {
        removeVertex(state.selected, Number(which.slice(1)));
        return;
      }
      state.handle = { index: state.selected, which: which, moved: false,
                       start: JSON.stringify(selected()) };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    // Right after a drawing that kept its selection (an AO or phase line
    // waiting for a name), the next click on empty ground only lets go of it.
    if (state.afterDraw && state.tool !== 'select' && !group && !state.draft) {
      state.afterDraw = false;
      if (selected()) {
        event.preventDefault();
        select(-1);
        return;
      }
    }
    state.afterDraw = false;

    if (state.tool === 'measure') {
      event.preventDefault();
      var from = snapPoint(point) || [round(point.x), round(point.y)];
      state.ruler = { from: from, to: from.slice() };
      overlay.innerHTML = draftSVG();
      svg.setPointerCapture(event.pointerId);
      return;
    }

    var aoBox = state.tool === 'ao' && state.aoForm !== 'free';
    if (state.tool === 'rect' || state.tool === 'ellipse' || aoBox) {
      event.preventDefault();
      var form = aoBox ? state.aoForm : state.tool;
      state.box = { shape: form, from: [point.x, point.y], to: [point.x, point.y],
                    square: event.shiftKey, ao: state.tool === 'ao' };
      say('Drawing — hold Shift for a ' +
          (form === 'rect' ? 'square' : 'circle') + ', let go to finish');
      svg.setPointerCapture(event.pointerId);
      return;
    }

    var drawing = state.tool === 'line' || state.tool === 'phase' || state.tool === 'area' ||
      state.tool === 'ao';
    if (state.draft && state.draft.chain) {
      // Plotting point by point, as on Arma's map with Ctrl held: every
      // click fixes the next corner, and a drag from here carries the same
      // line on freehand.
      event.preventDefault();
      // A second click on the same spot, quickly, is a double-click: it
      // finishes the line. Worked out here rather than from the browser's
      // dblclick, which does not fire when the draft redraws under the
      // pointer between the two clicks.
      var lastClick = state.draft.lastClick;
      var now = Date.now();
      if (lastClick && now - lastClick.t < 450 &&
          Math.abs(event.clientX - lastClick.x) + Math.abs(event.clientY - lastClick.y) < 6) {
        finishDraft();
        return;
      }
      state.draft.lastClick = { t: now, x: event.clientX, y: event.clientY };
      var corner = snapCorner(state.draft, point, event.shiftKey);
      if (state.draft.points.length < catalog.limits.points) state.draft.points.push(corner);
      state.draft.press = { x: event.clientX, y: event.clientY, moved: false };
      state.draft.cursor = null;
      overlay.innerHTML = draftSVG();
      svg.setPointerCapture(event.pointerId);
      return;
    }
    if (event.ctrlKey || event.metaKey || drawing) {
      event.preventDefault();
      state.draft = {
        // Which way it is being drawn: chain = a corner per click. Settled
        // on the first let-go — a press that never moved is a click.
        chain: false,
        byCtrl: !!(event.ctrlKey || event.metaKey),
        press: { x: event.clientX, y: event.clientY, moved: false },
        kind: state.tool === 'area' || state.tool === 'ao' ? 'area' : 'line',
        ao: state.tool === 'ao',
        phase: state.tool === 'phase',
        points: [snapPoint(point) || [round(point.x), round(point.y)]],
        cursor: null,
        // Where the straight segment starts, while Shift is held. null when
        // it is not.
        anchor: null
      };
      overlay.innerHTML = draftSVG();
      say('Drawing — click for corners or drag freehand; Shift keeps it straight');
      svg.setPointerCapture(event.pointerId);
      return;
    }

    if (state.tool !== 'select') {
      add(newItem(state.tool, point));
      return;
    }

    if (group && isLocked(state.doc.items[Number(group.dataset.i)])) group = null;
    if (group) {
      var index = Number(group.dataset.i);
      if (event.shiftKey) {
        toggleSelected(index);
        if (selection().indexOf(index) < 0) return;
      } else if (selection().indexOf(index) < 0) {
        select(index);
      }
      // Dragging any selected item moves every selected item with it.
      var moving = selection();
      state.drag = { indices: moving, from: point, moved: false,
                     starts: moving.map(function (i) { return JSON.stringify(state.doc.items[i]); }) };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    if (event.shiftKey) {
      // Shift+drag on empty ground: a box, and everything in it is selected.
      state.marquee = { from: [point.x, point.y], to: [point.x, point.y] };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    select(-1);
    state.pan = { x: event.clientX, y: event.clientY, view: { x: view.x, y: view.y } };
    svg.setPointerCapture(event.pointerId);
  });

  svg.addEventListener('pointermove', function (event) {
    var where = at(event);
    lastPointer = where;
    if (state.ruler && state.ruler.live !== false) {
      state.ruler.to = snapPoint(where) || [round(where.x), round(where.y)];
      overlay.innerHTML = draftSVG();
      showReadout(where, rulerText());
      return;
    }
    showReadout(where, state.draft ? draftMeasure() : '');
    if (state.box) {
      var to = at(event);
      state.box.to = [to.x, to.y];
      state.box.square = event.shiftKey;
      overlay.innerHTML = draftSVG();
      return;
    }
    if (state.handle) {
      var spot = at(event);
      if (!state.handle.moved) {
        state.handle.moved = true;
        snapshot();
      }
      var kind = state.handle.which.charAt(0);
      if (kind === 'v' || kind === 'm') {
        var moving = JSON.parse(state.handle.start);
        var at0 = Number(state.handle.which.slice(1));
        if (kind === 'm') {
          // Dragging a segment's middle adds a corner there, then moves it.
          moving.points.splice(at0 + 1, 0, [0, 0]);
          at0 += 1;
        }
        var target = snapPoint(spot, state.handle.index, at0) ||
          [round(spot.x), round(spot.y)];
        snapMark(snapPoint(spot, state.handle.index, at0));
        moving.points[at0] = target;
        state.doc.items[state.handle.index] = moving;
        render();
        showReadout(spot, metres(lengthM(moving.points, moving.kind === 'area')) +
          (moving.kind === 'area' ? ' round' : ' long'));
        return;
      }
      state.doc.items[state.handle.index] = state.handle.which === 'turn'
        ? turnTo(state.handle.start, spot, event.shiftKey)
        : resizeTo(state.handle.start, Number(state.handle.which), spot, event.shiftKey);
      render();
      return;
    }
    if (state.draft && !state.draft.press) {
      // Between clicks: a rubber band from the last corner to the pointer,
      // so you see the segment before you fix it.
      state.draft.cursor = snapCorner(state.draft, where, event.shiftKey);
      overlay.innerHTML = draftSVG();
      snapMark(event.shiftKey ? null : snapPoint(where));
      showReadout(where, draftMeasure());
      return;
    }
    if (state.draft) {
      var here = at(event);
      var draft = state.draft;
      var points = draft.points;
      if (!draft.press.moved &&
          Math.abs(event.clientX - draft.press.x) + Math.abs(event.clientY - draft.press.y) < 5) {
        return;
      }
      draft.press.moved = true;

      // Shift straightens the segment being drawn: from the last point that
      // was fixed before Shift went down, to wherever the pointer is now.
      // A drag on its own is freehand and only as straight as a hand is,
      // which a boundary or a phase line is not. Letting go of Shift carries
      // on freehand from the straight end, so one stroke can be a straight
      // leg, a curve round a hill and a straight leg again — and tapping
      // Shift again chains another one.
      if (event.shiftKey) {
        if (draft.anchor === null) {
          draft.anchor = points.length - 1;
        }
        points.length = draft.anchor + 1;
        points.push([round(here.x), round(here.y)]);
        overlay.innerHTML = draftSVG();
        return;
      }
      draft.anchor = null;

      var last = points[points.length - 1];
      // Thinned as it is drawn rather than afterwards: a pointer reports
      // every pixel it passes and the document takes MAX_POINTS of them, so
      // an unthinned stroke would hit the cap within one gesture. The step
      // is a fraction of what is on screen, so it thins by how far the line
      // actually looks, not by how far zoomed in it happens to be.
      var step = view.w / 90;
      if (Math.hypot(here.x - last[0], here.y - last[1]) < step) return;
      if (points.length >= catalog.limits.points) return;
      points.push([round(here.x), round(here.y)]);
      overlay.innerHTML = draftSVG();
      return;
    }
    if (state.drag) {
      var point = at(event);
      var dx = point.x - state.drag.from.x;
      var dy = point.y - state.drag.from.y;
      if (!state.drag.moved && Math.abs(dx) + Math.abs(dy) < 1) return;
      if (!state.drag.moved) {
        state.drag.moved = true;
        snapshot();
      }
      state.drag.indices.forEach(function (index, n) {
        state.doc.items[index] = shifted(JSON.parse(state.drag.starts[n]), dx, dy);
      });
      render();
      return;
    }
    if (state.marquee) {
      state.marquee.to = [where.x, where.y];
      overlay.innerHTML = marqueeSVG();
      return;
    }
    if (state.pan) {
      var scale = view.w / svg.getBoundingClientRect().width;
      view.x = state.pan.view.x - (event.clientX - state.pan.x) * scale;
      view.y = state.pan.view.y - (event.clientY - state.pan.y) * scale;
      applyView();
    }
  });

  svg.addEventListener('pointerup', function () {
    if (state.ruler && state.ruler.live !== false) {
      // The measurement stays on screen until the next one, or Esc.
      state.ruler.live = false;
      say('Measured ' + rulerText() + ' — drag again to measure another, Esc to clear');
      return;
    }
    snapMark(null);
    if (state.box) {
      finishBox();
      return;
    }
    if (state.handle) {
      if (state.handle.moved) {
        touched();
        fillInspector();
      }
      state.handle = null;
      return;
    }
    if (state.draft) {
      var press = state.draft.press;
      state.draft.press = null;
      if (!press.moved && !state.draft.chain) {
        // A press that never moved is a click: plot corner by corner from
        // here, until Enter, a double-click — or letting go of Ctrl, if Ctrl
        // is what started it.
        state.draft.chain = true;
        say(state.draft.byCtrl
          ? 'Plotting — Ctrl+click each corner, let go of Ctrl to finish'
          : 'Plotting — click each corner; double-click or Enter to finish, Esc to cancel');
        return;
      }
      if (state.draft.chain) {
        overlay.innerHTML = draftSVG();
        return;
      }
      finishDraft();
      return;
    }
    if (state.marquee) {
      finishMarquee();
      return;
    }
    if (state.drag && state.drag.moved) touched();
    state.drag = null;
    state.pan = null;
  });

  // Letting go outside the sheet still ends the stroke; without this the
  // draft would hang around and the next click would extend it.
  svg.addEventListener('pointercancel', function () {
    if (state.draft) finishDraft();
    if (state.box) finishBox();
    state.handle = null;
  });

  function finishBox() {
    var box = state.box;
    var shape = boxGeometry(box);
    state.box = null;
    say('');
    overlay.innerHTML = '';
    // A click that never became a drag is a slipped finger, as with a line.
    if (!shape || shape.w < catalog.minShape || shape.h < catalog.minShape) {
      drawSelection();
      return;
    }
    var item = {
      kind: 'shape', shape: shape.shape, side: state.side, layer: state.layer,
      label: '', note: '', size: catalog.defaultSize, color: state.color,
      x: shape.x, y: shape.y, w: shape.w, h: shape.h, rotation: 0,
      fill: state.fill, style: state.style, weight: state.weight
    };
    if (box.ao) makeAO(item);
    add(item);
    afterDrawn(box.ao);
    if (box.ao) nameAO();
  }

  // What happens once something is drawn. By default the tool goes back to
  // Select with the new item selected, so the next click on empty ground just
  // deselects it — it used to start another drawing, which is what people
  // tripped over. A tool double-clicked in the rail stays on (locked) for
  // drawing several in a row: then the new item is left unselected, unless
  // it is an AO or a phase line still waiting for its name, and the click
  // after that one only deselects (state.afterDraw).
  function afterDrawn(needsName) {
    if (!state.toolLock) {
      setTool('select');
      return;
    }
    if (needsName) {
      state.afterDraw = true;
    } else {
      select(-1);
    }
  }

  // An AO is a plain black border with a name, cut out of the grey: drawing
  // the first one turns the mask on, which is the point of drawing one.
  function makeAO(item) {
    var count = state.doc.items.filter(function (entry) { return entry.ao; }).length;
    item.ao = true;
    item.color = nextAOColour(item);
    item.style = 'solid';
    item.fill = 'none';
    item.size = 1;
    item.label = 'AO ' + (count + 1);
    if (!state.doc.mask || !state.doc.mask.on) {
      state.doc.mask = { on: true, opacity: (state.doc.mask && state.doc.mask.opacity) || 0.55 };
      if (settings.mask) settings.mask.checked = true;
    }
  }

  // Straight into its name: the label field, selected, so typing replaces it.
  function nameAO() {
    if (!fields.label) return;
    fields.label.focus();
    fields.label.select();
    say('Name the AO — on a campaign map it becomes an AO in the planner when you save');
  }

  // A corner while plotting: where the pointer is, or with Shift held the
  // nearest 15° from the last corner — Arma's map has no snapping, and a
  // boundary drawn by hand is only as straight as the hand.
  function snapCorner(draft, point, straight) {
    var last = draft.points[draft.points.length - 1];
    if (!straight || !last) return snapPoint(point) || [round(point.x), round(point.y)];
    var dx = point.x - last[0];
    var dy = point.y - last[1];
    var length = Math.hypot(dx, dy);
    var angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 12)) * (Math.PI / 12);
    return [round(last[0] + Math.cos(angle) * length), round(last[1] + Math.sin(angle) * length)];
  }

  function finishDraft() {
    var draft = state.draft;
    state.draft = null;
    say('');
    overlay.innerHTML = '';
    if (!draft) return;
    // A stroke that went nowhere is a slipped finger, not an error worth
    // a message: there is no deliberate way to draw a two-point line any more.
    if (draft.points.length < (draft.kind === 'area' ? 3 : 2)) return;
    var item = {
      kind: draft.kind, side: state.side, layer: state.layer, label: '', note: '',
      size: catalog.defaultSize, color: state.color, points: draft.points,
      style: state.style, weight: state.weight,
      // Never automatically: an arrow says "this way", and most lines on a
      // plan are boundaries and phase lines that say no such thing. The
      // inspector puts one on the lines that mean it.
      arrow: ''
    };
    if (draft.ao) makeAO(item);
    if (draft.phase) {
      // A phase line: named at both ends, solid, as on any overlay.
      item.label_at = 'ends';
      item.label = 'PL ';
      item.style = 'solid';
    }
    add(item);
    // A Ctrl-drawn line from the Select tool is already back where it began.
    if (state.tool !== 'select') afterDrawn(draft.ao || draft.phase);
    if (draft.ao) nameAO();
    if (draft.phase) namePhaseLine();
  }

  // Straight into its name, after the "PL " already there.
  function namePhaseLine() {
    if (!fields.label) return;
    fields.label.focus();
    var end = fields.label.value.length;
    fields.label.setSelectionRange(end, end);
    say('Name the phase line — PL AMBER, PL BLUE — it is written at both ends');
  }

  /* -- toolbar -------------------------------------------------------------- */

  function setTool(tool, lock) {
    if (state.draft) finishDraft();
    state.toolLock = !!lock && tool !== 'select';
    state.afterDraw = false;
    state.box = null;
    if (state.ruler) {
      state.ruler = null;
      overlay.innerHTML = '';
      drawSelection();
    }
    state.tool = tool;
    Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
      button.classList.toggle('active', button.dataset.tool === tool);
      button.classList.toggle('locked', button.dataset.tool === tool && state.toolLock);
    });
    // `data-for` is a list, the same as in the inspector: a field can belong
    // to more than one tool. Comparing the whole attribute silently hid every
    // such field — which is what happened to the State picker, whose
    // `data-for="unit point"` matched neither.
    Array.prototype.forEach.call(app.querySelectorAll('.tmpick [data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(tool) < 0;
    });
    svg.classList.toggle('tm-drawing', tool !== 'select');
    showToolPanel();
  }

  // The side panel's first box: what the tool in hand does and draws. With
  // the Select tool and something selected, the inspector is what matters,
  // so this steps aside.
  var TOOL_HINTS = {
    select: 'Click to select, Shift+click for several, drag to move. Ctrl+click ' +
      'or Ctrl+drag draws a line from here too.',
    unit: 'Click to place the symbol.',
    point: 'Click to place the marker. The list holds every marker this server has uploaded a picture for.',
    line: 'Click each corner, double-click or Enter to finish — or drag freehand.',
    phase: 'Click each corner; the line is named at both ends.',
    area: 'Click each corner, Enter to close it — or drag freehand.',
    rect: 'Drag corner to corner; Shift for a square.',
    ellipse: 'Drag corner to corner; Shift for a circle.',
    ao: 'Draw the area of operations; everything outside it is greyed out.',
    text: 'Click to place a label.',
    measure: 'Drag to measure a distance and bearing. Nothing is drawn.'
  };

  function showToolPanel() {
    var panel = document.getElementById('tmtoolpanel');
    if (!panel) return;
    var button = app.querySelector('.tmtool[data-tool="' + state.tool + '"]');
    var title = document.getElementById('tmtooltitle');
    var hint = document.getElementById('tmtoolhint');
    if (title && button) title.textContent = button.title.replace(/\s*\(\w\)$/, '');
    if (hint) {
      var drawsOne = ['line', 'phase', 'area', 'rect', 'ellipse', 'ao'].indexOf(state.tool) >= 0;
      hint.textContent = (TOOL_HINTS[state.tool] || '') + (drawsOne
        ? (state.toolLock ? ' 🔒 Stays on — Esc to stop.'
          : ' Back to Select when done — double-click the tool to keep it on.')
        : '');
    }
    panel.hidden = state.tool === 'select' && selection().length > 0;
  }

  Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
    // One click picks a tool for one drawing; a double-click keeps it on.
    button.addEventListener('click', function () { setTool(button.dataset.tool); });
    button.addEventListener('dblclick', function () {
      setTool(button.dataset.tool, true);
      say('🔒 ' + button.title.replace(/\s*\(\w\)$/, '') +
          ' stays on — Esc or Select to stop');
    });
  });

  function swatchRow(host, read, write) {
    // A row of colour buttons, the first of which hands the item back to its
    // side's own colour. Buttons rather than a <select>, because picking a
    // colour from a list of names is exactly the thing a swatch avoids.
    // Not "the side's own" any more: a line, an area and a marker stopped
    // inheriting the side, so the empty swatch is the default ink.
    var buttons = [{ value: '', label: 'Default (black)' }].concat(
      catalog.lineColours.map(function (entry) {
        return { value: entry.value, label: entry.label };
      }));
    host.innerHTML = buttons.map(function (entry) {
      return '<button type="button" class="tmswatch' +
        (entry.value ? '' : ' tmswatch-side') + '" data-color="' +
        attr(entry.value) + '" title="' + attr(entry.label) + '"' +
        (entry.value ? ' style="background:' + attr(entry.value) + '"' : '') +
        '><span class="visually-hidden">' + esc(entry.label) + '</span></button>';
    }).join('');
    host.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('.tmswatch') : null;
      if (!button) return;
      event.preventDefault();
      write(button.dataset.color);
      markSwatches(host, read());
    });
    markSwatches(host, read());
  }

  function markSwatches(host, value) {
    Array.prototype.forEach.call(host.querySelectorAll('.tmswatch'), function (b) {
      b.classList.toggle('active', b.dataset.color === (value || ''));
    });
  }

  // Only what this server has the game's picture for is offered, grouped the
  // way Arma's own marker list groups it. A value already on the map without
  // a picture stays in the list, so the inspector can show what it is.
  var NO_PICTURES = '<option value="" disabled>No pictures uploaded — see the Terrains page</option>';

  function fillMarkerOptions(select, value, keep) {
    var groups = {};
    var order = [];
    catalog.markers.forEach(function (marker) {
      if (!iconKeys[marker.key] && !(keep && marker.key === value)) return;
      var group = marker.group || 'Military';
      if (!groups[group]) {
        groups[group] = [];
        order.push(group);
      }
      groups[group].push(marker);
    });
    select.innerHTML = order.length ? order.map(function (group, index) {
      return (index ? '<option disabled>──────────</option>' : '') +
        '<optgroup label="' + attr(group) + '">' + groups[group].map(function (marker) {
          return '<option value="' + attr(marker.key) + '">' + esc(marker.label) +
            (iconKeys[marker.key] ? '' : ' (no picture)') + '</option>';
        }).join('') + '</optgroup>';
    }).join('') : NO_PICTURES;
    select.value = value;
  }

  // Civilians have their own five markers; everybody else has the rest.
  var CIV_SYMBOLS = { car: 1, ship: 1, heli: 1, plane: 1, installation: 1, unknown: 1 };

  function symbolOffered(symbol, side) {
    return side === 'civ' ? !!CIV_SYMBOLS[symbol.key] : !symbol.civ;
  }

  function fillSymbolOptions(select, side, value, keep) {
    var seen = {};
    var options = catalog.symbols.filter(function (symbol) {
      var key = symbol.classes[side];
      if (keep && symbol.key === value) return true;
      if (!symbolOffered(symbol, side) || !iconKeys[key] || seen[key]) return false;
      seen[key] = true;
      return true;
    }).map(function (symbol) {
      return '<option value="' + attr(symbol.key) + '">' + esc(symbol.label) +
        (iconKeys[symbol.classes[side]] ? '' : ' (no picture)') + '</option>';
    });
    select.innerHTML = options.length ? options.join('') : NO_PICTURES;
    select.value = value;
  }

  function firstOffered(select, value) {
    // The value if it is offered, otherwise the first that is.
    var option = Array.prototype.find.call(select.options, function (entry) {
      return !entry.disabled && entry.value;
    });
    if (!Array.prototype.some.call(select.options, function (entry) {
      return entry.value === value && !entry.disabled && !/\(no picture\)$/.test(entry.text);
    })) {
      return option ? option.value : value;
    }
    return value;
  }

  function fillOptions(select, options, value) {
    select.innerHTML = options.map(function (option) {
      return '<option value="' + attr(option.value) + '">' + esc(option.label) + '</option>';
    }).join('');
    select.value = value;
  }

  var sideOptions = catalog.sides.map(function (side) {
    return { value: side.key, label: side.label };
  });
  var glyphOptions = [{ value: '', label: 'No text' }].concat(
    catalog.points.map(function (preset) {
      return { value: preset.glyph, label: preset.glyph + ' · ' + preset.label };
    }));
  function echelonOptions(value) {
    return [{ value: '', label: 'No size' }].concat(
      catalog.echelons.filter(function (entry) {
        return iconKeys[entry.picture] || entry.key === value;
      }).map(function (entry) {
        return { value: entry.key, label: entry.label };
      }));
  }

  var sidePick = document.getElementById('tmside');
  var symbolPick = document.getElementById('tmsymbol');
  var glyphPick = document.getElementById('tmglyph');
  var markerPick = document.getElementById('tmmarker');
  var echelonPick = document.getElementById('tmechelon');

  fillOptions(sidePick, sideOptions, state.side);
  fillSymbolOptions(symbolPick, state.side, state.symbol);
  state.symbol = firstOffered(symbolPick, state.symbol);
  symbolPick.value = state.symbol;
  fillOptions(glyphPick, glyphOptions, state.glyph);
  fillMarkerOptions(markerPick, state.marker);
  state.marker = firstOffered(markerPick, state.marker);
  markerPick.value = state.marker;
  fillOptions(echelonPick, echelonOptions(''), state.echelon);

  var layerPick = document.getElementById('tmlayerpick');
  var layerAdd = document.getElementById('tmlayeradd');
  var layerName = document.getElementById('tmlayernew');

  function slug(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '').slice(0, 24);
  }

  function fillLayerPickers() {
    var options = layers().map(function (entry) {
      return { value: entry.id, label: entry.name };
    });
    fillOptions(layerPick, options, state.layer);
    fillOptions(fields.layer, options, state.layer);
    var item = selected();
    if (item) fields.layer.value = item.layer;
  }

  function addLayer(name) {
    var list = layers();
    if (list.length >= catalog.limits.layers) {
      say('That is as many layers as one map holds.', 'err');
      return;
    }
    var base = slug(name) || 'layer';
    // A layer somebody adds must never be mistaken for one of the planner's.
    if (plannerLayer(base)) base = 'l-' + base;
    var id = base;
    var suffix = 2;
    while (list.some(function (entry) { return entry.id === id; })) {
      id = base + '-' + (suffix++);
    }
    snapshot();
    list.push({ id: id, name: (name || base).slice(0, catalog.limits.layerName),
                visible: true });
    state.layer = id;
    touched();
    drawLayers();
    fillLayerPickers();
  }

  if (layerAdd) {
    layerAdd.addEventListener('click', function () {
      addLayer(layerName.value.trim());
      layerName.value = '';
    });
  }
  layerPick.addEventListener('change', function () { state.layer = layerPick.value; });

  sidePick.addEventListener('change', function () {
    state.side = sidePick.value;
    // A side has its own set of markers, so the symbols follow it.
    fillSymbolOptions(symbolPick, state.side, state.symbol);
    state.symbol = firstOffered(symbolPick, state.symbol);
    symbolPick.value = state.symbol;
  });
  symbolPick.addEventListener('change', function () { state.symbol = symbolPick.value; });
  glyphPick.addEventListener('change', function () { state.glyph = glyphPick.value; });
  markerPick.addEventListener('change', function () { state.marker = markerPick.value; });
  echelonPick.addEventListener('change', function () { state.echelon = echelonPick.value; });

  var colourRow = document.getElementById('tmcolours');
  if (colourRow) {
    swatchRow(colourRow,
      function () { return state.color; },
      function (value) { state.color = value; });
  }
  var aoPick = document.getElementById('tmaoform');
  if (aoPick) {
    aoPick.value = state.aoForm;
    aoPick.addEventListener('change', function () { state.aoForm = aoPick.value; });
  }
  var fillPick = document.getElementById('tmfill');
  if (fillPick) {
    fillOptions(fillPick, catalog.fills.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }), state.fill);
    fillPick.addEventListener('change', function () { state.fill = fillPick.value; });
  }
  var styleOptions = catalog.lineStyles.map(function (key) {
    return { value: key, label: catalog.lineStyleLabels[key] || key };
  });
  var weightOptions = catalog.lineWeights.map(function (value) {
    return { value: String(value), label: value + ' px' };
  });
  var stylePick = document.getElementById('tmstyle');
  if (stylePick) {
    fillOptions(stylePick, styleOptions, state.style);
    stylePick.addEventListener('change', function () { state.style = stylePick.value; });
  }
  var weightPick = document.getElementById('tmweight');
  if (weightPick) {
    fillOptions(weightPick, weightOptions, String(state.weight));
    weightPick.addEventListener('change', function () {
      state.weight = Number(weightPick.value) || catalog.defaultWeight;
    });
  }

  /* -- the inspector -------------------------------------------------------- */

  var fields = {
    label: document.getElementById('tmf-label'),
    note: document.getElementById('tmf-note'),
    side: document.getElementById('tmf-side'),
    symbol: document.getElementById('tmf-symbol'),
    glyph: document.getElementById('tmf-glyph'),
    marker: document.getElementById('tmf-marker'),
    echelon: document.getElementById('tmf-echelon'),
    rotation: document.getElementById('tmf-rotation'),
    size: document.getElementById('tmf-size'),
    style: document.getElementById('tmf-style'),
    arrow: document.getElementById('tmf-arrow'),
    weight: document.getElementById('tmf-weight'),
    labelAt: document.getElementById('tmf-labelat'),
    layer: document.getElementById('tmf-layer'),
    color: document.getElementById('tmf-color'),
    figure: document.getElementById('tmf-figure'),
    fill: document.getElementById('tmf-fill'),
    ao: document.getElementById('tmf-ao')
  };
  fillOptions(fields.figure, catalog.shapes.map(function (entry) {
    return { value: entry.key, label: entry.label };
  }), 'rect');
  fillOptions(fields.fill, catalog.fills.map(function (entry) {
    return { value: entry.key, label: entry.label };
  }), catalog.defaultFill);
  fillOptions(fields.side, sideOptions, state.side);
  fillOptions(fields.style, styleOptions, 'solid');
  fillOptions(fields.weight, weightOptions, String(catalog.defaultWeight));
  fillOptions(fields.arrow, catalog.arrows.map(function (entry) {
    return { value: entry.key, label: entry.label };
  }), '');
  fillOptions(fields.labelAt, catalog.labelPlaces.map(function (entry) {
    return { value: entry.key, label: entry.label };
  }), 'middle');

  function fillInspector() {
    showToolPanel();
    var item = selected();
    var many = selection().length > 1;
    inspector.hidden = !item || many;
    if (many) {
      say(selection().length + ' selected — drag to move them together, Del deletes, ' +
          'Ctrl+C copies, Ctrl+D duplicates');
    }
    if (!item || many) return;
    Array.prototype.forEach.call(inspector.querySelectorAll('[data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(item.kind) < 0;
    });
    fields.label.value = item.label || '';
    fields.note.value = item.note || '';
    fields.side.value = item.side;
    // Each list offers what this server has pictures for, plus whatever the
    // selected item already is.
    fillSymbolOptions(fields.symbol, item.side, item.symbol || 'inf', true);
    fields.glyph.value = item.glyph || '';
    fillMarkerOptions(fields.marker, markerKey(item), true);
    fillOptions(fields.echelon, echelonOptions(item.echelon || ''), item.echelon || '');
    fields.rotation.value = item.rotation || 0;
    fields.size.value = item.size;
    fields.style.value = item.style || 'solid';
    fields.arrow.value = item.arrow === true ? 'end' : (item.arrow || '');
    var weight = String(weightOf(item));
    // A weight off the list (a hand-made document) still shows what it is.
    if (!fields.weight.querySelector('option[value="' + weight + '"]')) {
      fields.weight.insertAdjacentHTML('beforeend',
        '<option value="' + weight + '">' + weight + ' px</option>');
    }
    fields.weight.value = weight;
    fields.labelAt.value = item.label_at || 'middle';
    fields.layer.value = item.layer;
    fields.color.value = item.color || colours[item.side].fill;
    fields.figure.value = item.shape || 'rect';
    fields.fill.value = item.fill || catalog.defaultFill;
    fields.ao.checked = !!item.ao;
    if (inspectorColours) markSwatches(inspectorColours, item.color);
    fillMeasure(item);
  }

  // How big the selected thing is on the ground, from the sheet's corners.
  function fillMeasure(item) {
    var host = document.getElementById('tmf-measure');
    if (!host) return;
    var text = '';
    if (item.kind === 'line') {
      text = 'Length ' + metres(lengthM(item.points));
    } else if (item.kind === 'area') {
      var scale = metresPerUnit();
      var sum = 0;
      item.points.forEach(function (point, index) {
        var next = item.points[(index + 1) % item.points.length];
        sum += point[0] * scale[0] * next[1] * scale[1] - next[0] * scale[0] * point[1] * scale[1];
      });
      text = 'Perimeter ' + metres(lengthM(item.points, true)) + ' · ' + hectares(Math.abs(sum) / 2);
    } else if (item.kind === 'shape') {
      var size = metresPerUnit();
      var w = item.w * size[0];
      var h = item.h * size[1];
      var area = item.shape === 'ellipse' ? Math.PI * w * h / 4 : w * h;
      text = metres(w) + ' × ' + metres(h) + ' · ' + hectares(area);
    } else if (item.x !== undefined) {
      text = 'Grid ' + gridRef(item.x, item.y);
    }
    host.textContent = text;
    host.hidden = !text;
  }

  function hectares(square) {
    if (square >= 1e6) return (square / 1e6).toFixed(2) + ' km²';
    return (square / 1e4).toFixed(square >= 1e5 ? 0 : 1) + ' ha';
  }

  function editField(field, read) {
    field.addEventListener('input', function () {
      var item = selected();
      if (!item) return;
      read(item);
      touched();
      render();
    });
  }

  editField(fields.label, function (item) { item.label = fields.label.value; });
  editField(fields.note, function (item) { item.note = fields.note.value; });
  editField(fields.side, function (item) {
    item.side = fields.side.value;
    if (item.kind === 'unit') {
      fillSymbolOptions(fields.symbol, item.side, item.symbol, true);
      item.symbol = firstOffered(fields.symbol, item.symbol);
      fields.symbol.value = item.symbol;
    }
  });
  editField(fields.symbol, function (item) { item.symbol = fields.symbol.value; });
  fields.echelon.addEventListener('change', function () {
    var item = selected();
    if (!item) return;
    item.echelon = fields.echelon.value;
    touched();
    render();
  });

  fields.marker.addEventListener('change', function () {
    var item = selected();
    if (!item) return;
    item.marker = fields.marker.value;
    touched();
    render();
  });

  editField(fields.glyph, function (item) {
    item.glyph = fields.glyph.value.toUpperCase().slice(0, catalog.limits.glyph);
    fields.glyph.value = item.glyph;
  });
  editField(fields.rotation, function (item) { item.rotation = Number(fields.rotation.value); });
  editField(fields.size, function (item) { item.size = Number(fields.size.value); });
  editField(fields.style, function (item) { item.style = fields.style.value; });
  editField(fields.arrow, function (item) { item.arrow = fields.arrow.value; });
  editField(fields.weight, function (item) { item.weight = Number(fields.weight.value); });
  editField(fields.labelAt, function (item) { item.label_at = fields.labelAt.value; });
  editField(fields.layer, function (item) { item.layer = fields.layer.value; });
  editField(fields.color, function (item) { item.color = fields.color.value; });
  editField(fields.figure, function (item) { item.shape = fields.figure.value; });
  editField(fields.fill, function (item) { item.fill = fields.fill.value; });
  editField(fields.ao, function (item) {
    item.ao = fields.ao.checked;
    // A colour somebody picked stays theirs; an unpicked one gets a colour
    // no other AO is using, as a drawn AO does.
    if (item.ao && !item.color) item.color = nextAOColour(item);
    if (item.ao && (!state.doc.mask || !state.doc.mask.on)) {
      state.doc.mask = { on: true, opacity: (state.doc.mask && state.doc.mask.opacity) || 0.55 };
      if (settings.mask) settings.mask.checked = true;
    }
  });
  var inspectorColours = document.getElementById('tmf-colours');
  if (inspectorColours) {
    swatchRow(inspectorColours,
      function () { var item = selected(); return item ? item.color : ''; },
      function (value) {
        var item = selected();
        if (!item) return;
        item.color = value;
        fields.color.value = value || colours[item.side].fill;
        touched();
        render();
      });
  }

  document.getElementById('tmf-delete').addEventListener('click', removeSelected);
  document.getElementById('tmf-front').addEventListener('click', function () {
    var item = selected();
    if (!item) return;
    snapshot();
    state.doc.items.splice(state.selected, 1);
    state.doc.items.push(item);
    touched();
    select(state.doc.items.length - 1);
  });

  function removeSelected() {
    var picked = selection().sort(function (a, b) { return b - a; });
    if (!picked.length) return;
    snapshot();
    picked.forEach(function (index) { state.doc.items.splice(index, 1); });
    touched();
    select(-1);
  }

  function shifted(item, dx, dy) {
    if (item.points) {
      item.points = item.points.map(function (corner) {
        return [round(corner[0] + dx), round(corner[1] + dy)];
      });
    } else {
      item.x = round(item.x + dx);
      item.y = round(item.y + dy);
    }
    return item;
  }

  function marqueeSVG() {
    var m = state.marquee;
    var x = Math.min(m.from[0], m.to[0]);
    var y = Math.min(m.from[1], m.to[1]);
    return '<rect class="tm-marquee" x="' + round(x) + '" y="' + round(y) + '" width="' +
      round(Math.abs(m.to[0] - m.from[0])) + '" height="' +
      round(Math.abs(m.to[1] - m.from[1])) + '" pointer-events="none"/>';
  }

  function itemAnchors(item) {
    if (item.points) return item.points;
    if (item.kind === 'shape') return [[item.x, item.y]];
    return [[item.x, item.y]];
  }

  function finishMarquee() {
    var m = state.marquee;
    state.marquee = null;
    overlay.innerHTML = '';
    var x1 = Math.min(m.from[0], m.to[0]);
    var x2 = Math.max(m.from[0], m.to[0]);
    var y1 = Math.min(m.from[1], m.to[1]);
    var y2 = Math.max(m.from[1], m.to[1]);
    var hidden = hiddenLayers();
    var picked = selection();
    state.doc.items.forEach(function (item, index) {
      if (hidden[item.layer] || picked.indexOf(index) >= 0 || isLocked(item)) return;
      // Inside means wholly inside: every corner of a line, the centre of
      // anything else.
      var inside = itemAnchors(item).every(function (p) {
        return p[0] >= x1 && p[0] <= x2 && p[1] >= y1 && p[1] <= y2;
      });
      if (inside) picked.push(index);
    });
    state.selected = picked.length ? picked[picked.length - 1] : -1;
    state.extra = picked.slice(0, -1);
    render();
    fillInspector();
  }

  /* -- copy, paste, duplicate ------------------------------------------- */

  // Copied items are kept in the browser too, so a plan copied on one map
  // pastes into another.
  var clipboard = null;

  function copySelection() {
    var picked = selection().slice().sort(function (a, b) { return a - b; });
    if (!picked.length) return false;
    clipboard = picked.map(function (index) { return JSON.parse(JSON.stringify(state.doc.items[index])); });
    try { window.localStorage.setItem('tmclip', JSON.stringify(clipboard)); } catch (e) { /* none */ }
    say(clipboard.length === 1 ? 'Copied' : 'Copied ' + clipboard.length + ' items', 'ok');
    return true;
  }

  function readClipboard() {
    try {
      var stored = JSON.parse(window.localStorage.getItem('tmclip') || 'null');
      if (Array.isArray(stored)) return stored;
    } catch (e) { /* none */ }
    return clipboard;
  }

  var lastPointer = null;

  function pasteItems(items, offset) {
    if (!items || !items.length) return;
    if (state.doc.items.length + items.length > catalog.limits.items) {
      say('That would be more than one map holds (' + catalog.limits.items + ' items).', 'err');
      return;
    }
    // Where it lands: under the pointer when it is over the map, otherwise a
    // step down and right of the original so the copy is visibly a copy.
    var xs = [];
    var ys = [];
    items.forEach(function (item) {
      itemAnchors(item).forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
    });
    var cx = (Math.min.apply(null, xs) + Math.max.apply(null, xs)) / 2;
    var cy = (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
    var dx = offset;
    var dy = offset;
    if (lastPointer && offset === null) {
      dx = lastPointer.x - cx;
      dy = lastPointer.y - cy;
    } else if (offset === null) {
      dx = dy = view.w / 40;
    }
    var known = {};
    layers().forEach(function (entry) { known[entry.id] = true; });
    snapshot();
    var first = state.doc.items.length;
    items.forEach(function (item) {
      var copy = shifted(JSON.parse(JSON.stringify(item)), dx, dy);
      // An AO copied is a new AO on the planner's side; a plain copy should
      // not quietly become one.
      delete copy.ao;
      if (!known[copy.layer] || plannerLayer(copy.layer)) copy.layer = state.layer;
      state.doc.items.push(copy);
    });
    touched();
    var added = [];
    for (var i = first; i < state.doc.items.length; i++) added.push(i);
    state.selected = added[added.length - 1];
    state.extra = added.slice(0, -1);
    render();
    fillInspector();
  }

  /* -- map settings --------------------------------------------------------- */

  var settings = {
    bg: document.getElementById('tmf-bg'),
    opacity: document.getElementById('tmf-bgop'),
    grid: document.getElementById('tmf-grid'),
    places: document.getElementById('tmf-places'),
    placeScale: document.getElementById('tmf-placescale'),
    // A container, listened to for the group checkboxes bubbling out of it.
    placeGroups: document.getElementById('tmf-placegroups'),
    cols: document.getElementById('tmf-cols'),
    rows: document.getElementById('tmf-rows'),
    shape: document.getElementById('tmf-shape'),
    zoom: document.getElementById('tmf-zoom'),
    terrain: document.getElementById('tmf-terrain'),
    left: document.getElementById('tmf-left'),
    right: document.getElementById('tmf-right'),
    bottom: document.getElementById('tmf-bottom'),
    top: document.getElementById('tmf-top'),
    mask: document.getElementById('tmf-mask'),
    maskOpacity: document.getElementById('tmf-maskop'),
    textScale: document.getElementById('tmf-textscale')
  };
  if (!state.doc.mask) state.doc.mask = { on: false, opacity: 0.55 };
  settings.mask.checked = !!state.doc.mask.on;
  settings.maskOpacity.value = state.doc.mask.opacity;
  settings.textScale.value = textScale();
  var textScaleOut = document.getElementById('tmf-textscaleout');
  function showTextScale() {
    if (textScaleOut) textScaleOut.textContent = Math.round(textScale() * 100) + '%';
  }
  showTextScale();
  var tiled = state.doc.background.kind === 'tiles';
  settings.bg.value = tiled ? '' : (state.doc.background.url || '');
  settings.zoom.value = state.doc.background.zoom;
  settings.zoom.max = state.doc.background.max_zoom;
  document.getElementById('tmf-tiles').value = state.doc.background.url || '';
  Array.prototype.forEach.call(app.querySelectorAll('[data-bg]'), function (field) {
    field.hidden = (field.dataset.bg === 'tiles') !== tiled;
  });
  settings.opacity.value = state.doc.background.opacity;
  settings.grid.checked = !!state.doc.grid.show;

  // The place-name controls. A terrain with no names has nothing to switch, so
  // the whole block says why instead of offering dead checkboxes.
  if (!state.doc.places) state.doc.places = { show: true, groups: [], scale: 1 };
  settings.places.checked = !!state.doc.places.show;
  settings.placeScale.value = state.doc.places.scale || 1;
  var placeBox = document.getElementById('tmf-placebox');
  var placeNote = document.getElementById('tmf-placenote');
  if (!places.length) {
    settings.places.disabled = true;
    placeBox.hidden = true;
    // A map on an OCAP server has no terrain stored here to hang names off, so
    // pointing at the Terrains page would be an instruction that leads nowhere.
    var stored = state.doc.background.kind === 'tiles' &&
      /^\/t\/\d+$/.test(state.doc.background.url || '');
    placeNote.textContent = stored
      ? 'This terrain has no place names yet — the Terrains page has the ' +
        'script that copies them out of a mission.'
      : 'Place names come from an uploaded terrain. This map is not on one.';
    placeNote.hidden = false;
  } else {
    var counted = {};
    places.forEach(function (place) {
      var spec = catalog.placeKinds[place.kind] || catalog.placeKinds[catalog.defaultPlaceKind];
      counted[spec.group] = (counted[spec.group] || 0) + 1;
    });
    // Ticking nothing means all of them, which is what an empty list means to
    // the renderer on both sides — so the boxes start ticked rather than
    // showing every group as off while every group is drawn.
    var chosen = state.doc.places.groups || [];
    settings.placeGroups.innerHTML = catalog.placeGroups.filter(function (group) {
      return counted[group.key];
    }).map(function (group) {
      var on = !chosen.length || chosen.indexOf(group.key) >= 0;
      return '<label class="field tmcheck"><input type="checkbox" data-placegroup="' +
        group.key + '"' + (on ? ' checked' : '') + '> <span>' + esc(group.label) +
        ' <span class="muted">' + counted[group.key] + '</span></span></label>';
    }).join('');
    placeNote.textContent = places.length + ' place names on this terrain.';
  }
  settings.cols.value = state.doc.grid.cols;
  settings.rows.value = state.doc.grid.rows;
  settings.shape.value = state.doc.width + 'x' + state.doc.height;

  fillOptions(settings.terrain, [{ value: '', label: 'Custom corners' }].concat(
    catalog.terrains.map(function (terrain) {
      return { value: terrain.name, label: terrain.name + ' · ' + terrain.size + ' m' };
    })
  ), state.doc.arma.terrain || '');
  ['left', 'right', 'bottom', 'top'].forEach(function (corner) {
    settings[corner].value = state.doc.arma[corner];
  });

  // Picking a terrain fills the corners in with its whole map, which is the
  // common case; typing in a corner afterwards is what a crop needs, and the
  // terrain then only names which map it is.
  settings.terrain.addEventListener('change', function () {
    var chosen = catalog.terrains.filter(function (terrain) {
      return terrain.name === settings.terrain.value;
    })[0];
    if (!chosen) return;
    settings.left.value = 0;
    settings.bottom.value = 0;
    settings.right.value = chosen.size;
    settings.top.value = chosen.size;
    settingsChanged();
  });

  function settingsChanged() {
    // A tile set's address comes from the OCAP import and is not typed here,
    // so only an image background reads the URL field back.
    if (state.doc.background.kind !== 'tiles') {
      state.doc.background.url = settings.bg.value.trim();
    }
    state.doc.background.zoom = Math.max(0, Math.min(
      Number(settings.zoom.value) || 0, state.doc.background.max_zoom));
    state.doc.background.opacity = Number(settings.opacity.value);
    state.doc.grid.show = settings.grid.checked;
    state.doc.mask = { on: settings.mask.checked,
                       opacity: Number(settings.maskOpacity.value) || 0.55 };
    state.doc.places.show = settings.places.checked;
    state.doc.places.scale = Number(settings.placeScale.value) || 1;
    state.doc.text_scale = Number(settings.textScale.value) || catalog.textScale['default'];
    showTextScale();
    var boxes = Array.prototype.slice.call(
      settings.placeGroups.querySelectorAll('[data-placegroup]'));
    var picked = boxes.filter(function (box) { return box.checked; })
      .map(function (box) { return box.dataset.placegroup; });
    // All ticked is the same thing as none ticked, and the empty list is what
    // survives a terrain gaining a new group of names later.
    state.doc.places.groups = picked.length === boxes.length ? [] : picked;
    state.doc.grid.cols = Math.max(1, Number(settings.cols.value) || 10);
    state.doc.grid.rows = Math.max(1, Number(settings.rows.value) || 10);
    state.doc.arma.terrain = settings.terrain.value;
    ['left', 'right', 'bottom', 'top'].forEach(function (corner) {
      state.doc.arma[corner] = Number(settings[corner].value);
    });
    var shape = settings.shape.value.split('x').map(Number);
    var resized = shape[0] !== state.doc.width || shape[1] !== state.doc.height;
    state.doc.width = shape[0];
    state.doc.height = shape[1];
    touched();
    render();
    if (resized) resetView();
  }

  Object.keys(settings).forEach(function (key) {
    settings[key].addEventListener('input', settingsChanged);
    settings[key].addEventListener('change', settingsChanged);
  });

  /* -- the bar's menus, the whole window, zoom ------------------------------- */

  // The menus are <details>, so they open without any of this; this only
  // keeps one open at a time and closes it on a click anywhere else.
  var menus = Array.prototype.slice.call(document.querySelectorAll('.tmmenu'));
  menus.forEach(function (menu) {
    menu.addEventListener('toggle', function () {
      if (!menu.open) return;
      menus.forEach(function (other) { if (other !== menu) other.open = false; });
    });
  });
  document.addEventListener('pointerdown', function (event) {
    menus.forEach(function (menu) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  });

  var focusButton = document.getElementById('tmfocus');
  // The page column is centred with a transform, which makes it the box a
  // fixed element is placed in — so for the whole window the editor moves
  // to the body, and back to where it was afterwards.
  var focusHome = null;
  function setFocus(on) {
    if (on && !focusHome) {
      focusHome = document.createComment('tmapp');
      app.parentNode.insertBefore(focusHome, app);
      document.body.appendChild(app);
    } else if (!on && focusHome) {
      focusHome.parentNode.insertBefore(app, focusHome);
      focusHome.remove();
      focusHome = null;
    }
    app.classList.toggle('tm-focus', on);
    document.body.classList.toggle('tm-focused', on);
    if (focusButton) focusButton.textContent = on ? '✕ Exit' : '⤢ Whole window';
    try { window.localStorage.setItem('tmfocus', on ? '1' : '0'); } catch (e) { /* none */ }
  }
  if (focusButton) {
    focusButton.addEventListener('click', function () {
      setFocus(!app.classList.contains('tm-focus'));
    });
    try { if (window.localStorage.getItem('tmfocus') === '1') setFocus(true); } catch (e) { /* none */ }
  }

  function zoomBy(factor) {
    var cx = view.x + view.w / 2;
    var cy = view.y + view.h / 2;
    var width = Math.min(Math.max(view.w * factor, state.doc.width / 20), state.doc.width * 3);
    var scale = width / view.w;
    view.w = width;
    view.h = view.h * scale;
    view.x = cx - view.w / 2;
    view.y = cy - view.h / 2;
    applyView();
    drawSelection();
  }
  [['tmzoomin', 1 / 1.4], ['tmzoomout', 1.4]].forEach(function (pair) {
    var button = document.getElementById(pair[0]);
    if (button) button.addEventListener('click', function () { zoomBy(pair[1]); });
  });
  var fitButton = document.getElementById('tmzoomfit');
  if (fitButton) fitButton.addEventListener('click', function () { resetView(); drawSelection(); });

  /* -- saving --------------------------------------------------------------- */

  var saving = false;
  // The revision of the map this editor is drawing on. A save says which one
  // it was made on, and the server refuses it when somebody else has saved
  // since — rather than quietly putting the older plan back over theirs.
  var rev = app.dataset.rev === '' || app.dataset.rev === undefined
    ? null : Number(app.dataset.rev);

  function conflict(message) {
    // Autosave stops here: the person has to choose.
    state.blocked = true;
    say(message + ' ', 'err');
    var reload = document.createElement('button');
    reload.type = 'button';
    reload.className = 'linkish';
    reload.textContent = 'Reload theirs';
    reload.addEventListener('click', function () {
      state.dirty = false;
      window.location.reload();
    });
    var mine = document.createElement('button');
    mine.type = 'button';
    mine.className = 'linkish';
    mine.textContent = 'Save mine over it';
    mine.addEventListener('click', function () { save(true); });
    status.appendChild(reload);
    status.appendChild(document.createTextNode(' · '));
    status.appendChild(mine);
  }

  function save(force) {
    if (saving) return;
    saving = true;
    var editsAtSend = state.edits || 0;
    say('Saving…');
    fetch(app.dataset.save, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csrf: app.dataset.csrf, doc: state.doc, rev: rev,
                             force: force === true })
    }).then(function (response) {
      return response.json().catch(function () {
        // A refusal comes back as an HTML page — a stale session or a CSRF
        // token from before a redeploy, both of which a reload fixes.
        return { ok: false, error: 'The server refused the save (' +
          response.status + '). Reload the page and try again.' };
      });
    }).then(function (answer) {
      saving = false;
      if (!answer.ok) {
        if (answer.conflict) conflict(answer.error);
        else say(answer.error || 'That did not save.', 'err');
        return;
      }
      if (typeof answer.rev === 'number') rev = answer.rev;
      state.blocked = false;
      var editedSince = (state.edits || 0) !== editsAtSend;
      if (editedSince) {
        // Something was drawn while the save was out. Replacing the
        // document with the server's would throw that away, so keep what is
        // on screen and leave it unsaved: the next save brings the planner's
        // changes back in (an AO outline finds its way home by its name).
        say('Saved — and you have changed it since. Save again to keep that.', 'warn');
        scheduleAutosave();
        return;
      }
      state.dirty = false;
      if (answer.doc) {
        // A campaign map comes back changed: an AO drawn on it is now one of
        // the planner's, on a layer of its own.
        state.doc = answer.doc;
        state.selected = -1;
        refreshLayers();
        render();
        fillInspector();
      }
      say((answer.notes && answer.notes.length) ? answer.notes.join(' ') : 'Saved', 'ok');
    }).catch(function () {
      saving = false;
      say('Could not reach the server — your drawing is still here, try again.', 'err');
    });
  }

  document.getElementById('tmsave').addEventListener('click', function () { save(); });
  document.getElementById('tmundo').addEventListener('click', undo);

  function undo() {
    var previous = state.undo.pop();
    if (!previous) {
      say('Nothing left to undo.');
      return;
    }
    state.doc = JSON.parse(previous);
    state.selected = -1;
    touched();
    refreshLayers();
    render();
    fillInspector();
  }

  window.addEventListener('beforeunload', function (event) {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  /* -- keyboard ------------------------------------------------------------- */

  var SHORTCUTS = { v: 'select', u: 'unit', m: 'point', l: 'line', a: 'area', t: 'text',
                    r: 'rect', e: 'ellipse', o: 'ao', d: 'measure', p: 'phase' };

  // Letting go of Shift fixes the straight segment where it is. Clearing the
  // anchor on the next freehand move is not enough: tapping Shift again
  // without moving in between left the old anchor standing, so the next
  // straight segment replaced the last one instead of chaining off it — five
  // corners came out as two points. That is the rhythm somebody drawing a
  // boundary actually uses, so it is the one that has to work.
  document.addEventListener('keyup', function (event) {
    if (event.key === 'Shift' && state.draft) state.draft.anchor = null;
    // Letting go of Ctrl ends a line plotted with Ctrl+click, as in Arma.
    if ((event.key === 'Control' || event.key === 'Meta') && state.draft &&
        state.draft.chain && state.draft.byCtrl && !state.draft.press) {
      finishDraft();
    }
  });

  document.addEventListener('keydown', function (event) {
    var tag = (event.target.tagName || '').toLowerCase();
    var typing = tag === 'input' || tag === 'textarea' || tag === 'select';

    // Saving works from anywhere, including mid-word in the label field, which
    // is exactly where somebody reaches for it. Undo does not: inside a field
    // it has to stay the browser's own undo.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      save();
      return;
    }
    // A draft finishes or is thrown away even while one of the toolbar's
    // dropdowns has the focus — that is where it is after picking a style.
    if (state.draft && tag === 'select' && (event.key === 'Enter' || event.key === 'Escape')) {
      event.preventDefault();
      if (event.key === 'Enter' && state.draft.chain) finishDraft();
      else { state.draft = null; overlay.innerHTML = ''; say(''); }
      event.target.blur();
      return;
    }
    if (typing) return;

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      undo();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && !state.draft) {
      var key = event.key.toLowerCase();
      if (key === 'c' && copySelection()) {
        event.preventDefault();
        return;
      }
      if (key === 'v' && editable) {
        event.preventDefault();
        pasteItems(readClipboard(), null);
        return;
      }
      if (key === 'd' && selection().length) {
        event.preventDefault();
        pasteItems(selection().sort(function (a, b) { return a - b; }).map(function (index) {
          return state.doc.items[index];
        }), view.w / 40);
        return;
      }
      if (key === 'a') {
        event.preventDefault();
        var all = [];
        var hidden = hiddenLayers();
        state.doc.items.forEach(function (item, index) {
          if (!hidden[item.layer] && !isLocked(item)) all.push(index);
        });
        state.selected = all.length ? all[all.length - 1] : -1;
        state.extra = all.slice(0, -1);
        render();
        fillInspector();
        return;
      }
    }
    if (event.key === 'Escape' && state.ruler) {
      state.ruler = null;
      overlay.innerHTML = '';
      say('');
      drawSelection();
      return;
    }
    if (event.key === 'Escape' && state.box) {
      state.box = null;
      overlay.innerHTML = '';
      say('');
      drawSelection();
      return;
    }
    if (event.key === 'Enter' && state.draft && state.draft.chain) {
      event.preventDefault();
      finishDraft();
      return;
    }
    if (event.key === 'Escape' && state.draft) {
      // Before the modifier guard below: a ctrl-drag is cancelled with the
      // key still held down, so a draft would otherwise be unescapable.
      state.draft = null;
      overlay.innerHTML = '';
      say('');
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === 'Escape') {
      // With nothing selected, Esc puts a drawing tool down and goes back to
      // Select, so a locked tool is never a trap.
      if (state.selected < 0 && state.tool !== 'select') {
        setTool('select');
        say('');
        return;
      }
      if (state.selected < 0 && app.classList.contains('tm-focus')) {
        setFocus(false);
        return;
      }
      state.draft = null;
      overlay.innerHTML = '';
      say('');
      select(-1);
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      if (state.selected >= 0) {
        event.preventDefault();
        removeSelected();
      }
    } else if (SHORTCUTS[event.key.toLowerCase()]) {
      setTool(SHORTCUTS[event.key.toLowerCase()]);
    }
  });

  fields.size.min = catalog.limits.minSize;
  fields.size.max = catalog.limits.maxSize;

  state.layer = layers()[0].id;
  drawLayers();
  fillLayerPickers();
  setTool('select');
  render();
  say('');
}());
