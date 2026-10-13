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
    glyph: catalog.points.length ? catalog.points[0].glyph : 'OBJ',
    marker: catalog.defaultMarker,
    echelon: '',
    dimension: catalog.defaultDimension,
    status: catalog.defaultStatus,
    layer: '',
    // What a line, an area or a marker is painted when it is drawn.
    // '' means the default ink — it used to mean the side's colour, which
    // is what made every friendly plan come out one flat blue.
    color: '',
    style: 'solid',
    // How a new rectangle or ellipse is filled.
    fill: catalog.defaultFill,
    // What the AO tool draws: freehand, a rectangle or an ellipse.
    aoForm: 'rect',
    hq: false,
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

  var colours = {};
  catalog.sides.forEach(function (side) { colours[side.key] = side; });
  var symbols = {};
  catalog.symbols.forEach(function (symbol) { symbols[symbol.key] = symbol; });
  var markers = {};
  catalog.markers.forEach(function (marker) { markers[marker.key] = marker; });
  var echelons = {};
  catalog.echelons.forEach(function (entry) { echelons[entry.key] = entry; });
  var strengths = {};
  catalog.strengths.forEach(function (entry) { strengths[entry.key] = entry; });
  var mobility = {};
  catalog.mobility.forEach(function (entry) { mobility[entry.key] = entry; });
  var statuses = {};
  catalog.statuses.forEach(function (entry) { statuses[entry.key] = entry; });

  function dimensionOf(item) {
    // Which frame a symbol is actually drawn in. A document written by a
    // newer version can name a dimension this one has not got, and the `use`
    // and the geometry have to agree on the fallback or the label would be
    // measured off a frame that is not on the page.
    var shapes = catalog.frames[item.side] || catalog.frames.friend;
    return shapes[item.dimension] ? item.dimension : catalog.defaultDimension;
  }

  function frameOf(item) {
    // Mirrors frame_of() in utils/tacmap.py: side first, then dimension.
    var shapes = catalog.frames[item.side] || catalog.frames.friend;
    return shapes[dimensionOf(item)];
  }

  function dashOf(item) {
    var status = statuses[item.status] || statuses[catalog.defaultStatus];
    return status && status.dash ? ' stroke-dasharray="' + status.dash + '"' : '';
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

  function label(text, x, y, size, fill) {
    if (!text) return '';
    var font = labelSize(size);
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="middle"' +
      ' font-size="' + font + '" font-weight="600"' +
      ' font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif"' +
      ' fill="' + (fill || '#ffffff') + '" stroke="#11161c"' +
      ' stroke-width="' + round(font * 0.22) + '"' +
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
    var font = labelSize(item.size);
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
      '" font-weight="700" font-family="' + FONT + '" fill="' + contrast + '">' +
      esc(item.label) + '</text></g>';
  }

  function chromeText(text, x, y, size, paint, anchor) {
    // Mirrors _chrome_text() in utils/tacmap.py: dark over a pale outline, so
    // what sits outside the frame reads on terrain of any colour.
    return '<text x="' + round(x) + '" y="' + round(y) + '" text-anchor="' +
      (anchor || 'middle') + '" font-size="' + size + '" font-weight="700"' +
      ' font-family="' + FONT + '" fill="' + paint.glyph + '" stroke="' +
      paint.fill + '" stroke-width="' + round(size * 0.3) +
      '" paint-order="stroke" stroke-linejoin="round">' + esc(text) + '</text>';
  }

  function modifierSVG(item, paint) {
    // Mirrors _modifier_svg() in utils/tacmap.py.
    var parts = [];
    var frame = frameOf(item);
    var line = frame.top - catalog.echelonLift;
    var hasEchelon = !!echelons[item.echelon];
    if (hasEchelon) {
      var shift = 'translate(0,' + round(line - 50) + ')';
      [[paint.fill, 15], [paint.glyph, 7]].forEach(function (pass) {
        parts.push('<use href="#tmx-' + item.echelon + '" fill="none" stroke="' +
          pass[0] + '" color="' + pass[0] + '" stroke-width="' + pass[1] +
          '" stroke-linecap="round" transform="' + shift + '"/>');
      });
    }
    if (strengths[item.strength]) {
      parts.push(chromeText(strengths[item.strength].text,
        hasEchelon ? 103 : 50, line + 7, 20, paint));
    }
    if (item.higher) {
      parts.push(chromeText(item.higher, -3, line + 7, 20, paint, 'end'));
    }
    if (mobility[item.mobility]) {
      var under = 'translate(0,' + round(frame.bottom + catalog.mobilityDrop) + ')';
      [[paint.fill, 14], [paint.glyph, 6]].forEach(function (pass) {
        parts.push('<use href="#tmv-' + item.mobility + '" fill="none" stroke="' +
          pass[0] + '" color="' + pass[0] + '" stroke-width="' + pass[1] +
          '" stroke-linecap="round" stroke-linejoin="round" transform="' +
          under + '"/>');
      });
    }
    if (item.text) {
      if ((symbols[item.symbol] || {}).hasIcon) {
        parts.push(chromeText(item.text, 102, 57, 22, paint, 'start'));
      } else {
        parts.push('<text x="50" y="61" text-anchor="middle" font-size="30"' +
          ' font-weight="700" font-family="' + FONT + '" fill="' + paint.glyph +
          '">' + esc(item.text) + '</text>');
      }
    }
    return parts.join('');
  }

  function unitSVG(item) {
    var paint = colours[item.side];
    var scale = catalog.unitBox * item.size / 100;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') rotate(' +
      (item.rotation || 0) + ') scale(' + scale + ') translate(-50,-50)';
    var dimension = dimensionOf(item);
    var parts = ['<use href="#tmf-' + item.side + '-' + dimension + '" fill="' +
      paint.fill + '" stroke="' + paint.edge + '" stroke-width="5"' +
      dashOf(item) + '/>'];
    if (item.hq) {
      parts.push('<use href="#tmh-' + item.side + '-' + dimension +
        '" stroke="' + paint.edge + '" stroke-width="5"/>');
    }
    if ((symbols[item.symbol] || {}).hasIcon) {
      parts.push('<use href="#tmi-' + item.symbol + '" fill="none" stroke="' +
        paint.glyph + '" color="' + paint.glyph + '" stroke-width="7"' +
        ' stroke-linecap="round" stroke-linejoin="round"/>');
    }
    parts.push(modifierSVG(item, paint));
    var depth = frameOf(item).bottom;
    if (mobility[item.mobility]) {
      depth += catalog.mobilityDrop + catalog.mobilityDepth;
    }
    depth = Math.max(depth, item.hq ? 135 : 112);
    var drop = catalog.unitBox * item.size * (depth - 50) / 100 + 6;
    return '<g transform="' + transform + '">' + parts.join('') + '</g>' +
      label(item.label, item.x, item.y + drop, item.size);
  }

  function markerKey(item) {
    return markers[item.marker] ? item.marker : catalog.defaultMarker;
  }

  function pointSVG(item) {
    // Mirrors _point_svg() in utils/tacmap.py: line art in the marker's own
    // colour, drawn twice so the shape holds up over terrain.
    var ink = itemColour(item);
    var backing = inkBacking(ink);
    var key = markerKey(item);
    var scale = catalog.pointBox * item.size / 100 * 1.55;
    var transform = 'translate(' + round(item.x) + ',' + round(item.y) + ') scale(' +
      scale + ') translate(-50,-50)';
    var dash = dashOf(item);
    var shape = '<g transform="' + transform + '">' +
      '<use href="#tmm-' + key + '" fill="none" stroke="' + backing +
      '" color="' + backing + '" stroke-width="15"' +
      ' stroke-linecap="round" stroke-linejoin="round"' + dash + '/>' +
      '<use href="#tmm-' + key + '" fill="none" stroke="' + ink +
      '" color="' + ink + '" stroke-width="7"' +
      ' stroke-linecap="round" stroke-linejoin="round"' + dash + '/></g>';
    var parts = [shape];
    var glyph = item.glyph || '';
    var name = item.label;
    if (glyph && markers[key].text) {
      parts.push(label(glyph, item.x, item.y + labelSize(item.size) * 0.36,
        item.size * 0.9));
    } else if (glyph) {
      name = (glyph + ' ' + name).trim();
    }
    parts.push(label(name, item.x, item.y + catalog.pointBox * item.size * 0.8 + 8,
      item.size));
    return parts.join('');
  }

  function arrowHead(points, size) {
    // Mirrors arrow_head() in utils/tacmap.py — a polygon rather than a marker,
    // because a marker cannot take the colour of the line it sits on.
    var from = points[points.length - 2];
    var to = points[points.length - 1];
    var length = Math.hypot(to[0] - from[0], to[1] - from[1]);
    if (!length) return null;
    var ax = (to[0] - from[0]) / length;
    var ay = (to[1] - from[1]) / length;
    var back = 13 * size;
    var wide = 5.5 * size;
    return [
      [round(to[0] + ax * 2 * size), round(to[1] + ay * 2 * size)],
      [round(to[0] - ax * back - ay * wide), round(to[1] - ay * back + ax * wide)],
      [round(to[0] - ax * back + ay * wide), round(to[1] - ay * back - ax * wide)]
    ];
  }

  function shapeSVG(item) {
    var colour = itemColour(item);
    var backing = inkBacking(colour);
    var points = item.points.map(function (point) {
      return round(point[0]) + ',' + round(point[1]);
    }).join(' ');
    var drawn = lineSize(item.size);
    var width = round(4 * drawn);
    // Mirrors _shape_svg(): wider than the ink and always solid, so the gaps
    // of a dashed line are backed too.
    var backWidth = round(width + 2.6 * drawn);
    var dash = item.style === 'dashed'
      ? ' stroke-dasharray="' + round(14 * drawn) + ' ' + round(9 * drawn) + '"' : '';
    if (item.kind === 'area') {
      var centre = item.points.reduce(function (sum, point) {
        return [sum[0] + point[0] / item.points.length, sum[1] + point[1] / item.points.length];
      }, [0, 0]);
      return '<polygon points="' + points + '" fill="none" stroke="' + backing +
        '" stroke-width="' + backWidth + '" stroke-linejoin="round"/>' +
        '<polygon points="' + points + '" ' +
        (item.ao ? 'fill="none"' : 'fill="' + colour + '" fill-opacity="0.22"') +
        ' stroke="' + colour + '" stroke-width="' + width +
        '"' + dash + ' stroke-linejoin="round"/>' +
        (item.ao ? aoTabSVG(item) : label(item.label, centre[0], centre[1], item.size));
    }
    var parts = ['<polyline points="' + points + '" fill="none" stroke="' + backing +
      '" stroke-width="' + backWidth +
      '" stroke-linecap="round" stroke-linejoin="round"/>',
      '<polyline points="' + points + '" fill="none" stroke="' + colour +
      '" stroke-width="' + width + '"' + dash +
      ' stroke-linecap="round" stroke-linejoin="round"/>'];
    if (item.arrow) {
      var head = arrowHead(item.points, drawn);
      if (head) {
        parts.push('<polygon points="' + head.map(function (corner) {
          return corner[0] + ',' + corner[1];
        }).join(' ') + '" fill="' + colour + '"/>');
      }
    }
    if (item.label) {
      var middle = item.points[Math.floor(item.points.length / 2)];
      parts.push(label(item.label, middle[0], middle[1] - 10 * item.size, item.size));
    }
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
    var backing = inkBacking(colour);
    var drawn = lineSize(item.size);
    var width = round(4 * drawn);
    var backWidth = round(width + 2.6 * drawn);
    var dash = item.style === 'dashed'
      ? ' stroke-dasharray="' + round(14 * drawn) + ' ' + round(9 * drawn) + '"' : '';
    var a = round(item.w / 2);
    var b = round(item.h / 2);
    var outline = figureOutline(item, a, b);
    // An AO is the clear ground itself: a border and nothing in it.
    var fillKind = item.ao ? 'none' : item.fill;
    var fill = fillKind === 'light'
      ? 'fill="' + colour + '" fill-opacity="0.22"' : 'fill="none"';
    var parts = [outline + ' fill="none" stroke="' + backing + '" stroke-width="' +
      backWidth + '" stroke-linejoin="round"/>'];
    if (fillKind === 'hatch') {
      var path = hatchSegments(item.shape, 2 * a, 2 * b, catalog.hatchGap * drawn)
        .map(function (s) { return 'M' + s[0] + ',' + s[1] + 'L' + s[2] + ',' + s[3]; })
        .join('');
      if (path) {
        parts.push('<path d="' + path + '" stroke="' + backing + '" stroke-width="' +
          round(3.4 * drawn) + '" stroke-opacity="0.55"/>');
        parts.push('<path d="' + path + '" stroke="' + colour + '" stroke-width="' +
          round(1.6 * drawn) + '"/>');
      }
    }
    parts.push(outline + ' ' + fill + ' stroke="' + colour + '" stroke-width="' + width +
      '"' + dash + ' stroke-linejoin="round"/>');
    return figureTurn(item) + parts.join('') + '</g>' +
      (item.ao ? aoTabSVG(item) : label(item.label, item.x, item.y, item.size));
  }

  function itemSVG(item) {
    if (item.kind === 'shape') return figureSVG(item);
    if (item.kind === 'unit') return unitSVG(item);
    if (item.kind === 'point') return pointSVG(item);
    if (item.kind === 'text') {
      return label(item.label || ' ', item.x, item.y, item.size * 1.6, item.color);
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
    if (state.box) return boxDraftSVG();
    if (!state.draft || state.draft.points.length === 0) return '';
    var paint = colours[state.side];
    var points = state.draft.points.concat(
      state.draft.cursor ? [state.draft.cursor] : []
    ).map(function (point) { return round(point[0]) + ',' + round(point[1]); }).join(' ');
    var tag = state.draft.kind === 'area' ? 'polygon' : 'polyline';
    return '<' + tag + ' points="' + points + '" fill="none" stroke="' + paint.fill +
      '" stroke-width="4" stroke-dasharray="10 8" stroke-linecap="round"/>';
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

  function drawSelection() {
    var existing = overlay.querySelector('.tm-ring');
    if (existing) existing.remove();
    if (state.selected < 0) return;
    var group = layer.querySelector('[data-i="' + state.selected + '"]');
    if (!group) return;
    var box = group.getBBox();
    var ring = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    ring.setAttribute('class', 'tm-ring');
    ring.setAttribute('x', box.x - 6);
    ring.setAttribute('y', box.y - 6);
    ring.setAttribute('width', box.width + 12);
    ring.setAttribute('height', box.height + 12);
    overlay.appendChild(ring);
    drawHandles();
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
    if (!editable || !item || item.kind !== 'shape') return;
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

      if (editable) {
        var name = document.createElement('input');
        name.className = 'tmlayername';
        name.value = entry.name;
        name.maxLength = catalog.limits.layerName;
        name.addEventListener('input', function () {
          entry.name = name.value;
          touched();
          fillInspector();
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

  function removeLayer(index) {
    var list = layers();
    if (list.length < 2) return;
    var going = list[index];
    var keep = list[index === 0 ? 1 : 0].id;
    snapshot();
    // Never take the items with it: a layer is a switch, not a folder anybody
    // meant to throw work into.
    state.doc.items.forEach(function (item) {
      if (item.layer === going.id) item.layer = keep;
    });
    list.splice(index, 1);
    touched();
    render();
    drawLayers();
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
    status.className = 'tmstatus' + (kind ? ' ' + kind : '');
  }

  function snapshot() {
    state.undo.push(JSON.stringify(state.doc));
    if (state.undo.length > 40) state.undo.shift();
  }

  function touched() {
    state.dirty = true;
    say('Unsaved changes', 'warn');
  }

  function selected() {
    return state.selected >= 0 ? state.doc.items[state.selected] : null;
  }

  function select(index) {
    state.selected = index;
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
      item.hq = state.hq;
      item.rotation = 0;
      item.echelon = state.echelon;
      item.strength = '';
      item.text = '';
      item.higher = '';
      item.mobility = '';
      item.dimension = state.dimension;
      item.status = state.status;
    } else if (kind === 'point') {
      item.x = round(point.x);
      item.y = round(point.y);
      item.glyph = state.glyph;
      item.marker = state.marker;
      item.status = state.status;
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
    if (handle && selected()) {
      event.preventDefault();
      state.handle = { index: state.selected, which: handle.dataset.h, moved: false,
                       start: JSON.stringify(selected()) };
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

    var drawing = state.tool === 'line' || state.tool === 'area' || state.tool === 'ao';
    if (event.ctrlKey || event.metaKey || drawing) {
      event.preventDefault();
      state.draft = {
        kind: state.tool === 'area' || state.tool === 'ao' ? 'area' : 'line',
        ao: state.tool === 'ao',
        points: [[round(point.x), round(point.y)]],
        cursor: null,
        // Where the straight segment starts, while Shift is held. null when
        // it is not.
        anchor: null
      };
      overlay.innerHTML = draftSVG();
      say('Drawing — hold Shift for a straight line, let go to finish');
      svg.setPointerCapture(event.pointerId);
      return;
    }

    if (state.tool !== 'select') {
      add(newItem(state.tool, point));
      return;
    }

    if (group) {
      var index = Number(group.dataset.i);
      select(index);
      state.drag = { index: index, from: point, moved: false,
                     start: JSON.stringify(state.doc.items[index]) };
      svg.setPointerCapture(event.pointerId);
      return;
    }

    select(-1);
    state.pan = { x: event.clientX, y: event.clientY, view: { x: view.x, y: view.y } };
    svg.setPointerCapture(event.pointerId);
  });

  svg.addEventListener('pointermove', function (event) {
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
      state.doc.items[state.handle.index] = state.handle.which === 'turn'
        ? turnTo(state.handle.start, spot, event.shiftKey)
        : resizeTo(state.handle.start, Number(state.handle.which), spot, event.shiftKey);
      render();
      return;
    }
    if (state.draft) {
      var here = at(event);
      var draft = state.draft;
      var points = draft.points;

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
      var item = JSON.parse(state.drag.start);
      if (item.points) {
        item.points = item.points.map(function (corner) {
          return [round(corner[0] + dx), round(corner[1] + dy)];
        });
      } else {
        item.x = round(item.x + dx);
        item.y = round(item.y + dy);
      }
      state.doc.items[state.drag.index] = item;
      render();
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
      finishDraft();
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
      fill: state.fill, style: state.style
    };
    if (box.ao) makeAO(item);
    add(item);
    if (box.ao) nameAO();
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
      style: state.style,
      // Never automatically: an arrow says "this way", and most lines on a
      // plan are boundaries and phase lines that say no such thing. The
      // inspector puts one on the lines that mean it.
      arrow: false
    };
    if (draft.ao) makeAO(item);
    add(item);
    if (draft.ao) nameAO();
  }

  /* -- toolbar -------------------------------------------------------------- */

  function setTool(tool) {
    if (state.draft) finishDraft();
    state.box = null;
    state.tool = tool;
    Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
      button.classList.toggle('active', button.dataset.tool === tool);
    });
    // `data-for` is a list, the same as in the inspector: a field can belong
    // to more than one tool. Comparing the whole attribute silently hid every
    // such field — which is what happened to the State picker, whose
    // `data-for="unit point"` matched neither.
    Array.prototype.forEach.call(app.querySelectorAll('.tmpick [data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(tool) < 0;
    });
    svg.classList.toggle('tm-drawing', tool !== 'select');
  }

  Array.prototype.forEach.call(app.querySelectorAll('.tmtool'), function (button) {
    button.addEventListener('click', function () { setTool(button.dataset.tool); });
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

  function fillOptions(select, options, value) {
    select.innerHTML = options.map(function (option) {
      return '<option value="' + attr(option.value) + '">' + esc(option.label) + '</option>';
    }).join('');
    select.value = value;
  }

  var sideOptions = catalog.sides.map(function (side) {
    return { value: side.key, label: side.label };
  });
  var symbolOptions = catalog.symbols.map(function (symbol) {
    return { value: symbol.key, label: symbol.group + ' · ' + symbol.label };
  });
  var glyphOptions = catalog.points.map(function (preset) {
    return { value: preset.glyph, label: preset.glyph + ' · ' + preset.label };
  });
  var markerOptions = catalog.markers.map(function (marker) {
    return { value: marker.key, label: marker.label };
  });
  var echelonOptions = [{ value: '', label: 'No size' }].concat(
    catalog.echelons.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var strengthOptions = [{ value: '', label: 'As it stands' }].concat(
    catalog.strengths.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var mobilityOptions = [{ value: '', label: 'Not stated' }].concat(
    catalog.mobility.map(function (entry) {
      return { value: entry.key, label: entry.label };
    }));
  var dimensionOptions = catalog.dimensions.map(function (entry) {
    return { value: entry.key, label: entry.label };
  });
  var statusOptions = catalog.statuses.map(function (entry) {
    return { value: entry.key, label: entry.label };
  });

  var sidePick = document.getElementById('tmside');
  var symbolPick = document.getElementById('tmsymbol');
  var glyphPick = document.getElementById('tmglyph');
  var markerPick = document.getElementById('tmmarker');
  var echelonPick = document.getElementById('tmechelon');
  var hqPick = document.getElementById('tmhq');
  var dimensionPick = document.getElementById('tmdimension');
  var statusPick = document.getElementById('tmstatuspick');

  fillOptions(sidePick, sideOptions, state.side);
  fillOptions(symbolPick, symbolOptions, state.symbol);
  fillOptions(glyphPick, glyphOptions, state.glyph);
  fillOptions(markerPick, markerOptions, state.marker);
  fillOptions(echelonPick, echelonOptions, state.echelon);
  fillOptions(dimensionPick, dimensionOptions, state.dimension);
  fillOptions(statusPick, statusOptions, state.status);

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

  sidePick.addEventListener('change', function () { state.side = sidePick.value; });
  symbolPick.addEventListener('change', function () { state.symbol = symbolPick.value; });
  glyphPick.addEventListener('change', function () { state.glyph = glyphPick.value; });
  markerPick.addEventListener('change', function () { state.marker = markerPick.value; });
  echelonPick.addEventListener('change', function () { state.echelon = echelonPick.value; });
  hqPick.addEventListener('change', function () { state.hq = hqPick.checked; });
  dimensionPick.addEventListener('change', function () {
    state.dimension = dimensionPick.value;
  });
  statusPick.addEventListener('change', function () { state.status = statusPick.value; });

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
  var stylePick = document.getElementById('tmstyle');
  if (stylePick) {
    stylePick.value = state.style;
    stylePick.addEventListener('change', function () { state.style = stylePick.value; });
  }

  /* -- the inspector -------------------------------------------------------- */

  var fields = {
    label: document.getElementById('tmf-label'),
    note: document.getElementById('tmf-note'),
    side: document.getElementById('tmf-side'),
    symbol: document.getElementById('tmf-symbol'),
    hq: document.getElementById('tmf-hq'),
    glyph: document.getElementById('tmf-glyph'),
    marker: document.getElementById('tmf-marker'),
    echelon: document.getElementById('tmf-echelon'),
    strength: document.getElementById('tmf-strength'),
    mobility: document.getElementById('tmf-mobility'),
    dimension: document.getElementById('tmf-dimension'),
    status: document.getElementById('tmf-status'),
    higher: document.getElementById('tmf-higher'),
    text: document.getElementById('tmf-text'),
    rotation: document.getElementById('tmf-rotation'),
    size: document.getElementById('tmf-size'),
    style: document.getElementById('tmf-style'),
    arrow: document.getElementById('tmf-arrow'),
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
  fillOptions(fields.symbol, symbolOptions, state.symbol);
  fillOptions(fields.marker, markerOptions, state.marker);
  fillOptions(fields.echelon, echelonOptions, state.echelon);
  fillOptions(fields.strength, strengthOptions, '');
  fillOptions(fields.mobility, mobilityOptions, '');
  fillOptions(fields.dimension, dimensionOptions, catalog.defaultDimension);
  fillOptions(fields.status, statusOptions, catalog.defaultStatus);

  function fillInspector() {
    var item = selected();
    inspector.hidden = !item;
    if (!item) return;
    Array.prototype.forEach.call(inspector.querySelectorAll('[data-for]'), function (field) {
      field.hidden = field.dataset.for.split(' ').indexOf(item.kind) < 0;
    });
    fields.label.value = item.label || '';
    fields.note.value = item.note || '';
    fields.side.value = item.side;
    fields.symbol.value = item.symbol || 'inf';
    fields.hq.checked = !!item.hq;
    fields.glyph.value = item.glyph || '';
    fields.marker.value = markerKey(item);
    fields.echelon.value = item.echelon || '';
    fields.strength.value = item.strength || '';
    fields.mobility.value = item.mobility || '';
    fields.dimension.value = dimensionOf(item);
    fields.status.value = statuses[item.status] ? item.status : catalog.defaultStatus;
    fields.higher.value = item.higher || '';
    fields.text.value = item.text || '';
    fields.rotation.value = item.rotation || 0;
    fields.size.value = item.size;
    fields.style.value = item.style || 'solid';
    fields.arrow.checked = !!item.arrow;
    fields.layer.value = item.layer;
    fields.color.value = item.color || colours[item.side].fill;
    fields.figure.value = item.shape || 'rect';
    fields.fill.value = item.fill || catalog.defaultFill;
    fields.ao.checked = !!item.ao;
    if (inspectorColours) markSwatches(inspectorColours, item.color);
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
  editField(fields.side, function (item) { item.side = fields.side.value; });
  editField(fields.symbol, function (item) { item.symbol = fields.symbol.value; });
  editField(fields.hq, function (item) { item.hq = fields.hq.checked; });
  [['echelon', fields.echelon], ['strength', fields.strength],
   ['mobility', fields.mobility], ['dimension', fields.dimension],
   ['status', fields.status]].forEach(
    function (pair) {
      pair[1].addEventListener('change', function () {
        var item = selected();
        if (!item) return;
        item[pair[0]] = pair[1].value;
        touched();
        render();
      });
    });

  editField(fields.text, function (item) {
    item.text = fields.text.value.slice(0, catalog.limits.mod);
  });
  editField(fields.higher, function (item) {
    item.higher = fields.higher.value.slice(0, catalog.limits.mod);
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
  editField(fields.arrow, function (item) { item.arrow = fields.arrow.checked; });
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
    if (state.selected < 0) return;
    snapshot();
    state.doc.items.splice(state.selected, 1);
    touched();
    select(-1);
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
    maskOpacity: document.getElementById('tmf-maskop')
  };
  if (!state.doc.mask) state.doc.mask = { on: false, opacity: 0.55 };
  settings.mask.checked = !!state.doc.mask.on;
  settings.maskOpacity.value = state.doc.mask.opacity;
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

  /* -- saving --------------------------------------------------------------- */

  var saving = false;

  function save() {
    if (saving) return;
    saving = true;
    say('Saving…');
    fetch(app.dataset.save, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csrf: app.dataset.csrf, doc: state.doc })
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
        say(answer.error || 'That did not save.', 'err');
        return;
      }
      state.dirty = false;
      if (answer.doc) {
        // A campaign map comes back changed: an AO drawn on it is now one of
        // the planner's, on a layer of its own.
        state.doc = answer.doc;
        state.selected = -1;
        drawLayers();
        fillLayerPickers();
        render();
        fillInspector();
      }
      say((answer.notes && answer.notes.length) ? answer.notes.join(' ') : 'Saved', 'ok');
    }).catch(function () {
      saving = false;
      say('Could not reach the server — your drawing is still here, try again.', 'err');
    });
  }

  document.getElementById('tmsave').addEventListener('click', save);
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
                    r: 'rect', e: 'ellipse', o: 'ao' };

  // Letting go of Shift fixes the straight segment where it is. Clearing the
  // anchor on the next freehand move is not enough: tapping Shift again
  // without moving in between left the old anchor standing, so the next
  // straight segment replaced the last one instead of chaining off it — five
  // corners came out as two points. That is the rhythm somebody drawing a
  // boundary actually uses, so it is the one that has to work.
  document.addEventListener('keyup', function (event) {
    if (event.key === 'Shift' && state.draft) state.draft.anchor = null;
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
    if (typing) return;

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      undo();
      return;
    }
    if (event.key === 'Escape' && state.box) {
      state.box = null;
      overlay.innerHTML = '';
      say('');
      drawSelection();
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
