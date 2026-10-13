/* The campaign planner's mission wizard.
 *
 * The second deliberate exception to the site's no-JavaScript rule (the first
 * is tacmap.js), for the same reason: the editor is the feature. Plain
 * JavaScript, no build step, no CDN, no libraries.
 *
 * It decides nothing. Every rule — when a drop goes out, what it says, how it
 * splits, whether a step is locked, what the checks find — is worked out by
 * utils/campaign.py on the server. This file edits values, sends what changed
 * a moment after each edit (PATCH), and draws what comes back.
 *
 * Saving: edits collect in `pending` (fields) and `phasesDirty`. A quarter of
 * a second after the last keystroke the batch is sent; edits made while it is
 * in flight wait for the next batch. Inputs are never overwritten from the
 * answer — only what is derived from them (progress, preview, checks) — so a
 * reply landing mid-word never eats what somebody is typing.
 */
(function () {
  'use strict';

  var app = document.getElementById('cp-app');
  if (!app) return;

  var state = JSON.parse(document.getElementById('cp-state').textContent);
  var AOS = JSON.parse(document.getElementById('cp-aos').textContent);
  var ROLE_LABELS = JSON.parse(document.getElementById('cp-roles').textContent);
  var ROLES = ['primary', 'secondary', 'complication'];
  var MAX_PHASES = 12;

  var fields = state.fields || {};
  var phases = clone(state.phases || []);
  var images = state.images || {};
  var pending = {};
  var phasesDirty = false;
  var phaseEdits = 0;            // bumps on every phase edit, so a reply knows if it is stale
  var inFlight = false;
  var timer = null;
  var library = null;            // the objectives library, fetched the first time the picker opens
  var activeStep = null;

  var statusEl = document.getElementById('cp-status');

  /* -- small helpers ------------------------------------------------------- */

  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'text') node.textContent = value;
      else if (key === 'class') node.className = value;
      else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach(function (child) {
      if (child) node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function say(text, kind) {
    statusEl.textContent = text;
    statusEl.className = 'cpsave' + (kind ? ' ' + kind : '');
  }

  function imageUrl(id) { return app.dataset.imageUrl + id; }

  /* -- saving --------------------------------------------------------------- */

  function touch(key, value) {
    fields[key] = value;
    pending[key] = value;
    schedule();
  }

  function touchPhases() {
    phasesDirty = true;
    phaseEdits += 1;
    schedule();
  }

  function schedule() {
    say('Unsaved changes…');
    clearTimeout(timer);
    timer = setTimeout(save, 250);
  }

  function save() {
    if (inFlight) return;               // the reply sends whatever piled up meanwhile
    var sendFields = pending;
    var sendPhases = phasesDirty;
    if (!Object.keys(sendFields).length && !sendPhases) return;
    pending = {};
    phasesDirty = false;
    var editsAtSend = phaseEdits;
    var body = { fields: sendFields };
    if (sendPhases) body.phases = phases;
    inFlight = true;
    say('Saving…');

    fetch(app.dataset.api, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': app.dataset.csrf },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, error: 'The server refused the save (' + response.status +
          '). Reload the page — your session may have expired.' };
      });
    }).then(function (answer) {
      inFlight = false;
      if (!answer.ok) {
        // Put the batch back so nothing typed is lost, and stop retrying
        // until the next edit: a refusal will not fix itself.
        Object.keys(sendFields).forEach(function (k) {
          if (!(k in pending)) pending[k] = sendFields[k];
        });
        if (sendPhases) phasesDirty = true;
        say(answer.error || 'That did not save.', 'err');
        return;
      }
      if (sendPhases && editsAtSend === phaseEdits) adoptPhaseIds(answer.state.phases);
      apply(answer.state);
      if (Object.keys(pending).length || phasesDirty) {
        save();
      } else {
        var now = new Date();
        say('Saved ' + String(now.getHours()).padStart(2, '0') + ':' +
            String(now.getMinutes()).padStart(2, '0'), 'ok');
      }
    }).catch(function () {
      inFlight = false;
      Object.keys(sendFields).forEach(function (k) {
        if (!(k in pending)) pending[k] = sendFields[k];
      });
      if (sendPhases) phasesDirty = true;
      say('Could not reach the server. Your changes are still here — retrying…', 'err');
      clearTimeout(timer);
      timer = setTimeout(save, 4000);
    });
  }

  /* New phases come back with ids. They are taken over only when nothing was
     edited in the meantime, so a stale reply cannot reorder what is on screen. */
  function adoptPhaseIds(saved) {
    if (!saved || saved.length !== phases.length) return;
    saved.forEach(function (phase, i) { phases[i].id = phase.id; });
  }

  window.addEventListener('beforeunload', function (event) {
    if (inFlight || phasesDirty || Object.keys(pending).length) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  /* -- what comes back -------------------------------------------------------- */

  function apply(next) {
    state = next;
    Object.keys(next.images || {}).forEach(function (k) { images[k] = next.images[k]; });
    document.getElementById('cp-title').textContent = next.name;
    document.title = next.name + ' · Mission wizard';
    drawProgress();
    drawGates();
    drawChecks();
    drawPreview();
  }

  function drawProgress() {
    state.progress.forEach(function (p) {
      var count = app.querySelector('[data-count="' + p.key + '"]');
      var tab = app.querySelector('.cpstep[data-step="' + p.key + '"]');
      count.textContent = p.complete ? '🔒' : p.done + '/' + p.total;
      tab.classList.toggle('complete', p.complete);
    });
  }

  function drawGates() {
    var gates = {};
    state.schedule.forEach(function (item) { if (item.kind === 'gate') gates[item.key] = item; });
    app.querySelectorAll('[data-gate]').forEach(function (node) {
      var gate = gates[node.dataset.gate];
      if (node.dataset.gate === 'aar') {
        node.textContent = 'Written the day after the op';
        return;
      }
      node.textContent = gate
        ? 'Lock by ' + gate.date_label + ' (' + gate.d_label + ') · ' + gate.status[1]
        : 'Set an op date to see when this locks';
      node.className = 'cpstatus ' + (gate ? gate.status[0] : '');
    });
  }

  function drawChecks() {
    var list = document.getElementById('cp-checks');
    list.textContent = '';
    var mine = state.checks.filter(function (c) { return !c.step || c.step === activeStep; });
    var others = state.checks.length - mine.length;
    mine.forEach(function (c) {
      list.appendChild(el('li', { class: c.level, text: (c.level === 'crit' ? '⛔ ' : '⚠️ ') + c.text }));
    });
    if (!mine.length) list.appendChild(el('li', { class: 'muted', text: 'Nothing to flag on this step.' }));
    if (others) list.appendChild(el('li', { class: 'muted', text: others + ' more on other steps.' }));
    document.getElementById('cp-checkcount').textContent = state.checks.length ? '(' + state.checks.length + ')' : '';
  }

  function drawPreview() {
    var drop = null;
    state.drops.forEach(function (d) { if (d.step === activeStep) drop = d; });
    var box = document.getElementById('cp-preview');
    box.textContent = '';
    previewDrop = drop;
    dcOpen.hidden = !drop;
    if (dcView.open) drawDiscord();
    if (!drop) return;
    var when = null;
    state.schedule.forEach(function (item) { if (item.kind === 'drop' && item.key === drop.key) when = item; });
    document.getElementById('cp-droptitle').textContent = drop.label;
    document.getElementById('cp-dropmeta').textContent =
      (when ? 'Posts ' + when.date_label + ' ' + state.settings.drop_time + ' (' + when.d_label + ') · ' : '') +
      drop.chars + ' characters' + (drop.parts.length > 1 ? ' · goes out as ' + drop.parts.length + ' messages' : '');
    box.appendChild(dropBar(drop));
    drop.parts.forEach(function (part, i) {
      var pre = el('pre', { class: 'cppre', text: part });
      var copy = el('button', { type: 'button', class: 'linkish', text: 'Copy' + (drop.parts.length > 1 ? ' part ' + (i + 1) : ''),
        onclick: function () {
          navigator.clipboard.writeText(part).then(function () {
            copy.textContent = 'Copied';
            setTimeout(function () { copy.textContent = 'Copy' + (drop.parts.length > 1 ? ' part ' + (i + 1) : ''); }, 1500);
          });
        } });
      box.appendChild(el('div', { class: 'cppart' }, [pre, copy]));
    });
    if (drop.images.length) {
      var thumbs = el('div', { class: 'cpthumbs' });
      drop.images.forEach(function (id) {
        thumbs.appendChild(el('img', { src: imageUrl(id), alt: 'Attached image', loading: 'lazy' }));
      });
      box.appendChild(thumbs);
    }
  }

  /* -- the drop as Discord will show it ------------------------------------ */

  /* The formatting is the server's (`drop.html`, utils/discordmd.py), so the
     pop-up and the schedule page show the same thing. What happens here is
     only what Discord itself does in the reader's browser: timestamps redrawn
     in their own time zone, and spoilers revealed on a click. */
  var previewDrop = null;
  var dcOpen = document.getElementById('cp-dcopen');
  var dcView = document.getElementById('cp-dcview');
  var dcBody = document.getElementById('cp-dcbody');
  var DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December'];

  function two(n) { return (n < 10 ? '0' : '') + n; }

  function tsText(ts, style) {
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '';
    var hm = two(d.getHours()) + ':' + two(d.getMinutes());
    var day = d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
    if (style === 't') return hm;
    if (style === 'T') return hm + ':' + two(d.getSeconds());
    if (style === 'd') return two(d.getDate()) + '/' + two(d.getMonth() + 1) + '/' + d.getFullYear();
    if (style === 'D') return day;
    if (style === 'F') return DAYS[d.getDay()] + ', ' + day + ' ' + hm;
    if (style === 'R') {
      var secs = (d.getTime() - Date.now()) / 1000;
      var ahead = secs >= 0;
      secs = Math.abs(secs);
      var units = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]];
      for (var i = 0; i < units.length; i++) {
        if (secs >= units[i][1]) {
          var unit = units[i][0];
          var count = Math.floor(secs / units[i][1]);
          if (unit === 'day' && count >= 7 && count < 30) { unit = 'week'; count = Math.floor(count / 7); }
          var words = count + ' ' + unit + (count === 1 ? '' : 's');
          return ahead ? 'in ' + words : words + ' ago';
        }
      }
      return ahead ? 'in a few seconds' : 'a few seconds ago';
    }
    return day + ' ' + hm;
  }

  function localTimes(root) {
    root.querySelectorAll('.dc-ts').forEach(function (node) {
      var text = tsText(parseInt(node.dataset.ts, 10), node.dataset.style);
      if (text) node.textContent = text;
      node.title = tsText(parseInt(node.dataset.ts, 10), 'F');
    });
  }

  function drawDiscord() {
    var drop = previewDrop;
    dcBody.textContent = '';
    if (!drop) return;
    var name = state.settings.channel_name;
    document.getElementById('cp-dctitle').textContent =
      (name ? '# ' + name : 'No drop channel set') + ' · ' + drop.label;
    var when = drop.due ? new Date(drop.due.ts * 1000) : null;
    var stamp = when
      ? DAYS[when.getDay()] + ' ' + when.getDate() + ' ' + MONTHS[when.getMonth()].slice(0, 3) +
        ' ' + two(when.getHours()) + ':' + two(when.getMinutes())
      : 'when it is due';
    var brand = dcBody.dataset.brand || 'Bot';
    var logo = dcBody.dataset.logo;
    drop.html.forEach(function (html, i) {
      var msg = el('div', { class: 'dcmsg' + (i ? ' dcfollow' : '') });
      if (!i) {
        msg.appendChild(logo
          ? el('img', { class: 'dcavatar', src: logo, alt: '' })
          : el('div', { class: 'dcavatar dcinitial', text: brand.charAt(0) }));
        msg.appendChild(el('div', { class: 'dchead' }, [
          el('span', { class: 'dcname', text: brand }),
          el('span', { class: 'dctag', text: 'APP' }),
          el('span', { class: 'dcwhen', text: stamp })
        ]));
      }
      var body = el('div', { class: 'dcbody' });
      body.innerHTML = html;          // escaped and marked up by the server
      msg.appendChild(body);
      if (i === drop.html.length - 1 && drop.images.length) {
        var grid = el('div', { class: 'dcimages' + (drop.images.length === 1 ? ' one' : '') });
        drop.images.forEach(function (id) {
          grid.appendChild(el('img', { src: imageUrl(id), alt: 'Attached image', loading: 'lazy' }));
        });
        msg.appendChild(grid);
      }
      dcBody.appendChild(msg);
    });
    localTimes(dcBody);
  }

  if (dcOpen && dcView) {
    dcOpen.addEventListener('click', function () {
      drawDiscord();
      if (dcView.showModal) dcView.showModal(); else dcView.setAttribute('open', '');
    });
    dcView.addEventListener('click', function (event) {
      if (event.target === dcView || event.target.closest('[data-dcclose]')) dcView.close();
      var spoiler = event.target.closest('.dc-spoiler');
      if (spoiler) spoiler.classList.add('shown');
    });
  }

  /* The drop's state and its buttons. Approving sends the version on screen
     (its hash); the server refuses if that is not what it would post, so an
     approval is always of words somebody read. */
  function dropBar(drop) {
    var st = drop.state;
    var bar = el('div', { class: 'cpdropstate' }, [
      el('span', { class: 'cpstatus ' + st.tone, text: st.label })
    ]);
    if (drop.approved_by && st.approved) bar.appendChild(el('span', { class: 'muted', text: 'by ' + drop.approved_by }));
    var actions = [];
    if (st.can_approve) actions.push(['approve', st.posted ? 'Approve and update the post' : 'Approve', 'primary']);
    if (st.can_unapprove) actions.push(['unapprove', 'Withdraw approval', '']);
    if (st.key === 'skipped') actions.push(['unskip', 'Put back in the schedule', '']);
    actions.forEach(function (a) {
      bar.appendChild(el('button', { type: 'button', class: 'button small ' + a[2], text: a[1],
        onclick: function () { dropAction(drop, a[0]); } }));
    });
    return bar;
  }

  function dropAction(drop, action) {
    if (inFlight || phasesDirty || Object.keys(pending).length) {
      say('Wait for the save to finish, then approve what you see.', 'err');
      return;
    }
    say('Working…');
    fetch(app.dataset.api + '/drops/' + drop.key + '/' + action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': app.dataset.csrf },
      body: JSON.stringify({ hash: drop.hash })
    }).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, error: 'The server refused that (' + response.status + ').' };
      });
    }).then(function (answer) {
      if (!answer.ok) { say(answer.error || 'That did not work.', 'err'); return; }
      apply(answer.state);
      say(answer.note, 'ok');
    }).catch(function () { say('Could not reach the server.', 'err'); });
  }

  /* -- steps ------------------------------------------------------------------ */

  function showStep(key) {
    activeStep = key;
    app.querySelectorAll('.cpstep').forEach(function (tab) {
      tab.classList.toggle('active', tab.dataset.step === key);
      tab.setAttribute('aria-current', tab.dataset.step === key ? 'step' : 'false');
    });
    app.querySelectorAll('[data-body]').forEach(function (body) { body.hidden = body.dataset.body !== key; });
    app.querySelectorAll('[data-tips]').forEach(function (tips) { tips.hidden = tips.dataset.tips !== key; });
    if (history.replaceState) history.replaceState(null, '', '#' + key);
    drawChecks();
    drawPreview();
  }

  app.querySelectorAll('.cpstep').forEach(function (tab) {
    tab.addEventListener('click', function () { showStep(tab.dataset.step); });
  });

  /* -- plain inputs ----------------------------------------------------------- */

  app.querySelectorAll('input[data-key], textarea[data-key]').forEach(function (input) {
    var key = input.dataset.key;
    input.value = fields[key] || '';
    input.addEventListener('input', function () { touch(key, input.value); });
  });

  /* -- dragging rows ---------------------------------------------------------- */

  /* One reorderable list: rows carry a ⠿ handle that drags, plus ↑ ↓ buttons
     for anybody not using a mouse. Lists nest — objectives inside a phase — so
     a drop only counts when it lands in the list the drag started from (the
     same array), and none of these events bubble to the list around it. */
  var dragging = null;

  function draggable(row, index, list, after) {
    var handle = row.querySelector('.cpdrag');
    handle.addEventListener('mousedown', function () { row.draggable = true; });
    row.addEventListener('dragstart', function (event) {
      event.stopPropagation();
      dragging = { list: list, index: index };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(index));
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', function (event) {
      event.stopPropagation();
      row.draggable = false;
      row.classList.remove('dragging');
      dragging = null;
    });
    row.addEventListener('dragover', function (event) {
      if (!dragging || dragging.list !== list) return;
      event.preventDefault();
      event.stopPropagation();
      row.classList.add('dropover');
    });
    row.addEventListener('dragleave', function () { row.classList.remove('dropover'); });
    row.addEventListener('drop', function (event) {
      if (!dragging || dragging.list !== list) return;
      event.preventDefault();
      event.stopPropagation();
      row.classList.remove('dropover');
      var from = dragging.index;
      dragging = null;
      if (from !== index) { moveItem(list, from, index); after(); }
    });
  }

  function moveItem(list, from, to) {
    var item = list.splice(from, 1)[0];
    list.splice(to, 0, item);
  }

  function rowTools(list, i, redraw, changed) {
    return el('span', { class: 'cprowtools' }, [
      el('button', { type: 'button', class: 'linkish', title: 'Move up', text: '↑', disabled: i === 0 ? true : null,
        onclick: function () { moveItem(list, i, i - 1); redraw(); changed(); } }),
      el('button', { type: 'button', class: 'linkish', title: 'Move down', text: '↓',
        disabled: i === list.length - 1 ? true : null,
        onclick: function () { moveItem(list, i, i + 1); redraw(); changed(); } }),
      el('button', { type: 'button', class: 'linkish cpremove', title: 'Remove', text: '✕',
        onclick: function () { list.splice(i, 1); redraw(); changed(); } })
    ]);
  }

  /* -- rows and list fields --------------------------------------------------- */

  function rowsEditor(box) {
    var key = box.dataset.key;
    var isList = box.dataset.list === '1';
    var columns = isList ? [['', box.dataset.placeholder || '']] : JSON.parse(box.dataset.columns);
    var value = clone(fields[key] || []);
    var changed = function () { touch(key, clone(value)); };

    function draw() {
      box.textContent = '';
      value.forEach(function (row, i) {
        var cells = columns.map(function (col) {
          var long = col[0] === 'tasks' || col[0] === 'detail';
          var input = el(long ? 'textarea' : 'input', {
            placeholder: col[1], maxlength: 2000, rows: long ? 2 : null,
            'aria-label': (col[1] || col[0])
          });
          input.value = isList ? (row || '') : (row[col[0]] || '');
          input.addEventListener('input', function () {
            if (isList) value[i] = input.value; else value[i][col[0]] = input.value;
            changed();
          });
          return input;
        });
        var line = el('div', { class: 'cprow' + (columns.length > 1 ? ' cols' + columns.length : '') },
          [el('span', { class: 'cpdrag', title: 'Drag to reorder', text: '⠿' })].concat(cells)
            .concat([rowTools(value, i, draw, changed)]));
        draggable(line, i, value, function () { draw(); changed(); });
        box.appendChild(line);
      });
      box.appendChild(el('button', { type: 'button', class: 'linkish cpadd', text: '+ Add ' + (isList ? 'item' : 'row'),
        onclick: function () {
          if (isList) value.push('');
          else { var blank = {}; columns.forEach(function (c) { blank[c[0]] = ''; }); value.push(blank); }
          draw();
          var inputs = box.querySelectorAll('.cprow:last-of-type input, .cprow:last-of-type textarea');
          if (inputs.length) inputs[0].focus();
        } }));
    }

    box.cpSet = function (next) { value = clone(next); draw(); changed(); };
    box.cpGet = function () { return value; };
    draw();
  }

  app.querySelectorAll('.cprows').forEach(rowsEditor);

  /* -- groups taking part ----------------------------------------------------- */

  app.querySelectorAll('.cpgroups').forEach(function (box) {
    var key = box.dataset.key;
    var boxes = box.querySelectorAll('input[type=checkbox]');
    var extra = box.querySelector('.cpgroupsextra');
    var known = Array.prototype.map.call(boxes, function (b) { return b.value; });
    var current = fields[key] || [];
    boxes.forEach(function (b) { b.checked = current.indexOf(b.value) !== -1; });
    extra.value = current.filter(function (g) { return known.indexOf(g) === -1; }).join(', ');
    function changed() {
      var picked = Array.prototype.filter.call(boxes, function (b) { return b.checked; })
        .map(function (b) { return b.value; });
      extra.value.split(',').forEach(function (g) { g = g.trim(); if (g && picked.indexOf(g) === -1) picked.push(g); });
      touch(key, picked);
    }
    boxes.forEach(function (b) { b.addEventListener('change', changed); });
    extra.addEventListener('input', changed);
  });

  /* -- images ----------------------------------------------------------------- */

  function upload(file) {
    var data = new FormData();
    data.append('csrf', app.dataset.csrf);
    data.append('campaign_id', app.dataset.campaign);
    data.append('file', file, file.name || 'pasted.png');
    say('Uploading ' + (file.name || 'image') + '…');
    return fetch(app.dataset.images, { method: 'POST', body: data }).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, error: 'The server refused the upload (' + response.status + ').' };
      });
    }).then(function (answer) {
      if (!answer.ok) throw new Error(answer.error || 'That did not upload.');
      images[answer.image.id] = { filename: answer.image.filename, bytes: answer.image.bytes };
      return answer.image.id;
    });
  }

  app.querySelectorAll('.cpimages').forEach(function (box) {
    var key = box.dataset.key;
    var multiple = box.dataset.multiple === '1';
    var thumbs = box.querySelector('.cpthumbs');
    var picker = box.querySelector('input[type=file]');
    var value = multiple ? clone(fields[key] || []) : fields[key] || null;
    // An upload error belongs next to the field: the status line at the top is
    // overwritten by the next "Saved", which would hide why the image is missing.
    var problem = el('p', { class: 'cpfielderr', role: 'alert', hidden: true });
    box.appendChild(problem);

    function commit() { touch(key, multiple ? clone(value) : value); }

    function draw() {
      thumbs.textContent = '';
      var ids = multiple ? value : (value ? [value] : []);
      ids.forEach(function (id, i) {
        var meta = images[id] || {};
        var figure = el('figure', { class: 'cpthumb' }, [
          el('a', { href: imageUrl(id), target: '_blank', rel: 'noopener' },
            [el('img', { src: imageUrl(id), alt: meta.filename || 'Image', loading: 'lazy' })]),
          el('figcaption', {}, [
            el('span', { class: 'muted', text: meta.filename || '' }),
            multiple && i > 0 ? el('button', { type: 'button', class: 'linkish', text: '←', title: 'Move earlier',
              onclick: function () { moveItem(value, i, i - 1); draw(); commit(); } }) : null,
            el('button', { type: 'button', class: 'linkish cpremove', text: '✕', title: 'Remove',
              onclick: function () {
                if (multiple) value.splice(i, 1); else value = null;
                draw(); commit();
              } })
          ])
        ]);
        thumbs.appendChild(figure);
      });
    }

    function take(files) {
      var list = Array.prototype.slice.call(files || []).filter(function (f) { return /^image\//.test(f.type); });
      if (!multiple) list = list.slice(0, 1);
      if (!list.length) {
        problem.textContent = 'That is not an image I can use. PNG, JPEG, GIF or WebP.';
        problem.hidden = false;
        return;
      }
      problem.hidden = true;
      list.reduce(function (chain, file) {
        return chain.then(function () {
          return upload(file).then(function (id) {
            if (multiple) value.push(id); else value = id;
            draw(); commit();
          });
        });
      }, Promise.resolve()).catch(function (error) {
        problem.textContent = error.message;
        problem.hidden = false;
        say('An image did not upload — see the field.', 'err');
      });
    }

    box.querySelector('[data-upload]').addEventListener('click', function () { picker.click(); });
    picker.addEventListener('change', function () { take(picker.files); picker.value = ''; });
    box.addEventListener('paste', function (event) {
      var files = [];
      Array.prototype.forEach.call((event.clipboardData || {}).items || [], function (item) {
        if (item.kind === 'file') files.push(item.getAsFile());
      });
      if (files.length) { event.preventDefault(); take(files); }
    });
    box.addEventListener('dragover', function (event) { event.preventDefault(); box.classList.add('dropover'); });
    box.addEventListener('dragleave', function () { box.classList.remove('dropover'); });
    box.addEventListener('drop', function (event) {
      event.preventDefault();
      box.classList.remove('dropover');
      take(event.dataTransfer.files);
    });
    draw();
  });

  /* -- phases and their objectives --------------------------------------------- */

  var phaseBox = app.querySelector('.cpphases');

  function aoSelect(phase) {
    var select = el('select', { 'aria-label': 'AO' },
      [el('option', { value: '', text: AOS.length ? 'No AO' : 'No AOs yet — add them on the campaign page' })]
        .concat(AOS.map(function (ao) {
          return el('option', { value: ao.id, text: ao.name, selected: phase.ao_id === ao.id ? true : null });
        })));
    select.addEventListener('change', function () {
      phase.ao_id = select.value ? parseInt(select.value, 10) : null;
      touchPhases();
    });
    return select;
  }

  function objectiveCard(phase, objective, i, redraw) {
    var role = el('select', { 'aria-label': 'Role' }, ROLES.map(function (r) {
      return el('option', { value: r, text: ROLE_LABELS[r], selected: objective.role === r ? true : null });
    }));
    function input(key, placeholder, long) {
      var node = el(long ? 'textarea' : 'input', { placeholder: placeholder, rows: long ? 2 : null,
        maxlength: key === 'name' ? 120 : 600, 'aria-label': placeholder });
      node.value = objective[key] || '';
      node.addEventListener('input', function () { objective[key] = node.value; touchPhases(); });
      return node;
    }
    var complication = objective.role === 'complication';
    role.addEventListener('change', function () { objective.role = role.value; touchPhases(); redraw(); });
    var card = el('div', { class: 'cpobjective role-' + objective.role }, [
      el('div', { class: 'cpobjhead' }, [
        el('span', { class: 'cpdrag', title: 'Drag to reorder', text: '⠿' }),
        role, input('name', complication ? 'Complication' : 'Task, e.g. SEIZE Obj KRITZENAST'),
        rowTools(phase.objectives, i, redraw, touchPhases)
      ]),
      input('players_do', complication ? 'Effect on play' : 'What players do', true),
      complication ? input('intsum_hint', 'INTSUM hint — how the intel foreshadows it', false)
                   : input('complete_when', 'Complete when — goes into "Success looks like"', true)
    ]);
    draggable(card, i, phase.objectives, function () { redraw(); touchPhases(); });
    return card;
  }

  function loadLibrary() {
    if (library) return Promise.resolve(library);
    return fetch(app.dataset.objectives).then(function (r) { return r.json(); }).then(function (answer) {
      library = answer.entries || [];
      return library;
    });
  }

  /* The picker: the library, searchable, with the entries that pair with what
     the phase already has lifted to the top. Picking copies the wording into
     the phase, where it can be edited without touching the library. */
  function picker(phase, redraw) {
    var wrap = el('div', { class: 'cppicker' });
    var search = el('input', { type: 'search', placeholder: 'Search the library — SEIZE, recon, IED…', 'aria-label': 'Search objectives' });
    var list = el('div', { class: 'cppicklist' }, [el('p', { class: 'muted', text: 'Loading the library…' })]);
    wrap.appendChild(el('div', { class: 'cppickbar' }, [
      search,
      el('button', { type: 'button', class: 'linkish', text: 'Blank objective', onclick: function () {
        add({ id: null, kind: 'objective', name: '', players_do: '', complete_when: '', intsum_hint: '' });
      } }),
      el('button', { type: 'button', class: 'linkish', text: 'Close', onclick: function () { wrap.remove(); } })
    ]));
    wrap.appendChild(list);

    function add(entry) {
      var roles = phase.objectives.map(function (o) { return o.role; });
      var role = entry.kind === 'complication' ? 'complication'
        : (roles.indexOf('primary') === -1 ? 'primary' : 'secondary');
      phase.objectives.push({
        library_id: entry.id, role: role, name: entry.name, players_do: entry.players_do,
        complete_when: entry.complete_when, intsum_hint: entry.intsum_hint, result: ''
      });
      touchPhases();
      redraw();
    }

    function draw() {
      var q = search.value.trim().toLowerCase();
      var chosen = phase.objectives.map(function (o) { return o.library_id; }).filter(Boolean);
      var suggested = {};
      library.forEach(function (e) {
        if (chosen.indexOf(e.id) !== -1) (e.pairs || []).forEach(function (id) { suggested[id] = true; });
      });
      var shown = library.filter(function (e) {
        return !q || (e.name + ' ' + e.players_do + ' ' + e.category).toLowerCase().indexOf(q) !== -1;
      });
      shown.sort(function (a, b) { return (suggested[b.id] ? 1 : 0) - (suggested[a.id] ? 1 : 0); });
      list.textContent = '';
      if (!shown.length) list.appendChild(el('p', { class: 'muted', text: 'Nothing matches.' }));
      var lastCategory = null;
      shown.slice(0, 80).forEach(function (e) {
        var heading = suggested[e.id] ? 'Pairs well with this phase' : e.category;
        if (heading !== lastCategory) {
          list.appendChild(el('h4', { class: 'cppickcat', text: heading }));
          lastCategory = heading;
        }
        list.appendChild(el('button', { type: 'button', class: 'cppick' + (suggested[e.id] ? ' suggested' : ''),
          onclick: function () { add(e); } }, [
          el('strong', { text: e.name }),
          el('span', { class: 'muted', text: ' — ' + e.players_do })
        ]));
      });
    }

    search.addEventListener('input', function () { if (library) draw(); });
    loadLibrary().then(draw).catch(function () {
      list.textContent = 'Could not load the library. Try again in a moment.';
    });
    setTimeout(function () { search.focus(); }, 0);
    return wrap;
  }

  function drawPhases() {
    phaseBox.textContent = '';
    phases.forEach(function (phase, i) {
      phase.objectives = phase.objectives || [];
      var title = el('input', { placeholder: 'Phase ' + (i + 1) + ' – Infiltration, HOCHABRUNN', maxlength: 200, 'aria-label': 'Phase title' });
      title.value = phase.title || '';
      title.addEventListener('input', function () { phase.title = title.value; touchPhases(); });
      var detail = el('textarea', { placeholder: 'What happens in this phase', rows: 2, maxlength: 2000, 'aria-label': 'What happens' });
      detail.value = phase.detail || '';
      detail.addEventListener('input', function () { phase.detail = detail.value; touchPhases(); });

      var objectives = el('div', { class: 'cpobjectives' });
      function redrawObjectives() {
        objectives.textContent = '';
        phase.objectives.forEach(function (o, j) { objectives.appendChild(objectiveCard(phase, o, j, redrawObjectives)); });
      }
      redrawObjectives();
      var pickerSlot = el('div');
      var addObjective = el('button', { type: 'button', class: 'button', text: '+ Add objective',
        onclick: function () {
          if (pickerSlot.firstChild) return;
          pickerSlot.appendChild(picker(phase, function () { redrawObjectives(); pickerSlot.textContent = ''; }));
        } });

      var card = el('div', { class: 'cpphase' }, [
        el('div', { class: 'cpphasehead' }, [
          el('span', { class: 'cpdrag', title: 'Drag to reorder', text: '⠿' }),
          el('span', { class: 'cpphasenum', text: String(i + 1) }),
          title, aoSelect(phase),
          rowTools(phases, i, drawPhases, touchPhases)
        ]),
        detail,
        objectives,
        el('div', { class: 'cpphaseactions' }, [addObjective]),
        pickerSlot
      ]);
      draggable(card, i, phases, function () { drawPhases(); touchPhases(); });
      phaseBox.appendChild(card);
    });
  }

  var addPhase = app.querySelector('[data-add-phase]');
  if (addPhase) {
    addPhase.addEventListener('click', function () {
      if (phases.length >= MAX_PHASES) {
        say('A mission can have at most ' + MAX_PHASES + ' phases — one per layer on the AO map.', 'err');
        return;
      }
      phases.push({ id: null, title: '', detail: '', ao_id: AOS.length === 1 ? AOS[0].id : null, objectives: [] });
      drawPhases();
      touchPhases();
      var inputs = phaseBox.querySelectorAll('.cpphase:last-child .cpphasehead input');
      if (inputs.length) inputs[0].focus();
    });
  }
  if (phaseBox) drawPhases();

  /* The AAR's objectives table, started from what the phases set out to do. */
  var fill = app.querySelector('[data-fill-results]');
  if (fill) {
    fill.addEventListener('click', function () {
      var box = document.getElementById('cpf-results');
      var rows = clone(box.cpGet());
      var have = rows.map(function (r) { return (r.objective || '').trim().toLowerCase(); });
      phases.forEach(function (phase, i) {
        phase.objectives.forEach(function (o) {
          if (o.role === 'complication' || !o.name) return;
          var label = o.name + ' (' + (phase.title || 'Phase ' + (i + 1)) + ')';
          if (have.indexOf(label.toLowerCase()) === -1) rows.push({ objective: label, result: '' });
        });
      });
      box.cpSet(rows);
    });
  }

  /* -- start ------------------------------------------------------------------ */

  var first = (location.hash || '').slice(1);
  var known = state.progress.map(function (p) { return p.key; });
  if (known.indexOf(first) === -1) {
    // Open on the first step that is not locked yet: where the work is.
    first = known[0];
    for (var i = 0; i < state.progress.length; i++) {
      if (!state.progress[i].complete) { first = state.progress[i].key; break; }
    }
  }
  apply(state);
  showStep(first);
})();
