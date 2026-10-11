// Where the numbers come from. The Google Sheet is the default; `?source=bot` reads
// the same rows from the bot's database (counted straight from the OCAP server) so
// the two can be compared side by side. Both are the same 35 columns.
const SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPdSKTP3NyYXXMON52HCpNv8bEmM9ElmCgKHeGbYIVAtMv9ADAwBaniA8dqIyEHyOe3q6gbA1PEdZb/pub?gid=267117435&single=true&output=csv";
const BOT_API = "https://bot.taskforcephalanx.com/kt/api/";
const BOT_CSV_URL = BOT_API + "stats.csv";
const DATA_SOURCE = new URLSearchParams(location.search).get("source") === "bot" ? "bot" : "sheet";
const CSV_URL = DATA_SOURCE === "bot" ? BOT_CSV_URL : SHEET_CSV_URL;
if (DATA_SOURCE === "bot") {
  document.title = "[BOT DATA] " + document.title;
  document.addEventListener("DOMContentLoaded", () => {
    const badge = document.createElement("div");
    badge.textContent = "Data source: bot (?source=bot) — not the sheet";
    badge.style.cssText = "position:fixed;bottom:8px;left:8px;z-index:9999;padding:4px 10px;" +
      "background:#b45309;color:#fff;font:12px/1.4 sans-serif;border-radius:4px;opacity:.9;";
    document.body.appendChild(badge);
  });
}
const RELEASE_DATE = "2026-06-27";
const VERSION      = "1.026";

// ── UNIT CLASSIFICATION ───────────────────────────────────────────────────
const UNIT_SEEDS = {
  '2nd USC': ['[2nd USC] Panzerfanlol', '[2nd USC] Cpt. Eddie'],
  'CNTO':    ['Pricepole', 'Clarke', 'Fishgrit'],
  'PXG':     ['Darkling', 'Aquafox', 'Lortmil', 'glyn'],
  'TFP':     ['Sindh', 'loltorres9', 'DickSplash', 'Gortarius', 'Akarin', 'LucasC', 'Ocke', 'najt'],
  'SKUA':    ['SPC. Green Giant', 'WC. Picker', 'PFC. Bearz'],
};
const UNIT_COLORS = { '2nd USC': '#1a56db', 'CNTO': '#057a55', 'PXG': '#c27803', 'TFP': '#c0392b', 'SKUA': '#6b21a8' };
const UNIT_ORDER  = ['2nd USC', 'CNTO', 'PXG', 'TFP', 'SKUA'];

// Column indices in the CSV (0-based after splitting)
// Source File(0), Mission(1), World(2), Username(3), Side(4), Group(5),
// Kills OnFoot(6), Deaths OnFoot(7), KD OnFoot(8), TK OnFoot(9),
// Shots OnFoot(10), Hits OnFoot(11), SPK OnFoot(12), AvgDist OnFoot(13), LongestOnFoot(14),
// Kills InVeh(15), Deaths InVeh(16), KD InVeh(17), TK InVeh(18),
// VehKills OnFoot(19), VehKills InVeh(20),
// Shots InVeh(21), Hits InVeh(22), SPK InVeh(23), AvgDist InVeh(24), LongestInVeh(25)

const NUM = v => parseFloat(v) || 0;
// On-foot kills beyond this distance are treated as data errors (e.g. wrong weapon
// attribution) and ignored for "longest kill" stats. Mirrors ImportScript.
const MAX_FOOT_KILL_DIST = 1500;
const footLongest = v => (v > MAX_FOOT_KILL_DIST ? 0 : v);

function fmtTime(secs) {
  if (!secs) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function normalizeRole(role) {
  return role.trim()
    .replace(/@.*$/, '')
    .replace(/\s+(Alpha|Bravo|Charlie|Delta|Echo|Foxtrot|\d+)$/i, '')
    .replace(/\s+(Red|Blue|Green|Yellow|White|Black|Orange|Purple|Teal|Pink|Cyan|Lime|Maroon|Navy|Olive|Silver|Violet|Magenta)$/i, '')
    .trim();
}

function missionDate(missionName) {
  const m = missionName.match(/\((\d{4}-\d{2}-\d{2})\)/);
  if (!m) return missionName;
  const [y, mo, d] = m[1].split('-');
  return `${d}/${mo}/${y}`;
}

let rawRows = [];    // one entry per CSV row
let aggPlayers = {}; // aggregated by player name across missions
let filteredPlayers = [];
let filteredRows = []; // rawRows filtered to match filteredPlayers (set in applyFilters)

let infSortCol = 2;   // col index 2 = Kills in INF_COLS
let infSortAsc = false;
let vehSortCol = 2;   // col index 2 = Kills in VEH_COLS
let vehSortAsc = false;
const infAvgCols = new Set();
const vehAvgCols = new Set();
let currentModalPlayer = null;
let playerUnits = {};   // name -> unit string
let selectedUnit = null; // null = all units

// ── EVENT TYPE DETECTION ─────────────────────────────────────────────────
// Filename format: YYYY_MM_DD__HH_MM_MissionName_json.gz
function isJointOp(filename) {
  // Joint Op = last Saturday of the month AND the Sunday immediately after it
  const m = filename.match(/(\d{4})_(\d{2})_(\d{2})/) || filename.match(/_(\d{4})(\d{2})(\d{2})_/);
  if (!m) return false;
  const year = parseInt(m[1]), month = parseInt(m[2]) - 1, day = parseInt(m[3]);
  const d = new Date(year, month, day);
  const dow = d.getDay(); // 0=Sun, 6=Sat
  if (dow === 6) {
    // Saturday — is it the last one of the month?
    return new Date(year, month, day + 7).getMonth() !== month;
  }
  if (dow === 0) {
    // Sunday — was yesterday the last Saturday of its month?
    const prevSat = new Date(year, month, day - 1);
    if (prevSat.getDay() !== 6) return false; // sanity check
    // Check prev Saturday is the last Saturday of ITS own month
    const nextSatFromPrev = new Date(prevSat.getFullYear(), prevSat.getMonth(), prevSat.getDate() + 7);
    return nextSatFromPrev.getMonth() !== prevSat.getMonth();
  }
  return false;
}

let showJointOps   = true;
let showRegularEvents = true;
let zeusFilter = "all";

// ── PLAYER NAME NORMALISATION ──────────────────────────────────────────────
// Reduces mechanical name variants to a common key so they merge into one
// player entry. Handles:
//   • Spaces inside brackets:   [57th MEU] → [57thmeu]
//   • No space after bracket:   [tag]Name  → [tag] Name
//   • Missing space after dot:  S.Fig      → S. Fig
//   • Underscores as separators: [SR]_Gronk → [sr] gronk
// Does NOT merge rank variants (Sgt. Deadly vs Deadly) — use player_aliases.json.
function normalizeName(name) {
  return name
    .replace(/\[([^\]]+)\]/g, (_, tag) => '[' + tag.replace(/\s+/g, '') + ']')
    .replace(/\]\s*/g, '] ')
    .replace(/\.(?=[^\s\].])/g, '. ')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

// Minimum length a callsign must have to be used as a suffix anchor.
// Prevents merging on short common words (e.g. "sky", "dan").
const MIN_CALLSIGN_LEN = 4;

// Pre-scan all rows to build rawName → canonicalName map.
// Three-pass strategy:
//   1. Exact-normalisation: spacing/bracket/punctuation variants → same key.
//   2. Suffix matching: "[2nd USC] Fre3ky", "HMC.P Fre3ky" → "Fre3ky" because
//      the longer normalised name ends with (space or ]) + shorter name.
//      Canonical = the bare (shortest) callsign.
//   3. Explicit aliases from player_aliases.json override everything.
let nameMap = {};
function buildNameMap(rows, aliases) {
  const freq = {};
  rows.forEach(r => {
    const n = (r['Username'] || '').trim();
    if (n) freq[n] = (freq[n] || 0) + 1;
  });

  // norm key → raw names
  const normToRaws = {};
  Object.keys(freq).forEach(n => {
    const key = normalizeName(n);
    if (!normToRaws[key]) normToRaws[key] = [];
    normToRaws[key].push(n);
  });

  // norm key → canonical raw name (most-missions, then alpha)
  const normToCanonical = {};
  Object.entries(normToRaws).forEach(([norm, raws]) => {
    normToCanonical[norm] = raws.slice().sort((a, b) => (freq[b] - freq[a]) || a.localeCompare(b))[0];
  });

  // Pass 1 — exact normalisation
  const map = {};
  Object.entries(normToRaws).forEach(([norm, raws]) => {
    raws.forEach(n => { map[n] = normToCanonical[norm]; });
  });

  // Pass 2 — suffix matching
  // Sort by length ascending so shorter (= bare callsign) comes first.
  const normKeys = Object.keys(normToCanonical).sort((a, b) => a.length - b.length);
  for (let i = 0; i < normKeys.length; i++) {
    const shorter = normKeys[i];
    if (shorter.length < MIN_CALLSIGN_LEN) continue;
    for (let j = i + 1; j < normKeys.length; j++) {
      const longer = normKeys[j];
      if (normToCanonical[longer] === normToCanonical[shorter]) continue; // already merged
      const pos = longer.length - shorter.length;
      // Suffix must be preceded by a space or ] to avoid partial-word matches
      if (pos > 0 && (longer[pos - 1] === ' ' || longer[pos - 1] === ']') && longer.endsWith(shorter)) {
        const shorterCanonical = normToCanonical[shorter];
        (normToRaws[longer] || []).forEach(n => { map[n] = shorterCanonical; });
        normToCanonical[longer] = shorterCanonical; // propagate for transitive chains
      }
    }
  }

  // Pass 3 — explicit aliases win over everything
  Object.entries(aliases).forEach(([raw, canonical]) => { map[raw] = canonical; });

  // Build reverse map: canonical → sorted list of other names it absorbed
  nameAliases = {};
  Object.entries(map).forEach(([raw, canonical]) => {
    if (raw !== canonical) {
      if (!nameAliases[canonical]) nameAliases[canonical] = [];
      nameAliases[canonical].push(raw);
    }
  });
  Object.values(nameAliases).forEach(arr => arr.sort());

  return map;
}

let nameAliases = {}; // canonical → [alias, ...]

// ── FETCH & PARSE ────────────────────────────────────────────────────────
Promise.all([
  fetch(CSV_URL).then(r => { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); }),
  // With ?source=bot these three come from the bot too (managed on its admin pages);
  // otherwise from the JSON files in this repo, as always.
  fetch(DATA_SOURCE === "bot" ? BOT_API + 'unit_overrides.json' : 'unit_overrides.json').then(r => r.ok ? r.json() : {}).catch(() => ({})),
  fetch(DATA_SOURCE === "bot" ? BOT_API + 'player_aliases.json' : 'player_aliases.json').then(r => r.ok ? r.json() : {}).catch(() => ({})),
  fetch(DATA_SOURCE === "bot" ? BOT_API + 'campaigns.json' : 'campaigns.json').then(r => r.ok ? r.json() : {}).catch(() => ({}))
])
  .then(([csv, overrides, aliases, campaigns]) => {
    const lines = csv.trim().split("\n");
    const header = lines[0].split(",").map(h => h.replace(/"/g,"").replace(/\r/g,"").trim());
    rawRows = lines.slice(1).map(line => {
      // Handle CSV fields with commas in quotes
      const cols = parseCSVLine(line);
      const obj = {};
      header.forEach((h,i) => {
        const raw = cols[i] || "";
        // Don't strip quotes from JSON columns
        obj[h] = (h === "Weapon Kills (JSON)" || h === "Vehicle Kills (JSON)") ? raw.trim() : raw.replace(/"/g,"").replace(/\r/g,"").trim();
      });
      return obj;
    }).filter(r => r["Username"]);

    nameMap = buildNameMap(rawRows, aliases);
    buildAggregates();
    playerUnits = classifyPlayerUnits(rawRows);
    // Apply manual overrides from unit_overrides.json on top of auto-classification
    Object.entries(overrides).forEach(([name, unit]) => {
      playerUnits[name] = unit;
    });
    Object.values(aggPlayers).forEach(p => { p.unit = playerUnits[p.name] || null; });
    campaignData = campaigns;
    buildUI();
  })
  .catch(err => {
    document.getElementById("loading").style.display = "none";
    const el = document.getElementById("error");
    el.style.display = "";
    el.textContent = "Failed to load data: " + err.message;
  });

function parseCSVLine(line) {
  const result = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQuotes && line[i+1] === '"') {
        cur += '"'; i++; // escaped quote "" -> "
      } else {
        inQuotes = !inQuotes; // toggle quoted mode, don't add the delimiter quote
      }
    } else if (c === ',' && !inQuotes) {
      result.push(cur); cur = "";
    } else {
      cur += c;
    }
  }
  result.push(cur);
  return result;
}

// ── AGGREGATE ───────────────────────────────────────────────────────────
function buildAggregates() {
  aggPlayers = {};
  rawRows.forEach(r => {
    const rawName = r["Username"] || r["Username\r"] || "";
    if (!rawName) return;
    const name = nameMap[rawName] || rawName;
    if (!aggPlayers[name]) {
      aggPlayers[name] = {
        name,
        missions: new Set(),
        worlds: new Set(),
        // On-foot
        killsOnFoot: 0, deathsOnFoot: 0, tkOnFoot: 0,
        shotsOnFoot: 0, hitsOnFoot: 0, hitsDealtOnFoot: 0,
        killDistOnFoot: [], // collect distances for avg/max
        suicides: 0,
        distanceRun: 0,
        // In-vehicle
        killsInVeh: 0, deathsInVeh: 0, tkInVeh: 0,
        vehKillsFoot: 0, vehKillsVeh: 0,
        shotsInVeh: 0, hitsInVeh: 0, hitsDealtInVeh: 0,
        killDistInVeh: [],
        maxLongestFoot: 0, maxLongestVeh: 0,
        avgDistFootSum: 0, avgDistFootN: 0,
        avgDistVehSum: 0,  avgDistVehN: 0,
        weaponKills: {},
        missionRows: [],
        longestKillWeapon: "",  // weapon used for the longest kill
        roleCounts: {},
        timePlayed: 0,
      };
    }
    const p = aggPlayers[name];
    const mission = r["Mission"] || r["Mission\r"] || "";
    p.missions.add(mission);

    const rawRole = r["Role"] || r["Group"] || "";
    if (rawRole) {
      const normRole = normalizeRole(rawRole);
      if (normRole) p.roleCounts[normRole] = (p.roleCounts[normRole] || 0) + 1;
    }
    const world = r["World"] || "";
    if (world) p.worlds.add(world);

    const kof  = NUM(r["Kills (On Foot)"]);
    const dof  = NUM(r["Deaths (On Foot)"]);
    const tkof = NUM(r["Teamkills (On Foot)"]);
    const sof  = NUM(r["Shots (On Foot)"]);
    const hof  = NUM(r["Hits Taken (On Foot)"]);
    const hdof = NUM(r["Hits Dealt (On Foot)"]);
    const lof  = footLongest(NUM(r["Longest Kill On Foot (m)"]));
    const aof  = NUM(r["Avg Kill Dist On Foot (m)"]);

    const kiv  = NUM(r["Kills (In Vehicle)"]);
    const div2 = NUM(r["Deaths (In Vehicle)"]);
    const tkiv = NUM(r["Teamkills (In Vehicle)"]);
    const vkof = NUM(r["Vehicle Kills (On Foot)"]);
    const vkiv = NUM(r["Vehicle Kills (In Vehicle)"]);
    const siv  = NUM(r["Shots (In Vehicle)"]);
    const hiv  = NUM(r["Hits Taken (In Vehicle)"]);
    const hdiv = NUM(r["Hits Dealt (In Vehicle)"]);
    const liv  = NUM(r["Longest Kill In Vehicle (m)"]);
    const aiv  = NUM(r["Avg Kill Dist In Vehicle (m)"]);

    p.killsOnFoot  += kof;
    p.deathsOnFoot += dof;
    p.tkOnFoot     += tkof;
    p.shotsOnFoot      += sof;
    p.hitsOnFoot       += hof;
    p.hitsDealtOnFoot  += hdof;
    p.suicides     += NUM((r["Suicides"] || r["Suicides\r"] || "0"));
    p.distanceRun  += NUM(r["Distance Run (km)"] || r["Distance Run (km)\r"] || "0");
    p.timePlayed   += NUM(r["Time Played (s)"] || r["Time Played (s)\r"] || "0");
    const topWeaponRow = r["Top Weapon"] || r["Top Weapon\r"] || "";
    if (lof > p.maxLongestFoot) { p.maxLongestFoot = lof; p.longestKillWeapon = topWeaponRow; }
    if (kof > 0 && aof > 0) { p.avgDistFootSum += aof * kof; p.avgDistFootN += kof; }

    p.killsInVeh  += kiv;
    p.deathsInVeh += div2;
    p.tkInVeh     += tkiv;
    p.vehKillsFoot+= vkof;
    p.vehKillsVeh += vkiv;
    p.shotsInVeh      += siv;
    p.hitsInVeh       += hiv;
    p.hitsDealtInVeh  += hdiv;
    if (liv > p.maxLongestVeh) p.maxLongestVeh = liv;
    if (kiv > 0 && aiv > 0) { p.avgDistVehSum += aiv * kiv; p.avgDistVehN += kiv; }

    // Weapons + mission rows (for modal)
    try {
      const weaponJson = r["Weapon Kills (JSON)"] || r["Weapon Kills (JSON)\r"] || "{}";
      const wmap = JSON.parse(weaponJson);
      Object.entries(wmap).forEach(([w, c]) => {
        const weapon = w.replace(/\s*\[[^\]]+\]$/, '');
        p.weaponKills[weapon] = (p.weaponKills[weapon] || 0) + c;
      });
    } catch(e) {}
    p.missionRows.push(r);
  });

  // Compute derived fields
  Object.values(aggPlayers).forEach(p => {
    const totalDeaths = p.deathsOnFoot + p.deathsInVeh;
    const totalKills  = p.killsOnFoot  + p.killsInVeh;
    p.kdFoot = p.deathsOnFoot > 0 ? p.killsOnFoot / p.deathsOnFoot : p.killsOnFoot;
    p.kdVeh  = p.deathsInVeh  > 0 ? p.killsInVeh  / p.deathsInVeh  : p.killsInVeh;
    p.spkFoot      = p.killsOnFoot > 0 ? p.shotsOnFoot / p.killsOnFoot : null;
    p.spkVeh       = p.killsInVeh  > 0 ? p.shotsInVeh  / p.killsInVeh  : null;
    p.accuracyFoot = p.shotsOnFoot > 0 ? (p.hitsDealtOnFoot / p.shotsOnFoot) * 100 : null;
    p.accuracyVeh  = p.shotsInVeh  > 0 ? (p.hitsDealtInVeh  / p.shotsInVeh)  * 100 : null;
    p.avgDistFoot = p.avgDistFootN > 0 ? p.avgDistFootSum / p.avgDistFootN : 0;
    p.avgDistVeh  = p.avgDistVehN  > 0 ? p.avgDistVehSum  / p.avgDistVehN  : 0;
    p.missionCount = p.missions.size;
    const _rs = Object.entries(p.roleCounts || {}).sort((a, b) => b[1] - a[1]);
    p.topRole = _rs.length > 0 ? _rs[0][0] : null;
    p.topRoleCount = _rs.length > 0 ? _rs[0][1] : 0;
  });
}

// ── ZEUS DETECTION ────────────────────────────────────────────────────────
// True if a CSV row represents a Zeus player — checks both Group and Role
// so players with "Co Zeus", "Zeus@Zeus", "Command@Zeus" etc. are all caught.
function rowIsZeus(r) {
  return (r['Group'] || '').toLowerCase().includes('zeus') ||
         (r['Role']  || '').toLowerCase().includes('zeus');
}

// ── UNIT CLASSIFICATION ───────────────────────────────────────────────────
function classifyPlayerUnits(rows) {
  // Build squad co-occurrence: players in the same (sourceFile, group) pair
  const squads = {};
  rows.forEach(r => {
    const src   = r['Source File'] || '';
    const group = r['Group'] || '';
    const name  = r['Username'] || '';
    if (!src || !group || !name || rowIsZeus(r)) return;
    const key = `${src}|||${group}`;
    if (!squads[key]) squads[key] = new Set();
    squads[key].add(name);
  });

  // Count how many squads each pair of players shared
  const coOcc = {};
  Object.values(squads).forEach(members => {
    const arr = [...members];
    for (let i = 0; i < arr.length; i++) {
      for (let j = 0; j < arr.length; j++) {
        if (i === j) continue;
        if (!coOcc[arr[i]]) coOcc[arr[i]] = {};
        coOcc[arr[i]][arr[j]] = (coOcc[arr[i]][arr[j]] || 0) + 1;
      }
    }
  });

  // Start with seed assignments
  const units = {};
  Object.entries(UNIT_SEEDS).forEach(([unit, players]) => {
    players.forEach(p => { units[p] = unit; });
  });

  // Auto-classify players whose name starts with [Tag] matching a unit
  const allPlayers = [...new Set(rows.map(r => r['Username']).filter(Boolean))];
  allPlayers.forEach(name => {
    if (units[name]) return;
    const m = name.match(/^\[([^\]]+)\]/);
    if (!m) return;
    const tag = m[1].toLowerCase();
    const matched = UNIT_ORDER.find(u =>
      u.toLowerCase() === tag || u.toLowerCase().replace('2nd ', '') === tag
    );
    if (matched) units[name] = matched;
  });

  // Iterative spreading: assign unclassified players based on co-occurrence scores
  let changed = true;
  while (changed) {
    changed = false;
    allPlayers.forEach(name => {
      if (units[name]) return;
      const neighbors = coOcc[name] || {};
      const scores = {};
      let total = 0;
      Object.entries(neighbors).forEach(([neighbor, count]) => {
        const u = units[neighbor];
        if (u) { scores[u] = (scores[u] || 0) + count; total += count; }
      });
      if (total === 0) return;
      const [bestUnit, bestScore] = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];
      // Assign only if the dominant unit accounts for ≥99% of co-occurrence weight
      if (bestScore / total >= 0.99) {
        units[name] = bestUnit;
        changed = true;
      }
    });
  }

  return units;
}

function unitBadgeHTML(playerName) {
  const unit = playerUnits[playerName];
  if (!unit) return '';
  const color = UNIT_COLORS[unit] || '#666';
  return `<span class="unit-badge" style="background:${color}">${unit}</span>`;
}

function renderUnitFilter() {
  const container = document.getElementById('unitFilterBtns');
  if (!container) return;

  // Count players per unit from the full aggPlayers (not filtered)
  const counts = {};
  UNIT_ORDER.forEach(u => { counts[u] = 0; });
  counts['Unknown'] = 0;
  Object.values(aggPlayers).forEach(p => {
    const u = p.unit || 'Unknown';
    if (counts[u] !== undefined) counts[u]++;
    else counts['Unknown']++;
  });

  const allUnits = [null, ...UNIT_ORDER, 'Unknown'];
  container.innerHTML = allUnits.map(u => {
    const isActive = selectedUnit === u;
    const color = u ? (UNIT_COLORS[u] || '#555') : null;
    const label = u ? `${u} <span style="opacity:0.7;font-weight:400">${counts[u] || 0}</span>` : 'All';
    const dotStyle = color ? `style="color:${isActive ? 'white' : color};margin-right:3px"` : '';
    const dot = color ? `<span ${dotStyle}>●</span>` : '';
    return `<button class="unit-btn${isActive ? ' active' : ''}" onclick="_filterUnit(${u ? `'${u}'` : 'null'})">${dot}${label}</button>`;
  }).join('');
}

window._filterUnit = function(unit) {
  selectedUnit = (selectedUnit === unit) ? null : unit;
  renderUnitFilter();
  refreshPills();
  filterChanged();
};

// ── BUILD UI ────────────────────────────────────────────────────────────
// ── COLLAPSIBLE SECTIONS ─────────────────────────────────────────────────
const SECTION_DEFAULTS = {
  infantry: true, vehicle: true,
  'unit-lb': false, 'mission-hist': false, 'veh-kills': false, weapons: false,
  maps: false, roles: false, attendance: false,
  'kd-trend': false, comparison: false,
};

function toggleSection(key) {
  const content = document.getElementById('sc-' + key);
  const arrow   = document.getElementById('arr-' + key);
  if (!content) return;
  const opening = content.style.display === 'none';
  content.style.display = opening ? '' : 'none';
  if (arrow) arrow.textContent = opening ? '▼' : '▶';
  try {
    const s = JSON.parse(localStorage.getItem('tfp-sections') || '{}');
    s[key] = opening;
    localStorage.setItem('tfp-sections', JSON.stringify(s));
  } catch(e) {}
}
window.toggleSection = toggleSection;

function initSectionStates() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('tfp-sections') || '{}'); } catch(e) {}
  Object.entries(SECTION_DEFAULTS).forEach(([key, defaultOpen]) => {
    const open    = key in saved ? saved[key] : defaultOpen;
    const content = document.getElementById('sc-' + key);
    const arrow   = document.getElementById('arr-' + key);
    if (content) content.style.display = open ? '' : 'none';
    if (arrow)   arrow.textContent = open ? '▼' : '▶';
  });
}

function buildUI() {
  document.getElementById("loading").style.display = "none";
  document.getElementById("content").style.display = "";
  document.getElementById("sectionNav").style.display = "";

  initSectionStates();
  buildFilters();
  applyFilters();

  // ── Deep-link: ?player=Name ──────────────────────────────────────────────
  const _dlPlayer = new URLSearchParams(window.location.search).get('player');
  const _dlCampaign = new URLSearchParams(window.location.search).get('campaign');
  if (_dlPlayer) openCareerPage(_dlPlayer);
  else if (_dlCampaign && campaignData[_dlCampaign]) openCampaignPage(_dlCampaign);

  window.addEventListener('popstate', e => {
    const name = e.state && e.state.player;
    const camp = e.state && e.state.campaign;
    if (camp && campaignData[camp]) {
      _showCampaignPage(camp);
    } else if (name) {
      _hideCampaignPage();
      _openCareerPageNoHistory(name);
    } else {
      _hideCampaignPage();
      _closeCareerPageNoHistory();
    }
  });
}

function _setCareerSubtitle(p) {
  const cml = p.missions ? [...p.missions].sort((a, b) => {
    const da = (a.match(/\((\d{4}-\d{2}-\d{2})\)/) || [])[1] || '';
    const db = (b.match(/\((\d{4}-\d{2}-\d{2})\)/) || [])[1] || '';
    return da.localeCompare(db);
  }) : [];
  const active = cml.length > 1 ? `${missionDate(cml[0])} – ${missionDate(cml[cml.length - 1])}` : (cml[0] ? missionDate(cml[0]) : '—');
  const aliases = nameAliases[p.name];
  document.getElementById('careerPlayerSub').innerHTML =
    `Combat Missions: ${p.missionCount}   ·   Active: ${active}` +
    (p.timePlayed ? `   ·   Time Played: ${fmtTime(p.timePlayed)}` : '') +
    (p.topRole ? `   ·   Top Role: ${p.topRole} (${p.topRoleCount})` : '') +
    (aliases && aliases.length
      ? `<br><span style="font-size:0.78em;opacity:0.55;font-style:italic">Also known as: ${aliases.join('  ·  ')}</span>`
      : '');
}

// Internal versions that update the DOM without pushing another history entry
function _openCareerPageNoHistory(playerName) {
  selectedPlayers = new Set([playerName]);
  applyFilters();
  const p = filteredPlayers.find(x => x.name === playerName);
  if (!p) return;
  document.getElementById('careerPlayerName').innerHTML = p.name + unitBadgeHTML(p.name);
  _setCareerSubtitle(p);
  document.getElementById('careerStats').innerHTML = buildCareerStatsHTML(p);
  renderMedalRibbons('medalContainer', new Set(((aggPlayers[p.name] || p).missionRows || []).map(r => r["Mission"] || "").filter(Boolean)));
  document.getElementById('statsBar').style.display                   = 'none';
  document.getElementById('awardsRow').style.display                  = 'none';
  document.getElementById('hallFameLabel').style.display              = 'none';
  document.getElementById('shameRow').style.display                   = 'none';
  document.getElementById('hallShameLabel').style.display             = 'none';
  document.querySelector('.chart-section').style.display              = 'none';
  document.querySelector('.filter-panel').style.display               = 'none';
  document.getElementById('unitLeaderboardSection').style.display     = 'none';
  document.getElementById('extraLeaderboardSections').style.display   = 'none';
  document.getElementById('careerHeader').style.display               = '';
  document.getElementById('careerStats').style.display                = '';
  refreshPills();
  window.scrollTo(0, 0);
}

function _closeCareerPageNoHistory() {
  selectedPlayers = null;
  document.getElementById('careerHeader').style.display               = 'none';
  document.getElementById('careerStats').style.display                = 'none';
  document.getElementById('statsBar').style.display                   = '';
  document.querySelector('.chart-section').style.display              = '';
  document.querySelector('.filter-panel').style.display               = '';
  document.getElementById('unitLeaderboardSection').style.display     = '';
  document.getElementById('extraLeaderboardSections').style.display   = '';
  applyFilters();
  refreshPills();
  window.scrollTo(0, 0);
}

// selectedPlayers / selectedMissions = null means "all", Set means explicit selection
let selectedPlayers   = null;
let selectedMissions  = null;
let selectedCampaign  = null; // null = no campaign selected, string = campaign name
let campaignData      = {};

function buildFilters() {
  // Wire up event type toggles
  document.getElementById("eventTypeSelect").onchange = () => {
    const val = document.getElementById("eventTypeSelect").value;
    showJointOps      = val === "both" || val === "joint";
    showRegularEvents = val === "both" || val === "regular";
    selectedPlayers  = null;
    selectedMissions = null;
    selectedCampaign = null;
    refreshPills();
    filterChanged();
  };

  document.getElementById("resetFilters").onclick = () => {
    showJointOps      = true;
    showRegularEvents = true;
    selectedPlayers   = null;
    selectedMissions  = null;
    selectedCampaign  = null;
    selectedUnit      = null;
    document.getElementById("playerSearch").value   = "";
    document.getElementById("missionSearch").value  = "";
    document.getElementById("eventTypeSelect").value = "both";
    document.getElementById("zeusFilterSelect").value = "all";
    zeusFilter = "all";
    refreshPills();
    filterChanged();
  };

  document.getElementById("playerSearch").oninput  = refreshPills;
  document.getElementById("missionSearch").oninput = refreshPills;
  document.getElementById("zeusFilterSelect").onchange = () => {
    zeusFilter = document.getElementById("zeusFilterSelect").value;
    selectedPlayers = null; selectedMissions = null; selectedCampaign = null;
    refreshPills(); filterChanged();
  };

  refreshPills();
}

function getEventFilteredRows() {
  return rawRows.filter(r => {
    const src = r["Source File"] || "";
    const isLarge = isJointOp(src);
    return isLarge ? showJointOps : showRegularEvents;
  });
}

function getCampaignMissions(campaignName) {
  const camp = campaignData[campaignName];
  if (!camp) return [];
  const missions = new Set(camp.missions || []);
  if (camp.patterns) {
    const eventRows = getEventFilteredRows();
    const availMissions = [...new Set(eventRows.map(r => r["Mission"] || "").filter(Boolean))];
    camp.patterns.forEach(pattern => {
      availMissions.filter(m => m.includes(pattern)).forEach(m => missions.add(m));
    });
  }
  return Array.from(missions);
}

function refreshPills() {
  const eventRows = getEventFilteredRows();

  // Available missions given current event type
  const availMissions = [...new Set(eventRows.map(r => r["Mission"] || "").filter(Boolean))].sort();

  // Available players given current event type + selected missions
  const missionFiltered = selectedMissions
    ? eventRows.filter(r => selectedMissions.has(r["Mission"] || ""))
    : eventRows;
  const availPlayers = [...new Set(missionFiltered.map(r => nameMap[r["Username"] || ""] || r["Username"] || "").filter(Boolean))]
    .filter(name => selectedUnit === null || (playerUnits[name] || 'Unknown') === selectedUnit)
    .sort();

  renderSelectablePills("missionPills", availMissions, selectedMissions, "missionSearch", (item, set) => {
    if (set === null) {
      // First explicit click — select only this one
      selectedMissions = new Set([item]);
    } else if (set.has(item)) {
      set.delete(item);
      // If nothing left selected, go back to "all"
      selectedMissions = set.size > 0 ? set : null;
    } else {
      set.add(item);
    }
    // Reset player selection when mission filter changes
    selectedPlayers = null;
    selectedCampaign = null;
    refreshPills();
    filterChanged();
  });

  // Campaign pills
  const campaigns = Object.keys(campaignData).filter(k => !k.startsWith('_')).sort();
  const campaignContainer = document.getElementById("campaignPills");
  if (campaignContainer) {
    campaignContainer.innerHTML = "";
    campaigns.forEach(campaignName => {
      const isActive = selectedCampaign === campaignName;
      const pill = document.createElement("span");
      pill.className = "pill" + (isActive ? " active" : "");
      pill.textContent = campaignName;
      const campBtn = document.createElement("button");
      campBtn.className = "career-icon-btn";
      campBtn.title = "Open campaign page";
      campBtn.textContent = "📊";
      campBtn.onclick = ev => { ev.stopPropagation(); openCampaignPage(campaignName); };
      pill.appendChild(campBtn);
      pill.oncontextmenu = ev => { ev.preventDefault(); openCampaignPage(campaignName); };
      pill.onclick = () => {
        if (selectedCampaign === campaignName) {
          selectedCampaign = null;
          selectedMissions = null;
        } else {
          selectedCampaign = campaignName;
          const missions = getCampaignMissions(campaignName);
          selectedMissions = new Set(missions);
        }
        selectedPlayers = null;
        refreshPills();
        filterChanged();
      };
      campaignContainer.appendChild(pill);
    });
  }

  renderCareerPills("playerPills", availPlayers, "playerSearch");
}

function renderCareerPills(containerId, items, searchId) {
  const container = document.getElementById(containerId);
  const searchVal = document.getElementById(searchId).value.toLowerCase();
  container.innerHTML = "";
  items
    .filter(item => !searchVal || item.toLowerCase().includes(searchVal))
    .forEach(item => {
      const isActive = selectedPlayers === null || selectedPlayers.has(item);
      const pill = document.createElement("span");
      pill.className = "pill" + (isActive ? " active" : "");
      pill.textContent = item;
      pill.onclick = () => filterToPlayer(item);
      pill.oncontextmenu = (e) => { e.preventDefault(); openCareerPage(item); };
      container.appendChild(pill);
    });
}

function renderSelectablePills(containerId, items, selectedSet, searchId, onSelect) {
  // selectedSet === null means "all active"; a Set means explicit selection
  const container = document.getElementById(containerId);
  const searchVal = document.getElementById(searchId).value.toLowerCase();
  container.innerHTML = "";
  items
    .filter(item => !searchVal || item.toLowerCase().includes(searchVal))
    .forEach(item => {
      const isActive = selectedSet === null || selectedSet.has(item);
      const pill = document.createElement("span");
      pill.className = "pill" + (isActive ? " active" : "");
      pill.textContent = item;
      pill.onclick = () => onSelect(item, selectedSet);
      container.appendChild(pill);
    });
}

function filterChanged() {
  applyFilters();
}

function applyFilters() {
  const filtered = rawRows.filter(r => {
    const rawName = r["Username"] || "";
    const name    = nameMap[rawName] || rawName;
    const mission = r["Mission"]  || "";
    const srcFile = r["Source File"] || "";
    const isLarge = isJointOp(srcFile);
    const eventTypeOk = isLarge ? showJointOps : showRegularEvents;
    const playerOk    = selectedPlayers  === null || selectedPlayers.has(name);
    const missionOk   = selectedMissions === null || selectedMissions.has(mission);
    const playerUnit  = playerUnits[name] || 'Unknown';
    const unitOk      = selectedUnit === null || playerUnit === selectedUnit;
    return eventTypeOk && playerOk && missionOk && unitOk;
  });

  // Re-aggregate only for filtered rows
  const tempAgg = {};
  filtered.forEach(r => {
    const rawName = r["Username"] || "";
    const name = nameMap[rawName] || rawName;
    if (!name) return;
    if (!tempAgg[name]) {
      tempAgg[name] = {
        name,
        missions: new Set(),
        killsOnFoot: 0, deathsOnFoot: 0, tkOnFoot: 0,
        shotsOnFoot: 0, hitsOnFoot: 0, hitsDealtOnFoot: 0, suicides: 0, distanceRun: 0, timePlayed: 0,
        maxLongestFoot: 0, avgDistFootSum: 0, avgDistFootN: 0,
        killsInVeh: 0, deathsInVeh: 0, tkInVeh: 0,
        vehKillsFoot: 0, vehKillsVeh: 0,
        shotsInVeh: 0, hitsInVeh: 0, hitsDealtInVeh: 0,
        maxLongestVeh: 0, avgDistVehSum: 0, avgDistVehN: 0,
        weaponKills: {},   // weapon -> total kills across missions
        missionRows: [],   // raw per-mission rows for modal
        roleCounts: {},    // normalized role -> appearances
      };
    }
    const p = tempAgg[name];
    const mission = r["Mission"] || "";
    p.missions.add(mission);

    const rawRole = r["Role"] || r["Group"] || "";
    if (rawRole) {
      const normRole = normalizeRole(rawRole);
      if (normRole) p.roleCounts[normRole] = (p.roleCounts[normRole] || 0) + 1;
    }

    const kof  = NUM(r["Kills (On Foot)"]);
    const dof  = NUM(r["Deaths (On Foot)"]);
    const tkof = NUM(r["Teamkills (On Foot)"]);
    const sof  = NUM(r["Shots (On Foot)"]);
    const hof  = NUM(r["Hits Taken (On Foot)"]);
    const hdof = NUM(r["Hits Dealt (On Foot)"]);
    const lof  = footLongest(NUM(r["Longest Kill On Foot (m)"]));
    const aof  = NUM(r["Avg Kill Dist On Foot (m)"]);
    const kiv  = NUM(r["Kills (In Vehicle)"]);
    const div2 = NUM(r["Deaths (In Vehicle)"]);
    const tkiv = NUM(r["Teamkills (In Vehicle)"]);
    const vkof = NUM(r["Vehicle Kills (On Foot)"]);
    const vkiv = NUM(r["Vehicle Kills (In Vehicle)"]);
    const siv  = NUM(r["Shots (In Vehicle)"]);
    const hiv  = NUM(r["Hits Taken (In Vehicle)"]);
    const hdiv = NUM(r["Hits Dealt (In Vehicle)"]);
    const liv  = NUM(r["Longest Kill In Vehicle (m)"]);
    const aiv  = NUM(r["Avg Kill Dist In Vehicle (m)"]);

    p.killsOnFoot      += kof; p.deathsOnFoot += dof; p.tkOnFoot += tkof;
    p.shotsOnFoot      += sof; p.hitsOnFoot   += hof; p.hitsDealtOnFoot += hdof;
    p.suicides     += NUM((r["Suicides"] || r["Suicides\r"] || "0"));
    p.distanceRun  += NUM(r["Distance Run (km)"] || r["Distance Run (km)\r"] || "0");
    p.timePlayed   += NUM(r["Time Played (s)"] || r["Time Played (s)\r"] || "0");
    const topWeaponRow = r["Top Weapon"] || r["Top Weapon\r"] || "";
    if (lof > p.maxLongestFoot) { p.maxLongestFoot = lof; p.longestKillWeapon = topWeaponRow; }
    if (kof > 0 && aof > 0) { p.avgDistFootSum += aof * kof; p.avgDistFootN += kof; }

    p.killsInVeh  += kiv; p.deathsInVeh += div2; p.tkInVeh += tkiv;
    p.vehKillsFoot+= vkof; p.vehKillsVeh += vkiv;
    p.shotsInVeh  += siv; p.hitsInVeh += hiv; p.hitsDealtInVeh += hdiv;
    if (liv > p.maxLongestVeh) p.maxLongestVeh = liv;
    if (kiv > 0 && aiv > 0) { p.avgDistVehSum += aiv * kiv; p.avgDistVehN += kiv; }

    // Weapons + mission rows (for modal)
    try {
      const weaponJson = r["Weapon Kills (JSON)"] || r["Weapon Kills (JSON)\r"] || "{}";
      const wmap = JSON.parse(weaponJson);
      Object.entries(wmap).forEach(([w, c]) => {
        const weapon = w.replace(/\s*\[[^\]]+\]$/, '');
        p.weaponKills[weapon] = (p.weaponKills[weapon] || 0) + c;
      });
    } catch(e) {}
    p.missionRows.push(r);
  });

  Object.values(tempAgg).forEach(p => {
    p.kdFoot  = p.deathsOnFoot > 0 ? p.killsOnFoot / p.deathsOnFoot : p.killsOnFoot;
    p.kdVeh   = p.deathsInVeh  > 0 ? p.killsInVeh  / p.deathsInVeh  : p.killsInVeh;
    p.spkFoot      = p.killsOnFoot > 0 ? p.shotsOnFoot / p.killsOnFoot : null;
    p.spkVeh       = p.killsInVeh  > 0 ? p.shotsInVeh  / p.killsInVeh  : null;
    p.accuracyFoot = p.shotsOnFoot > 0 ? (p.hitsDealtOnFoot / p.shotsOnFoot) * 100 : null;
    p.accuracyVeh  = p.shotsInVeh  > 0 ? (p.hitsDealtInVeh  / p.shotsInVeh)  * 100 : null;
    p.avgDistFoot = p.avgDistFootN > 0 ? p.avgDistFootSum / p.avgDistFootN : 0;
    p.avgDistVeh  = p.avgDistVehN  > 0 ? p.avgDistVehSum  / p.avgDistVehN  : 0;
    p.missionCount = p.missions.size;
    const _rs = Object.entries(p.roleCounts || {}).sort((a, b) => b[1] - a[1]);
    p.topRole = _rs.length > 0 ? _rs[0][0] : null;
    p.topRoleCount = _rs.length > 0 ? _rs[0][1] : 0;
  });

  filteredPlayers = Object.values(tempAgg).filter(p => {
    const playerOk = selectedPlayers === null || selectedPlayers.has(p.name);
    const isZeus = p.missionRows && p.missionRows.some(r => rowIsZeus(r));
    const zeusOk = zeusFilter === "all"
      || (zeusFilter === "no-zeus"   && !isZeus)
      || (zeusFilter === "zeus-only" &&  isZeus);
    return playerOk && zeusOk;
  });

  // Align filteredRows with filteredPlayers (respects zeus filter too)
  const _fpNames = new Set(filteredPlayers.map(p => p.name));
  filteredRows = filtered.filter(r => _fpNames.has(r["Username"] || ""));

  const totalCount = Object.keys(aggPlayers).length;
  const parts = [];
  if (selectedCampaign) parts.push(`Campaign: ${selectedCampaign}`);
  if (selectedMissions && !selectedCampaign) parts.push(selectedMissions.size === 1 ? [...selectedMissions][0] : `${selectedMissions.size} missions`);
  if (selectedPlayers)  parts.push(selectedPlayers.size  === 1 ? [...selectedPlayers][0]  : `${selectedPlayers.size} players`);
  if (selectedUnit)     parts.push(selectedUnit);
  const evVal = document.getElementById("eventTypeSelect") ? document.getElementById("eventTypeSelect").value : "both";
  if (evVal !== "both") parts.push(evVal === "joint" ? "Joint Op only" : "Regular Op only");
  document.getElementById("filterCount").textContent =
    parts.length ? `Filtered: ${parts.join(" · ")}` : `${filteredPlayers.length} players`;

  const badge = document.getElementById("filterBadge");
  if (badge) {
    if (parts.length) {
      const totalCount = Object.keys(aggPlayers).length;
      badge.style.display = 'flex';
      badge.innerHTML = `<span>⚡ Showing <strong>${filteredPlayers.length}</strong> of <strong>${totalCount}</strong> players &nbsp;·&nbsp; ${parts.map(p => `<span style="background:#ffe082;padding:1px 8px;border-radius:12px;font-weight:600">${p}</span>`).join(' ')}</span><button onclick="document.getElementById('resetFilters').click()" style="background:none;border:1px solid #cca800;border-radius:4px;padding:2px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap;font-family:'Open Sans',sans-serif">× Clear</button>`;
    } else {
      badge.style.display = 'none';
    }
  }

  renderStats();
  renderChart();
  renderLeader();
  renderInfantryTable();
  renderVehicleTable();
  renderUnitLeaderboard();
  renderMissionHistory();
  renderVehicleKillsSection();
  renderWeaponLeaderboard();
  renderWorldStats();
  renderRoleLeaderboard();
  renderAttendanceTracker();
  refreshKdTrendSubjects();
  renderKdTrend();
  refreshCompareSelects();
  renderComparison();
  renderUnitFilter();
}

// ── STATS BAR ────────────────────────────────────────────────────────────
function renderStats() {
  const totKillsFoot = filteredPlayers.reduce((s,p) => s + p.killsOnFoot, 0);
  const totDeaths    = filteredPlayers.reduce((s,p) => s + p.deathsOnFoot + p.deathsInVeh, 0);
  const totTK        = filteredPlayers.reduce((s,p) => s + p.tkOnFoot + p.tkInVeh, 0);
  const totVehKills  = filteredPlayers.reduce((s,p) => s + p.vehKillsFoot + p.vehKillsVeh, 0);
  const totKillsVeh  = filteredPlayers.reduce((s,p) => s + p.killsInVeh, 0);
  const missions     = new Set(filteredPlayers.flatMap(p => [...p.missions])).size;
  const avgKD        = filteredPlayers.length ? (filteredPlayers.reduce((s,p)=>s+p.kdFoot,0)/filteredPlayers.length).toFixed(2) : "0.00";

  const cards = [
    { val: totKillsFoot, lbl: "Infantry Kills" },
    { val: totKillsVeh,  lbl: "Kills from Vehicles" },
    { val: totDeaths,    lbl: "Total Deaths" },
    { val: totTK,        lbl: "Teamkills" },
    { val: totVehKills,  lbl: "Vehicles Destroyed" },
    { val: filteredPlayers.length, lbl: "Players" },
    { val: missions,     lbl: "Missions" },
    { val: avgKD,        lbl: "Avg K/D (Infantry)" },
  ];
  document.getElementById("statsBar").innerHTML = cards.map(c =>
    `<div class="stat-card"><div class="val">${c.val}</div><div class="lbl">${c.lbl}</div></div>`
  ).join("");
}

// ── CHART ────────────────────────────────────────────────────────────────
function renderChart() {
  const top = [...filteredPlayers].sort((a,b) => b.killsOnFoot - a.killsOnFoot).slice(0,10);
  if (!top.length) { document.getElementById("chartBars").innerHTML = ""; return; }
  const max = top[0].killsOnFoot || 1;
  document.getElementById("chartBars").innerHTML = top.map(p =>
    `<div class="bar-row">
      <div class="bar-label" title="${p.name}">${p.name}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(p.killsOnFoot/max*100).toFixed(1)}%"></div></div>
      <div class="bar-val">${p.killsOnFoot}</div>
    </div>`
  ).join("");
}

// ── AWARD CARDS ──────────────────────────────────────────────────────────
function renderLeader() {
  const row       = document.getElementById("awardsRow");
  const shameRow  = document.getElementById("shameRow");
  const fameLabel = document.getElementById("hallFameLabel");
  const shameLabel= document.getElementById("hallShameLabel");
  const eligible  = filteredPlayers.filter(p => p.killsOnFoot > 0);
  if (!eligible.length) {
    row.style.display = shameRow.style.display = "none";
    fameLabel.style.display = shameLabel.style.display = "none";
    return;
  }
  row.style.display = shameRow.style.display = "flex";
  fameLabel.style.display = shameLabel.style.display = "";

  // Executioner — most on-foot kills
  const byKills = [...eligible].sort((a,b) => b.killsOnFoot - a.killsOnFoot)[0];
  document.getElementById("aw-kills-name").textContent = byKills.name;
  document.getElementById("aw-kills-stat").textContent =
    `${byKills.killsOnFoot} kills · K/D ${byKills.kdFoot.toFixed(2)}`;
  document.getElementById("aw-kills-stat").dataset.avg =
    `${(byKills.killsOnFoot / byKills.missions.size).toFixed(1)} kills/mission (${byKills.missions.size} missions)`;

  // Pro Sniper — highest maxLongestFoot (min 2 kills to avoid flukes)
  const byLong = [...eligible].filter(p => p.killsOnFoot >= 2)
    .sort((a,b) => b.maxLongestFoot - a.maxLongestFoot)[0];
  if (byLong && byLong.maxLongestFoot > 0) {
    document.getElementById("aw-long-name").textContent = byLong.name;
    const longWeapon = byLong.longestKillWeapon ? ` · ${byLong.longestKillWeapon}` : "";
    document.getElementById("aw-long-stat").textContent =
      `${byLong.maxLongestFoot} m · avg ${Math.round(byLong.avgDistFoot)} m${longWeapon}`;
  } else {
    document.getElementById("aw-long-name").textContent = "—";
    document.getElementById("aw-long-stat").textContent = "";
  }

  // Perfect Aim — lowest shots per kill (min 3 kills to filter noise)
  // spkFoot >= 1 means at least 1 shot fired per kill (no knife/0-shot kills skewing it)
  const bySpk = [...eligible].filter(p => p.killsOnFoot >= 3 && p.spkFoot != null && p.spkFoot >= 1)
    .sort((a,b) => a.spkFoot - b.spkFoot)[0];
  if (bySpk) {
    document.getElementById("aw-spk-name").textContent = bySpk.name;
    document.getElementById("aw-spk-stat").textContent =
      `${bySpk.spkFoot.toFixed(1)} shots/kill · ${bySpk.killsOnFoot} kills`;
  } else {
    document.getElementById("aw-spk-name").textContent = "—";
    document.getElementById("aw-spk-stat").textContent = "";
  }

  // K/D Player — highest kdFoot (min 3 kills)
  const byKda = [...eligible].filter(p => p.killsOnFoot >= 3)
    .sort((a,b) => b.kdFoot - a.kdFoot)[0];
  if (byKda) {
    document.getElementById("aw-kda-name").textContent = byKda.name;
    document.getElementById("aw-kda-stat").textContent =
      `${byKda.kdFoot.toFixed(2)} K/D · ${byKda.killsOnFoot}K / ${byKda.deathsOnFoot}D`;
  } else {
    document.getElementById("aw-kda-name").textContent = "—";
    document.getElementById("aw-kda-stat").textContent = "";
  }

  // Always On Duty — most time played
  const byTime = [...filteredPlayers].filter(p => p.timePlayed > 0)
    .sort((a,b) => b.timePlayed - a.timePlayed)[0];
  if (byTime) {
    document.getElementById("aw-time-name").textContent = byTime.name;
    document.getElementById("aw-time-stat").textContent = fmtTime(byTime.timePlayed);
  } else {
    document.getElementById("aw-time-name").textContent = "—";
    document.getElementById("aw-time-stat").textContent = "";
  }

  // Ultra Runner — most distance run
  const byDist = [...filteredPlayers].filter(p => p.distanceRun > 0)
    .sort((a,b) => b.distanceRun - a.distanceRun)[0];
  if (byDist) {
    document.getElementById("aw-dist-name").textContent = byDist.name;
    const awDistStat = document.getElementById("aw-dist-stat");
    awDistStat.textContent = `${byDist.distanceRun.toFixed(1)} km`;
    awDistStat.dataset.avg = `${(byDist.distanceRun / byDist.missions.size).toFixed(1)} km/mission (${byDist.missions.size} missions)`;
  } else {
    document.getElementById("aw-dist-name").textContent = "—";
    document.getElementById("aw-dist-stat").textContent = "";
  }

  // ── HALL OF SHAME ────────────────────────────────────────────────────

  // Cannon Fodder — worst K/D (min 3 kills to avoid flukes, must have died at least once)
  const shameKd = [...eligible].filter(p => p.killsOnFoot >= 3 && p.deathsOnFoot > 0)
    .sort((a,b) => a.kdFoot - b.kdFoot)[0];
  if (shameKd) {
    document.getElementById("sh-kd-name").textContent = shameKd.name;
    document.getElementById("sh-kd-stat").textContent =
      `${shameKd.kdFoot.toFixed(2)} K/D · ${shameKd.killsOnFoot}K / ${shameKd.deathsOnFoot}D`;
  } else {
    document.getElementById("sh-kd-name").textContent = "—";
    document.getElementById("sh-kd-stat").textContent = "";
  }

  // Most Valuable Opponent — most total teamkills (on foot + vehicle)
  const shameTk = [...filteredPlayers]
    .map(p => ({ ...p, totalTK: p.tkOnFoot + p.tkInVeh }))
    .filter(p => p.totalTK > 0)
    .sort((a,b) => b.totalTK - a.totalTK)[0];
  if (shameTk) {
    document.getElementById("sh-tk-name").textContent = shameTk.name;
    document.getElementById("sh-tk-stat").textContent =
      `${shameTk.totalTK} teamkills (${shameTk.tkOnFoot} foot · ${shameTk.tkInVeh} veh)`;
  } else {
    document.getElementById("sh-tk-name").textContent = "—";
    document.getElementById("sh-tk-stat").textContent = "";
  }

  // Spray & Pray Specialist — highest shots per kill on foot (min 3 kills, spkFoot >= 1)
  const shameSpk = [...eligible].filter(p => p.killsOnFoot >= 20 && p.missions.size >= 2 && p.spkFoot != null && p.spkFoot >= 1)
    .sort((a,b) => b.spkFoot - a.spkFoot)[0];
  if (shameSpk) {
    document.getElementById("sh-spk-name").textContent = shameSpk.name;
    document.getElementById("sh-spk-stat").textContent =
      `${shameSpk.spkFoot.toFixed(1)} shots/kill · ${shameSpk.killsOnFoot} kills`;
  } else {
    document.getElementById("sh-spk-name").textContent = "—";
    document.getElementById("sh-spk-stat").textContent = "";
  }

  // Bullet Magnet — most hits taken on foot
  const shameHits = [...filteredPlayers].filter(p => p.hitsOnFoot > 0)
    .sort((a,b) => b.hitsOnFoot - a.hitsOnFoot)[0];
  if (shameHits) {
    document.getElementById("sh-hits-name").textContent = shameHits.name;
    const shHitsStat = document.getElementById("sh-hits-stat");
    shHitsStat.textContent = `${shameHits.hitsOnFoot} hits taken`;
    shHitsStat.dataset.avg = `${(shameHits.hitsOnFoot / shameHits.missions.size).toFixed(1)} hits/mission (${shameHits.missions.size} missions)`;
  } else {
    document.getElementById("sh-hits-name").textContent = "—";
    document.getElementById("sh-hits-stat").textContent = "";
  }

  // Tactically Unfortunate — most suicides
  const shameSui = [...filteredPlayers].filter(p => p.suicides > 0)
    .sort((a,b) => b.suicides - a.suicides)[0];
  if (shameSui) {
    document.getElementById("sh-sui-name").textContent = shameSui.name;
    document.getElementById("sh-sui-stat").textContent = `${shameSui.suicides} suicides`;
  } else {
    document.getElementById("sh-sui-name").textContent = "—";
    document.getElementById("sh-sui-stat").textContent = "";
  }

  // Body Bag Collector — most deaths on foot
  const shameDeaths = [...filteredPlayers].filter(p => p.deathsOnFoot > 0)
    .sort((a,b) => b.deathsOnFoot - a.deathsOnFoot)[0];
  if (shameDeaths) {
    document.getElementById("sh-deaths-name").textContent = shameDeaths.name;
    const shDeathsStat = document.getElementById("sh-deaths-stat");
    shDeathsStat.textContent = `${shameDeaths.deathsOnFoot} deaths`;
    shDeathsStat.dataset.avg = `${(shameDeaths.deathsOnFoot / shameDeaths.missions.size).toFixed(1)} deaths/mission (${shameDeaths.missions.size} missions)`;
  } else {
    document.getElementById("sh-deaths-name").textContent = "—";
    document.getElementById("sh-deaths-stat").textContent = "";
  }

  // Full-Auto Philosopher — most bullets fired on foot
  const shameShots = [...filteredPlayers].filter(p => p.shotsOnFoot > 0)
    .sort((a,b) => b.shotsOnFoot - a.shotsOnFoot)[0];
  if (shameShots) {
    document.getElementById("sh-shots-name").textContent = shameShots.name;
    const shShotsStat = document.getElementById("sh-shots-stat");
    shShotsStat.textContent = `${shameShots.shotsOnFoot.toLocaleString()} shots fired`;
    const shotsCost = (shameShots.shotsOnFoot * 0.50).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    shShotsStat.dataset.avg = `${Math.round(shameShots.shotsOnFoot / shameShots.missions.size).toLocaleString()} shots/mission (${shameShots.missions.size} missions) · €${shotsCost} in bullets`;
  } else {
    document.getElementById("sh-shots-name").textContent = "—";
    document.getElementById("sh-shots-stat").textContent = "";
  }

  // Passenger Princess — least distance run (min 1h30m played to filter out short sessions)
  const shameDist = [...filteredPlayers].filter(p => p.timePlayed >= 5400 && p.distanceRun > 0
    && !p.missionRows.some(r => rowIsZeus(r)))
    .sort((a,b) => a.distanceRun - b.distanceRun)[0];
  if (shameDist) {
    document.getElementById("sh-dist-name").textContent = shameDist.name;
    const shDistStat = document.getElementById("sh-dist-stat");
    shDistStat.textContent = `${shameDist.distanceRun.toFixed(1)} km`;
    shDistStat.dataset.avg = `${(shameDist.distanceRun / shameDist.missions.size).toFixed(1)} km/mission (${shameDist.missions.size} missions)`;
  } else {
    document.getElementById("sh-dist-name").textContent = "—";
    document.getElementById("sh-dist-stat").textContent = "";
  }
}


// ── PLAYER MODAL ─────────────────────────────────────────────────────────
function closeModal(e) {
  if (e.target === document.getElementById('playerModal')) {
    document.getElementById('playerModal').classList.remove('open');
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.getElementById('playerModal').classList.remove('open');
});

// ── AVG-PER-MISSION TOOLTIP (hover on award stats) ───────────────────────
const avgTooltip = document.getElementById('avgTooltip');
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-avg]');
  if (!el) return;
  avgTooltip.textContent = 'Avg: ' + el.dataset.avg;
  avgTooltip.style.display = 'block';
});
document.addEventListener('mousemove', e => {
  if (avgTooltip.style.display === 'none') return;
  avgTooltip.style.left = (e.clientX + 12) + 'px';
  avgTooltip.style.top  = (e.clientY + 12) + 'px';
});
document.addEventListener('mouseout', e => {
  if (e.target.closest('[data-avg]')) avgTooltip.style.display = 'none';
});
document.addEventListener('scroll', () => avgTooltip.style.display = 'none', true);

// ── SHARED INFANTRY STAT COLUMNS ─────────────────────────────────────────
// Single source of truth for infantry stats. Both the main leaderboard
// (INF_COLS) and the per-mission breakdown (MISSION_COLS) are derived
// from this array. To add/remove/rename a stat, edit only this array.
//
// aggKey    – key on the aggregated player object (used in INF leaderboard)
// missionKey – key on the per-mission row object (used in mission breakdown)
// afterMissions – if true, column appears after the "Missions" column in INF
const SHARED_INF_STAT_COLS = [
  { label: "Kills",         aggKey: "killsOnFoot",    missionKey: "k",    numeric: true,  canAvg: true },
  { label: "Veh Kills",     aggKey: "vehKillsFoot",   missionKey: "vk",   numeric: true,  fmt: v => v || "—", canAvg: true },
  { label: "Deaths",        aggKey: "deathsOnFoot",   missionKey: "d",    numeric: true,  canAvg: true },
  { label: "K/D",           aggKey: "kdFoot",         missionKey: "_kd",  numeric: true,  fmt: v => v != null ? v.toFixed(2) : "—", css: kdClass },
  { label: "TK",            aggKey: "tkOnFoot",       missionKey: "tk",   numeric: true,  fmt: v => v || "—", css: tkClass, canAvg: true },
  { label: "Suicides",      aggKey: "suicides",       missionKey: "sui",  numeric: true,  fmt: v => v || "—", css: v => v > 0 ? "tk-cell" : "", canAvg: true },
  { label: "Shots",         aggKey: "shotsOnFoot",    missionKey: "sh",   numeric: true,  canAvg: true },
  { label: "Hits Taken",    aggKey: "hitsOnFoot",     missionKey: "ht",   numeric: true,  fmt: v => v || "—", canAvg: true },
  { label: "Hits Dealt",    aggKey: "hitsDealtOnFoot",missionKey: "hd",   numeric: true,  fmt: v => v || "—", canAvg: true },
  { label: "Accuracy",      aggKey: "accuracyFoot",   missionKey: "_acc", numeric: true,  fmt: v => v != null ? v.toFixed(1) + "%" : "—" },
  { label: "Shots/Kill",    aggKey: "spkFoot",        missionKey: "_spk", numeric: true,  fmt: v => v != null ? v.toFixed(1) : "—" },
  { label: "Avg Dist (m)",  aggKey: "avgDistFoot",    missionKey: "_ad",  numeric: true,  fmt: v => v ? Math.round(v) : "—" },
  { label: "Longest (m)",   aggKey: "maxLongestFoot", missionKey: "lk",   numeric: true,  fmt: v => v || "—" },
  { label: "Dist Run (km)", aggKey: "distanceRun",    missionKey: "dr",   numeric: true,  fmt: v => v ? v.toFixed(1) : "—", canAvg: true, afterMissions: true },
  { label: "Time Played",   aggKey: "timePlayed",     missionKey: "tp",   numeric: true,  fmt: v => v ? fmtTime(v) : "—", afterMissions: true },
];

// ── PER-MISSION TABLE SORT STATE ─────────────────────────────────────────
const MISSION_COLS = [
  { label: "Mission", key: "mission", numeric: false },
  ...SHARED_INF_STAT_COLS.map(c => ({ label: c.label, key: c.missionKey, numeric: c.numeric, fmt: c.fmt, css: c.css })),
];
let missionSortCol = 1; // default: Kills
let missionSortAsc = false;
let _missionData   = [];

function _buildMissionThead() {
  return `<tr>${MISSION_COLS.map((c, i) => {
    const arrow = i === missionSortCol ? (missionSortAsc ? " ▲" : " ▼") : " ⇅";
    return `<th onclick="window._sortMission(${i})">${c.label}<span class="sort-arrow">${arrow}</span></th>`;
  }).join("")}</tr>`;
}

function _buildMissionTbody(data) {
  const col = MISSION_COLS[missionSortCol];
  const sorted = [...data].sort((a, b) => {
    let va = a[col.key], vb = b[col.key];
    if (va == null) va = missionSortAsc ? Infinity : -Infinity;
    if (vb == null) vb = missionSortAsc ? Infinity : -Infinity;
    if (!col.numeric) return missionSortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    return missionSortAsc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });
  return sorted.map((m, i) => {
    const bg = i % 2 === 1 ? 'background:#f9f9f9' : '';
    const cells = MISSION_COLS.map(c => {
      const raw = m[c.key];
      const val = c.fmt ? c.fmt(raw) : (raw == null || raw === "" ? "—" : raw);
      const cls = c.css ? c.css(raw) : "";
      return `<td${cls ? ` class="${cls}"` : ""}>${val}</td>`;
    });
    return `<tr style="${bg}">${cells.join("")}</tr>`;
  }).join("");
}

window._sortMission = function(col) {
  if (missionSortCol === col) missionSortAsc = !missionSortAsc;
  else { missionSortCol = col; missionSortAsc = !MISSION_COLS[col].numeric; }
  // Use querySelectorAll so both modal and career page (which share the DOM)
  // are updated — getElementById only returns the first match and would miss
  // whichever context isn't first in document order.
  document.querySelectorAll(".js-mission-thead").forEach(el => { el.innerHTML = _buildMissionThead(); });
  document.querySelectorAll(".js-mission-tbody").forEach(el => { el.innerHTML = _buildMissionTbody(_missionData); });
};

// ── CAMPAIGN MEDAL RIBBONS ────────────────────────────────────────────────
// Ribbon generator adapted from https://github.com/Mchl/medal-bar-generator (MIT)

const RIBBON_W = 140, RIBBON_H = 38;

function _mash() {
  let n = 0xefc8249d;
  return function(data) {
    data = String(data);
    for (let i = 0; i < data.length; i++) {
      n += data.charCodeAt(i);
      let h = 0.02519603282416938 * n;
      n = h >>> 0; h -= n; h *= n; n = h >>> 0; h -= n; n += h * 0x100000000;
    }
    return (n >>> 0) * 2.3283064365386963e-10;
  };
}

function _aleaSeed(seed) {
  const mash = _mash();
  let s0 = mash(' '), s1 = mash(' '), s2 = mash(' '), c = 1;
  s0 -= mash(seed); if (s0 < 0) s0 += 1;
  s1 -= mash(seed); if (s1 < 0) s1 += 1;
  s2 -= mash(seed); if (s2 < 0) s2 += 1;
  return function() {
    const t = 2091639 * s0 + c * 2.3283064365386963e-10;
    s0 = s1; s1 = s2;
    return s2 = t - (c = t | 0);
  };
}

const _RIBBON_COLORS = [
  [255,205,0],[213,0,50],[40,71,52],[183,201,211],[12,35,64],[104,210,223],
  [123,175,212],[17,87,64],[134,38,51],[139,111,78],[96,61,32],[185,151,91],
  [227,82,5],[186,12,47],[242,199,92],[0,32,91],[0,45,114],[165,0,80],
  [0,38,58],[221,203,164],[100,167,11],[4,30,66],[255,88,93],[154,219,232],
  [111,38,61],[74,119,41],[133,113,77],[184,97,37],[255,158,27],[255,198,88],
  [84,88,90],[33,87,50],[0,193,213],[0,61,165],[0,132,61],[253,210,110],
  [176,170,126],[251,221,64],[91,127,149],[233,223,151],[127,48,53],[228,0,43],
  [87,41,50],[164,214,94],[122,154,1],[0,122,51],[255,209,0],[1,33,105],
  [132,117,78],[78,91,49],[252,76,2],[0,114,206],[229,114,0],[136,219,223],
  [0,146,188],[0,154,68],[67,176,42],[95,37,159],[178,168,162],[0,111,98],
  [158,162,162],[115,56,29],[197,232,108],[0,76,69],[253,218,36],[124,135,142],
  [137,144,100],[0,62,81],[150,56,33],[155,90,26],[0,20,137],[0,75,135],
  [0,114,206],[255,199,44]
];

function drawCampaignRibbon(canvas, campaignName) {
  const prng = _aleaSeed(campaignName);
  const ctx = canvas.getContext('2d');

  const paletteSize = 3 + Math.floor(prng() * 3);
  const palette = Array.from({length: paletteSize}, () =>
    _RIBBON_COLORS[Math.floor(prng() * _RIBBON_COLORS.length)]);
  const getColor = () => palette[Math.floor(prng() * palette.length)];

  if (prng() < 0.5) {
    // Symmetric odd: center stripe + mirrored pairs
    const n = 2 + Math.floor(prng() * 5);
    const w = Math.round(RIBBON_W / (1 + 2 * (n - 1)));
    for (let i = 0; i < n; i++) {
      const [r,g,b] = getColor();
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(i * w, 0, w, RIBBON_H);
      ctx.fillRect(RIBBON_W - (i + 1) * w, 0, w, RIBBON_H);
    }
  } else {
    // Symmetric even: mirrored pairs only
    const n = Math.round((2 + Math.floor(prng() * 8)) / 2);
    const w = Math.round(RIBBON_W / (n * 2) + 0.5);
    for (let i = 0; i < n; i++) {
      const [r,g,b] = getColor();
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(i * w, 0, w, RIBBON_H);
      ctx.fillRect(RIBBON_W - (i + 1) * w, 0, w, RIBBON_H);
    }
  }

  // Fabric texture (horizontal grille)
  ctx.fillStyle = 'rgba(0,0,0,0.25)';
  for (let y = Math.round(RIBBON_H / 64); y < RIBBON_H; y += Math.round(RIBBON_H / 16)) {
    ctx.fillRect(0, y, RIBBON_W, Math.round(RIBBON_H / 32));
  }

  // Thin gold frame
  ctx.strokeStyle = 'rgba(200,163,0,0.9)';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(0.75, 0.75, RIBBON_W - 1.5, RIBBON_H - 1.5);
}

function getEarnedCampaigns(playerMissions) {
  const allMissions = [...new Set(rawRows.map(r => r["Mission"] || "").filter(Boolean))];
  const earned = [];
  Object.keys(campaignData).filter(k => !k.startsWith('_')).forEach(name => {
    const camp = campaignData[name];
    if (!camp) return;
    const ms = new Set(camp.missions || []);
    (camp.patterns || []).forEach(pat => allMissions.filter(m => m.includes(pat)).forEach(m => ms.add(m)));
    if ([...ms].some(m => playerMissions.has(m))) earned.push(name);
  });
  return earned.sort();
}

function renderMedalRibbons(containerId, playerMissions) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const section = container.closest('.medals-section');
  const earned = getEarnedCampaigns(playerMissions);
  if (earned.length === 0) {
    if (section) section.style.display = 'none';
    return;
  }
  if (section) section.style.display = '';
  earned.forEach(campaignName => {
    const wrap = document.createElement('div');
    wrap.className = 'medal-wrap';
    const canvas = document.createElement('canvas');
    canvas.width = RIBBON_W;
    canvas.height = RIBBON_H;
    canvas.title = campaignName;
    drawCampaignRibbon(canvas, campaignName);
    const label = document.createElement('div');
    label.className = 'medal-label';
    label.textContent = campaignName;
    wrap.appendChild(canvas);
    wrap.appendChild(label);
    container.appendChild(wrap);
  });
}

function buildCareerStatsHTML(p) {
  // ── Campaign medals placeholder (canvases drawn after innerHTML set) ──
  const medalsHTML = Object.keys(campaignData).filter(k => !k.startsWith('_')).length > 0
    ? `<div class="modal-section medals-section"><h3>Campaign Medals</h3><div class="medals-row" id="medalContainer"></div></div>`
    : '';

  // ── Section 1: Overall stats summary ──
  const overallHTML = `
    <div class="modal-section">
      <h3>Overall Infantry Stats</h3>
      <div class="modal-grid">
        ${mStat(p.killsOnFoot, 'Kills')}
        ${mStat(p.deathsOnFoot, 'Deaths')}
        ${mStat(p.kdFoot.toFixed(2), 'K/D')}
        ${mStat(p.tkOnFoot, 'Teamkills')}
        ${mStat(p.shotsOnFoot, 'Shots Fired')}
        ${mStat(p.spkFoot != null ? p.spkFoot.toFixed(1) : '—', 'Shots / Kill')}
        ${mStat(p.maxLongestFoot ? p.maxLongestFoot + 'm' : '—', 'Longest Kill', p.longestKillWeapon || '')}
        ${mStat(p.avgDistFoot ? Math.round(p.avgDistFoot) + 'm' : '—', 'Avg Kill Dist')}
        ${mStat(p.distanceRun ? p.distanceRun.toFixed(1) + ' km' : '—', 'Dist Run')}
      </div>
    </div>`;

  // ── Section 2: Weapon breakdown ──
  const weapons = Object.entries(p.weaponKills || {}).sort((a,b) => b[1]-a[1]);
  let weaponHTML = '<div class="modal-section"><h3>Weapon Kill Breakdown</h3>';
  if (weapons.length === 0) {
    weaponHTML += '<p style="color:#888;font-size:0.82rem">No weapon data available — re-import missions with the updated script to populate this.</p>';
  } else {
    const maxW = weapons[0][1];
    const totalKillsW = weapons.reduce((s,[,c]) => s+c, 0);
    weapons.slice(0, 15).forEach(([weapon, kills]) => {
      const pct = (kills / maxW * 100).toFixed(1);
      const sharePct = (kills / totalKillsW * 100).toFixed(0);
      weaponHTML += `
        <div class="weapon-bar-row">
          <div class="weapon-label" title="${weapon}">${weapon}</div>
          <div class="weapon-track"><div class="weapon-fill" style="width:${pct}%"></div></div>
          <div class="weapon-val">${kills}</div>
          <div style="font-size:0.72rem;color:#888;width:50px;text-align:right">${sharePct}%</div>
        </div>`;
    });
  }
  weaponHTML += '</div>';

  // ── Section 3: Best single mission ──
  const mRows = p.missionRows || [];
  let bestHTML = '<div class="modal-section"><h3>Best Single Mission</h3>';
  if (mRows.length > 0) {
    const best = mRows.reduce((b, r) => {
      const k = NUM(r["Kills (On Foot)"]);
      return k > NUM(b["Kills (On Foot)"]) ? r : b;
    }, mRows[0]);
    const bk   = NUM(best["Kills (On Foot)"]);
    const bd   = NUM(best["Deaths (On Foot)"]);
    const bkd  = bd > 0 ? (bk/bd).toFixed(2) : bk.toFixed(2);
    const btk  = NUM(best["Teamkills (On Foot)"]);
    const bsh  = NUM(best["Shots (On Foot)"]);
    const bspk = bk > 0 ? (bsh / bk).toFixed(1) : null;
    const blk  = footLongest(NUM(best["Longest Kill On Foot (m)"]));
    const bad  = NUM(best["Avg Kill Dist On Foot (m)"]);
    const bkiv  = NUM(best["Kills (In Vehicle)"]);
    const bdiv  = NUM(best["Deaths (In Vehicle)"]);
    const btkiv = NUM(best["Teamkills (In Vehicle)"]);
    const bsiv  = NUM(best["Shots (In Vehicle)"]);
    const bspkiv = bkiv > 0 ? (bsiv / bkiv).toFixed(1) : null;
    const bvkof = NUM(best["Vehicle Kills (On Foot)"]);
    const bvkiv = NUM(best["Vehicle Kills (In Vehicle)"]);
    const blkiv = NUM(best["Longest Kill In Vehicle (m)"]);
    const badiv = NUM(best["Avg Kill Dist In Vehicle (m)"]);
    const bsui  = NUM(best["Suicides"] || best["Suicides\r"] || "0");
    const bdist = NUM(best["Distance Run (km)"] || best["Distance Run (km)\r"] || "0");
    bestHTML += `
      <div class="best-mission-card">
        <div class="bm-name">${best["Mission"] || best["Source File"] || '—'}</div>
        <div class="bm-stats" style="flex-wrap:wrap;gap:6px 14px">
          <span style="font-weight:700;color:#888;width:100%;font-size:0.75rem;text-transform:uppercase;letter-spacing:.05em">Infantry</span>
          <span>⚔️ ${bk} kills</span>
          <span>💀 ${bd} deaths</span>
          <span>📊 K/D ${bkd}</span>
          ${btk > 0 ? '<span style="color:var(--red)">⚠️ ' + btk + ' TK</span>' : ''}
          <span>🔫 ${bsh} shots</span>
          ${bspk != null ? '<span>🎯 ' + bspk + ' shots/kill</span>' : ''}
          ${blk > 0 ? '<span>📏 ' + blk + 'm longest</span>' : ''}
          ${bad > 0 ? '<span>📐 ' + Math.round(bad) + 'm avg dist</span>' : ''}
          ${bsui > 0 ? '<span style="color:#888">💣 ' + bsui + ' suicides</span>' : ''}
          ${bdist > 0 ? '<span>🏃 ' + bdist.toFixed(1) + ' km run</span>' : ''}
          ${(bkiv > 0 || bdiv > 0 || bvkof > 0 || bvkiv > 0) ? `
            <span style="font-weight:700;color:#888;width:100%;font-size:0.75rem;text-transform:uppercase;letter-spacing:.05em;margin-top:4px">Vehicle</span>
            <span>🚗 ${bkiv} kills</span>
            <span>💀 ${bdiv} deaths</span>
            ${btkiv > 0 ? '<span style="color:var(--red)">⚠️ ' + btkiv + ' TK</span>' : ''}
            <span>🔫 ${bsiv} shots</span>
            ${bspkiv != null ? '<span>🎯 ' + bspkiv + ' shots/kill</span>' : ''}
            ${blkiv > 0 ? '<span>📏 ' + blkiv + 'm longest</span>' : ''}
            ${badiv > 0 ? '<span>📐 ' + Math.round(badiv) + 'm avg dist</span>' : ''}
            ${(bvkof + bvkiv) > 0 ? '<span>💥 ' + (bvkof + bvkiv) + ' vehicles destroyed</span>' : ''}
          ` : ''}
        </div>
      </div>`;
  } else {
    bestHTML += '<p style="color:#888;font-size:0.82rem">No mission data available.</p>';
  }
  bestHTML += '</div>';

  // ── Section 4: Kill breakdown by mission ──
  const missionMap = {};
  mRows.forEach(r => {
    const key = r["Mission"] || r["Source File"] || '—';
    if (!missionMap[key]) missionMap[key] = {
      mission: key,
      k: 0, vk: 0, d: 0, tk: 0, sui: 0, sh: 0, ht: 0, hd: 0,
      lk: 0, _adSum: 0, _adN: 0, dr: 0, tp: 0,
      _kd: null, _spk: null, _ad: null
    };
    const m   = missionMap[key];
    const kof = NUM(r["Kills (On Foot)"]);
    const aof = NUM(r["Avg Kill Dist On Foot (m)"]);
    m.k   += kof;
    m.vk  += NUM(r["Vehicle Kills (On Foot)"]);
    m.d   += NUM(r["Deaths (On Foot)"]);
    m.tk  += NUM(r["Teamkills (On Foot)"]);
    m.sui += NUM(r["Suicides"] || r["Suicides\r"] || "0");
    m.sh  += NUM(r["Shots (On Foot)"]);
    m.ht  += NUM(r["Hits Taken (On Foot)"]);
    m.hd  += NUM(r["Hits Dealt (On Foot)"]);
    m.lk   = Math.max(m.lk, footLongest(NUM(r["Longest Kill On Foot (m)"])));
    if (kof > 0 && aof > 0) { m._adSum += aof * kof; m._adN += kof; }
    m.dr  += NUM(r["Distance Run (km)"] || r["Distance Run (km)\r"] || "0");
    m.tp  += NUM(r["Time Played (s)"] || r["Time Played (s)\r"] || "0");
  });
  // Pre-compute derived sort keys
  Object.values(missionMap).forEach(m => {
    m._kd  = m.d > 0 ? m.k / m.d : m.k;
    m._spk = m.k > 0 ? m.sh / m.k : null;
    m._ad  = m._adN > 0 ? m._adSum / m._adN : null;
    m._acc = m.sh > 0 ? (m.hd / m.sh) * 100 : null;
  });
  // Reset sort to default (kills desc) each time a new player's stats are opened
  missionSortCol = 1;
  missionSortAsc = false;
  _missionData = Object.values(missionMap);

  const missionHTML = `<div class="modal-section"><h3>Kill Breakdown by Mission</h3>
    <div style="overflow-x:auto">
    <table class="mission-table">
      <thead class="js-mission-thead">${_buildMissionThead()}</thead>
      <tbody class="js-mission-tbody">${_buildMissionTbody(_missionData)}</tbody>
    </table></div></div>`;

  return medalsHTML + overallHTML + weaponHTML + bestHTML + missionHTML;
}

function filterToPlayer(playerName) {
  if (selectedPlayers === null) {
    selectedPlayers = new Set([playerName]);
  } else if (selectedPlayers.has(playerName)) {
    selectedPlayers.delete(playerName);
    if (selectedPlayers.size === 0) selectedPlayers = null;
  } else {
    selectedPlayers.add(playerName);
  }
  refreshPills();
  filterChanged();
}

function openPlayerModal(playerName) {
  const p = filteredPlayers.find(x => x.name === playerName);
  if (!p) return;

  currentModalPlayer = playerName;
  document.getElementById('modalPlayerName').innerHTML = p.name + unitBadgeHTML(p.name);
  const _mml = p.missions ? [...p.missions].sort((a, b) => {
    const da = (a.match(/\((\d{4}-\d{2}-\d{2})\)/) || [])[1] || '';
    const db = (b.match(/\((\d{4}-\d{2}-\d{2})\)/) || [])[1] || '';
    return da.localeCompare(db);
  }) : [];
  const _mActive = _mml.length > 1 ? `${missionDate(_mml[0])} – ${missionDate(_mml[_mml.length - 1])}` : (_mml[0] ? missionDate(_mml[0]) : '—');
  document.getElementById('modalPlayerSub').innerHTML =
    `<div class="co-col"><div><b>Combat Missions:</b> ${p.missionCount}</div></div>` +
    `<div class="co-col"><div><b>Active:</b> ${_mActive}</div></div>` +
    (p.timePlayed ? `<div class="co-col"><div><b>Time Played:</b> ${fmtTime(p.timePlayed)}</div></div>` : '') +
    (p.topRole ? `<div class="co-col"><div><b>Top Role:</b> ${p.topRole} (${p.topRoleCount})</div></div>` : '');
  document.getElementById('modalMaximizeBtn').onclick = () => {
    document.getElementById('playerModal').classList.remove('open');
    openCareerPage(currentModalPlayer);
  };

  const body = document.getElementById('modalBody');
  body.innerHTML = buildCareerStatsHTML(p);
  renderMedalRibbons('medalContainer', new Set(((aggPlayers[p.name] || p).missionRows || []).map(r => r["Mission"] || "").filter(Boolean)));
  document.getElementById('playerModal').classList.add('open');
}

function mStat(val, lbl, sub) {
  return `<div class="modal-stat"><div class="ms-val">${val}</div><div class="ms-lbl">${lbl}</div>${sub ? `<div class="ms-sub" title="${sub}">${sub}</div>` : ''}</div>`;
}

// ── CAREER PAGE ──────────────────────────────────────────────────────────
function openCareerPage(playerName) {
  _hideCampaignPage();
  const url = new URL(window.location.href);
  url.searchParams.delete('campaign');
  url.searchParams.set('player', playerName);
  history.pushState({ player: playerName }, '', url);

  selectedPlayers = new Set([playerName]);
  applyFilters();

  const p = filteredPlayers.find(x => x.name === playerName);
  if (!p) return;

  document.getElementById('careerPlayerName').innerHTML = p.name + unitBadgeHTML(p.name);
  _setCareerSubtitle(p);
  document.getElementById('careerStats').innerHTML = buildCareerStatsHTML(p);
  renderMedalRibbons('medalContainer', new Set(((aggPlayers[p.name] || p).missionRows || []).map(r => r["Mission"] || "").filter(Boolean)));

  document.getElementById('statsBar').style.display                   = 'none';
  document.getElementById('awardsRow').style.display                  = 'none';
  document.getElementById('hallFameLabel').style.display              = 'none';
  document.getElementById('shameRow').style.display                   = 'none';
  document.getElementById('hallShameLabel').style.display             = 'none';
  document.querySelector('.chart-section').style.display              = 'none';
  document.querySelector('.filter-panel').style.display               = 'none';
  document.getElementById('sectionNav').style.display                 = 'none';
  document.getElementById('unitLeaderboardSection').style.display     = 'none';
  document.getElementById('extraLeaderboardSections').style.display   = 'none';
  document.getElementById('careerHeader').style.display               = '';
  document.getElementById('careerStats').style.display                = '';

  refreshPills();
  window.scrollTo(0, 0);
}

function closeCareerPage() {
  selectedPlayers = null;

  const url = new URL(window.location.href);
  url.searchParams.delete('player');
  history.pushState({}, '', url);

  document.getElementById('careerHeader').style.display               = 'none';
  document.getElementById('careerStats').style.display                = 'none';
  document.getElementById('statsBar').style.display                   = '';
  document.querySelector('.chart-section').style.display              = '';
  document.querySelector('.filter-panel').style.display               = '';
  document.getElementById('sectionNav').style.display                 = '';
  document.getElementById('unitLeaderboardSection').style.display     = '';
  document.getElementById('extraLeaderboardSections').style.display   = '';

  applyFilters(); // renderLeader handles awardsRow visibility
  refreshPills();
  window.scrollTo(0, 0);
}

// ── CAMPAIGN PAGE ────────────────────────────────────────────────────────
// All missions of a campaign (explicit list + pattern matches), independent of UI filters.
function _campaignMissionNames(campaignName) {
  const camp = campaignData[campaignName];
  if (!camp) return [];
  const present = new Set(rawRows.map(r => r["Mission"] || "").filter(Boolean));
  const out = new Set((camp.missions || []).filter(m => present.has(m)));
  (camp.patterns || []).forEach(pat => present.forEach(m => { if (m.includes(pat)) out.add(m); }));
  return [...out];
}

function _missionSortKey(m) {
  return (m.match(/\((\d{4}-\d{2}-\d{2})\)/) || [])[1] || '';
}

function buildCampaignHTML(campaignName) {
  const missionNames = _campaignMissionNames(campaignName).sort((a, b) => _missionSortKey(a).localeCompare(_missionSortKey(b)) || a.localeCompare(b));
  const missionSet = new Set(missionNames);
  const rows = rawRows.filter(r => missionSet.has(r["Mission"] || "") && !rowIsZeus(r));
  if (!rows.length) return '<p style="color:#888">No data for this campaign yet.</p>';

  const T = { kills: 0, deaths: 0, killsVeh: 0, deathsVeh: 0, vk: 0, tk: 0, shots: 0, hitsDealt: 0, longest: 0, time: 0, dist: 0 };
  const players = {}, missions = {}, weapons = {};
  missionNames.forEach(m => { missions[m] = { name: m, players: new Set(), k: 0, d: 0, vk: 0, tk: 0, lk: 0 }; });

  rows.forEach(r => {
    const name = nameMap[r["Username"] || ""] || r["Username"] || "";
    if (!name) return;
    const mission = r["Mission"] || "";
    const kof = NUM(r["Kills (On Foot)"]), dof = NUM(r["Deaths (On Foot)"]), tk = NUM(r["Teamkills (On Foot)"]) + NUM(r["Teamkills (In Vehicle)"]);
    const kiv = NUM(r["Kills (In Vehicle)"]), div2 = NUM(r["Deaths (In Vehicle)"]);
    const vk = NUM(r["Vehicle Kills (On Foot)"]) + NUM(r["Vehicle Kills (In Vehicle)"]);
    const sh = NUM(r["Shots (On Foot)"]), hd = NUM(r["Hits Dealt (On Foot)"]);
    const lk = footLongest(NUM(r["Longest Kill On Foot (m)"]));

    T.kills += kof; T.deaths += dof; T.killsVeh += kiv; T.deathsVeh += div2; T.vk += vk; T.tk += tk;
    T.shots += sh; T.hitsDealt += hd; T.longest = Math.max(T.longest, lk);
    T.time += NUM(r["Time Played (s)"]); T.dist += NUM(r["Distance Run (km)"]);

    const m = missions[mission];
    m.players.add(name); m.k += kof; m.d += dof; m.vk += vk; m.tk += tk; m.lk = Math.max(m.lk, lk);

    const p = players[name] || (players[name] = { name, missions: new Set(), k: 0, d: 0, vk: 0, tk: 0, sh: 0, lk: 0 });
    p.missions.add(mission); p.k += kof; p.d += dof; p.vk += vk; p.tk += tk; p.sh += sh; p.lk = Math.max(p.lk, lk);

    try {
      Object.entries(JSON.parse(r["Weapon Kills (JSON)"] || "{}")).forEach(([w, c]) => {
        const weapon = w.replace(/\s*\[[^\]]+\]$/, '');
        weapons[weapon] = (weapons[weapon] || 0) + c;
      });
    } catch (e) {}
  });

  const plist = Object.values(players).sort((a, b) => b.k - a.k || a.d - b.d);
  const fmtKD = (k, d) => (d > 0 ? k / d : k).toFixed(2);
  const acc = T.shots > 0 ? (T.hitsDealt / T.shots * 100).toFixed(1) + '%' : '—';
  const esc = n => n.replace(/'/g, "\\'");

  const overall = `<div class="modal-section"><h3>Campaign Overview</h3><div class="modal-grid">
    ${mStat(missionNames.length, 'Missions')}
    ${mStat(plist.length, 'Players')}
    ${mStat(T.kills, 'Infantry Kills')}
    ${mStat(T.deaths, 'Deaths (On Foot)')}
    ${mStat(fmtKD(T.kills, T.deaths), 'K/D')}
    ${mStat(T.killsVeh, 'Kills in Vehicle')}
    ${mStat(T.vk, 'Vehicles Destroyed')}
    ${mStat(T.tk, 'Teamkills')}
    ${mStat(T.shots.toLocaleString(), 'Shots Fired')}
    ${mStat(acc, 'Accuracy')}
    ${mStat(T.longest ? T.longest + ' m' : '—', 'Longest Kill')}
    ${mStat(T.time ? fmtTime(T.time) : '—', 'Time Played')}
    ${mStat(T.dist ? T.dist.toFixed(1) + ' km' : '—', 'Distance Run')}
  </div></div>`;

  const top = (arr, key) => arr.slice().sort((a, b) => b[key] - a[key])[0];
  const topK = top(plist, 'k'), topVK = top(plist, 'vk'), topLK = top(plist, 'lk');
  const best = plist.filter(p => p.d + p.k >= 10).sort((a, b) => (b.d ? b.k / b.d : b.k) - (a.d ? a.k / a.d : a.k))[0];
  const spotlight = `<div class="modal-section"><h3>Highlights</h3><div class="modal-grid">
    ${topK && topK.k ? mStat(topK.name, 'Most Kills', topK.k + ' kills') : ''}
    ${best ? mStat(best.name, 'Best K/D (min 10 K+D)', fmtKD(best.k, best.d)) : ''}
    ${topLK && topLK.lk ? mStat(topLK.name, 'Longest Kill', topLK.lk + ' m') : ''}
    ${topVK && topVK.vk ? mStat(topVK.name, 'Most Vehicles Destroyed', topVK.vk + '') : ''}
  </div></div>`;

  const missionRows = missionNames.map(n => {
    const m = missions[n];
    return `<tr><td>${n.replace(/\s*\(\d{4}-\d{2}-\d{2}\)$/, '')}</td><td>${missionDate(n)}</td><td>${m.players.size}</td><td>${m.k}</td><td>${m.d}</td><td>${fmtKD(m.k, m.d)}</td><td>${m.vk}</td><td>${m.tk}</td><td>${m.lk || '—'}</td></tr>`;
  }).join('');
  const missionHTML = `<div class="modal-section"><h3>Missions</h3><div style="overflow-x:auto"><table class="mission-table" style="min-width:700px">
    <thead><tr><th>Mission</th><th>Date</th><th>Players</th><th>Kills</th><th>Deaths</th><th>K/D</th><th>Veh Kills</th><th>TK</th><th>Longest (m)</th></tr></thead>
    <tbody>${missionRows}</tbody></table></div></div>`;

  const playerRows = plist.map((p, i) => `<tr><td>${i + 1}</td><td><span>${p.name}</span>${unitBadgeHTML(p.name)}<button class="career-icon-btn" onclick="openCareerPage('${esc(p.name)}')">📊</button></td><td>${p.missions.size}</td><td>${p.k}</td><td>${p.d}</td><td>${fmtKD(p.k, p.d)}</td><td>${p.vk}</td><td>${p.tk}</td><td>${p.k > 0 ? (p.sh / p.k).toFixed(1) : '—'}</td><td>${p.lk || '—'}</td></tr>`).join('');
  const playerHTML = `<div class="modal-section"><h3>Players</h3><div style="overflow-x:auto"><table class="mission-table" style="min-width:700px">
    <thead><tr><th>#</th><th>Player</th><th>Missions</th><th>Kills</th><th>Deaths</th><th>K/D</th><th>Veh Kills</th><th>TK</th><th>Shots/Kill</th><th>Longest (m)</th></tr></thead>
    <tbody>${playerRows}</tbody></table></div></div>`;

  const wlist = Object.entries(weapons).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const wmax = wlist.length ? wlist[0][1] : 1;
  const weaponHTML = wlist.length ? `<div class="modal-section"><h3>Top Weapons</h3>${wlist.map(([w, c]) =>
    `<div class="weapon-bar-row"><div class="weapon-label" title="${w}">${w}</div><div class="weapon-track"><div class="weapon-fill" style="width:${(c / wmax * 100).toFixed(1)}%"></div></div><div class="weapon-val">${c}</div></div>`).join('')}</div>` : '';

  return overall + spotlight + missionHTML + playerHTML + weaponHTML;
}

function _showCampaignPage(campaignName) {
  const names = _campaignMissionNames(campaignName);
  const dates = names.map(_missionSortKey).filter(Boolean).sort();
  const range = dates.length > 1 ? `${missionDate(`(${dates[0]})`)} – ${missionDate(`(${dates[dates.length - 1]})`)}` : (dates[0] ? missionDate(`(${dates[0]})`) : '');
  document.getElementById('campaignName').textContent = campaignName;
  document.getElementById('campaignSub').textContent = `${names.length} mission${names.length === 1 ? '' : 's'}` + (range ? `   ·   ${range}` : '');
  document.getElementById('campaignStats').innerHTML = buildCampaignHTML(campaignName);
  document.getElementById('careerHeader').style.display = 'none';
  document.getElementById('careerStats').style.display  = 'none';
  document.body.classList.add('campaign-mode');
  window.scrollTo(0, 0);
}

function _hideCampaignPage() {
  document.body.classList.remove('campaign-mode');
}

function openCampaignPage(campaignName) {
  const url = new URL(window.location.href);
  url.searchParams.delete('player');
  url.searchParams.set('campaign', campaignName);
  history.pushState({ campaign: campaignName }, '', url);
  _showCampaignPage(campaignName);
}

function closeCampaignPage() {
  const url = new URL(window.location.href);
  url.searchParams.delete('campaign');
  history.pushState({}, '', url);
  _hideCampaignPage();
  window.scrollTo(0, 0);
}

// ── INFANTRY TABLE ───────────────────────────────────────────────────────
// Derived from SHARED_INF_STAT_COLS — edit that array to add/remove stats.
// The "Missions" column is injected between the non-afterMissions and
// afterMissions groups to preserve the original column order.
const INF_COLS = [
  { label: "#",       key: "_rank",       numeric: false, sortKey: null },
  { label: "Player",  key: "name",        numeric: false, sortKey: "name" },
  ...SHARED_INF_STAT_COLS.filter(c => !c.afterMissions).map(c => ({
    label: c.label, key: c.aggKey, numeric: c.numeric, sortKey: c.aggKey, fmt: c.fmt, css: c.css, canAvg: c.canAvg,
  })),
  { label: "Missions", key: "missionCount", numeric: true, sortKey: "missionCount" },
  ...SHARED_INF_STAT_COLS.filter(c => c.afterMissions).map(c => ({
    label: c.label, key: c.aggKey, numeric: c.numeric, sortKey: c.aggKey, fmt: c.fmt, css: c.css, canAvg: c.canAvg,
  })),
];

function renderInfantryTable() {
  const sorted = sortPlayers([...filteredPlayers], infSortCol, infSortAsc, INF_COLS, infAvgCols);
  // Rank is always by on-foot kills regardless of sort
  const byKills = [...filteredPlayers].sort((a,b) => b.killsOnFoot - a.killsOnFoot);
  const rankMap = {};
  byKills.forEach((p,i) => rankMap[p.name] = i+1);

  renderTableHead("infantryHead", INF_COLS, infSortCol, infSortAsc, "inf");

  const tbody = document.getElementById("infantryBody");
  tbody.innerHTML = sorted.map(p => {
    const rank = rankMap[p.name];
    const rankStr = rank === 1 ? `<span class="rank-gold">🥇</span>` :
                    rank === 2 ? `<span class="rank-silver">🥈</span>` :
                    rank === 3 ? `<span class="rank-bronze">🥉</span>` : rank;
    const hasTK = p.tkOnFoot > 0;
    const cells = INF_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${rankStr}</td>`;
      if (ci === 1) return `<td><span>${p.name}</span><button class="career-icon-btn" onclick="event.stopPropagation();openCareerPage('${p.name.replace(/'/g, "\\'")}')">📊</button></td>`;
      let raw = p[col.key];
      let val;
      if (col.canAvg && infAvgCols.has(ci) && p.missionCount > 0) {
        val = (raw / p.missionCount).toFixed(2).replace(/\.?0+$/, "");
      } else {
        val = col.fmt ? col.fmt(raw) : (raw == null || raw === "" ? "—" : raw);
      }
      const cls = col.css ? col.css(raw) : "";
      const avgAttr = col.canAvg ? ` oncontextmenu="event.stopPropagation();_toggleAvg('inf',${ci});return false;"` : "";
      return `<td${cls ? ` class="${cls}"` : ""}${avgAttr}>${val}</td>`;
    });
    return `<tr${hasTK ? ' class="tk-row"' : ""} onclick="openPlayerModal('${p.name.replace(/'/g, "\\'")}')"> ${cells.join("")}</tr>`;
  }).join("");
}

// ── VEHICLE TABLE ────────────────────────────────────────────────────────
const VEH_COLS = [
  { label: "#",        key: "_rank",           numeric: false, sortKey: null },
  { label: "Player",   key: "name",            numeric: false, sortKey: "name" },
  { label: "Kills (Veh)", key: "killsInVeh",   numeric: true,  sortKey: "killsInVeh",  canAvg: true },
  { label: "Deaths (Veh)", key: "deathsInVeh", numeric: true,  sortKey: "deathsInVeh", canAvg: true },
  { label: "K/D (Veh)", key: "kdVeh",          numeric: true,  sortKey: "kdVeh",  fmt: v => v.toFixed(2), css: kdClass },
  { label: "TK (Veh)", key: "tkInVeh",         numeric: true,  sortKey: "tkInVeh", css: tkClass, canAvg: true },
  { label: "Veh Kills (Veh)",  key: "vehKillsVeh",   numeric: true, sortKey: "vehKillsVeh", canAvg: true },
  { label: "Shots (Veh)",      key: "shotsInVeh",     numeric: true, sortKey: "shotsInVeh",     canAvg: true },
  { label: "Hits Taken (Veh)", key: "hitsInVeh",     numeric: true, sortKey: "hitsInVeh",     canAvg: true },
  { label: "Hits Dealt (Veh)", key: "hitsDealtInVeh",numeric: true, sortKey: "hitsDealtInVeh",canAvg: true, fmt: v => v || "—" },
  { label: "Accuracy (Veh)",   key: "accuracyVeh",   numeric: true, sortKey: "accuracyVeh",   fmt: v => v != null ? v.toFixed(1) + "%" : "—" },
  { label: "Shots/Kill (Veh)", key: "spkVeh",        numeric: true, sortKey: "spkVeh",        fmt: v => v != null ? v.toFixed(1) : "—" },
  { label: "Avg Dist (m)", key: "avgDistVeh",   numeric: true,  sortKey: "avgDistVeh", fmt: v => v ? Math.round(v) : "—" },
  { label: "Longest (m)", key: "maxLongestVeh", numeric: true,  sortKey: "maxLongestVeh", fmt: v => v || "—" },
  { label: "Missions",  key: "missionCount",   numeric: true,  sortKey: "missionCount" },
];

function renderVehicleTable() {
  // Only show players who have some vehicle activity
  const vehPlayers = filteredPlayers.filter(p =>
    p.killsInVeh > 0 || p.deathsInVeh > 0 || p.vehKillsFoot > 0 || p.vehKillsVeh > 0
  );
  const sorted = sortPlayers([...vehPlayers], vehSortCol, vehSortAsc, VEH_COLS, vehAvgCols);
  const byKills = [...vehPlayers].sort((a,b) => b.killsInVeh - a.killsInVeh);
  const rankMap = {};
  byKills.forEach((p,i) => rankMap[p.name] = i+1);

  renderTableHead("vehicleHead", VEH_COLS, vehSortCol, vehSortAsc, "veh");

  const tbody = document.getElementById("vehicleBody");
  if (!sorted.length) {
    tbody.innerHTML = `<tr><td colspan="${VEH_COLS.length}" style="text-align:center;padding:20px;color:#888">No vehicle combat data for selected filters</td></tr>`;
    return;
  }
  tbody.innerHTML = sorted.map(p => {
    const rank = rankMap[p.name];
    const rankStr = rank === 1 ? `<span class="rank-gold">🥇</span>` :
                    rank === 2 ? `<span class="rank-silver">🥈</span>` :
                    rank === 3 ? `<span class="rank-bronze">🥉</span>` : rank;
    const cells = VEH_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${rankStr}</td>`;
      if (ci === 1) return `<td><span>${p.name}</span><button class="career-icon-btn" onclick="event.stopPropagation();openCareerPage('${p.name.replace(/'/g, "\\'")}')">📊</button></td>`;
      let raw = p[col.key];
      let val;
      if (col.canAvg && vehAvgCols.has(ci) && p.missionCount > 0) {
        val = (raw / p.missionCount).toFixed(2).replace(/\.?0+$/, "");
      } else {
        val = col.fmt ? col.fmt(raw) : (raw == null || raw === "" ? "—" : raw);
      }
      const cls = col.css ? col.css(raw) : "";
      const avgAttr = col.canAvg ? ` oncontextmenu="event.stopPropagation();_toggleAvg('veh',${ci});return false;"` : "";
      return `<td${cls ? ` class="${cls}"` : ""}${avgAttr}>${val}</td>`;
    });
    return `<tr onclick="openPlayerModal('${p.name.replace(/'/g, "\\'")}')"> ${cells.join("")}</tr>`;
  }).join("");
}

// ── HELPERS ──────────────────────────────────────────────────────────────
function renderTableHead(headId, cols, sortCol, sortAsc, tableId) {
  const fnName = tableId === "inf" ? "_sortInf" : "_sortVeh";
  const avgCols = tableId === "inf" ? infAvgCols : vehAvgCols;
  document.getElementById(headId).innerHTML =
    `<tr>${cols.map((c,i) => {
      const arrow = i === sortCol ? (sortAsc ? " ▲" : " ▼") : " ⇅";
      const clickable = c.sortKey !== null;
      const isAvg = avgCols.has(i);
      const label = isAvg ? `<span style="color:#f0b429;font-size:0.7em;font-weight:400">~/m</span> ${c.label}` : c.label;
      return `<th${clickable ? ` onclick="${fnName}(${i})"` : ""}${c.canAvg ? ` oncontextmenu="_toggleAvg('${tableId}',${i});return false;" title="Right-click to toggle per-mission avg"` : ""} style="${clickable ? "" : "cursor:default"}">${label}<span class="sort-arrow">${clickable ? arrow : ""}</span></th>`;
    }).join("")}</tr>`;
}
window._sortInf = function(col) {
  if (INF_COLS[col].sortKey === null) return;
  if (infSortCol === col) infSortAsc = !infSortAsc;
  else { infSortCol = col; infSortAsc = !INF_COLS[col].numeric; }
  renderInfantryTable();
};
window._sortVeh = function(col) {
  if (VEH_COLS[col].sortKey === null) return;
  if (vehSortCol === col) vehSortAsc = !vehSortAsc;
  else { vehSortCol = col; vehSortAsc = !VEH_COLS[col].numeric; }
  renderVehicleTable();
};
window._toggleAvg = function(tableId, col) {
  const avgCols = tableId === "inf" ? infAvgCols : vehAvgCols;
  if (avgCols.has(col)) avgCols.delete(col); else avgCols.add(col);
  if (tableId === "inf") renderInfantryTable(); else renderVehicleTable();
};

function sortPlayers(arr, colIdx, asc, cols, avgCols) {
  const col = cols[colIdx];
  const key = col.sortKey;
  if (!key) return arr;
  const useAvg = avgCols && avgCols.has(colIdx) && col.canAvg;
  return arr.sort((a,b) => {
    let va = a[key], vb = b[key];
    if (useAvg) {
      va = a.missionCount > 0 ? va / a.missionCount : 0;
      vb = b.missionCount > 0 ? vb / b.missionCount : 0;
    }
    if (va == null) va = asc ? Infinity : -Infinity;
    if (vb == null) vb = asc ? Infinity : -Infinity;
    return asc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });
}

function kdClass(v) {
  if (v >= 2)   return "kd-good";
  if (v < 0.8)  return "kd-bad";
  return "";
}

function tkClass(v) {
  return v > 0 ? "tk-cell" : "";
}

// ── UNIT LEADERBOARD ─────────────────────────────────────────────────────
let unitSortCol = 3; // default: Kills (On Foot)
let unitSortAsc = false;
const unitAvgCols = new Set();

const UNIT_COLS = [
  { label: "#",             key: "_rank",         numeric: false, sortKey: null },
  { label: "Unit",          key: "unit",          numeric: false, sortKey: "unit" },
  { label: "Players",       key: "playerCount",   numeric: true,  sortKey: "playerCount" },
  { label: "Kills",         key: "killsOnFoot",   numeric: true,  sortKey: "killsOnFoot",   canAvg: true },
  { label: "Deaths",        key: "deathsOnFoot",  numeric: true,  sortKey: "deathsOnFoot",  canAvg: true },
  { label: "K/D",           key: "kd",            numeric: true,  sortKey: "kd",           fmt: v => v.toFixed(2), css: kdClass },
  { label: "TK",            key: "tkOnFoot",      numeric: true,  sortKey: "tkOnFoot",     css: tkClass },
  { label: "Veh Kills",     key: "killsInVeh",    numeric: true,  sortKey: "killsInVeh",    canAvg: true },
  { label: "Kills/Player",  key: "killsPerPlayer",numeric: true,  sortKey: "killsPerPlayer", fmt: v => v.toFixed(1), canAvg: true },
  { label: "Avg K/D",       key: "avgKd",         numeric: true,  sortKey: "avgKd",        fmt: v => v.toFixed(2), css: kdClass },
  { label: "Missions",      key: "missionCount",  numeric: true,  sortKey: "missionCount" },
  { label: "Dist Run (km)", key: "distanceRun",   numeric: true,  sortKey: "distanceRun",  fmt: v => v.toFixed(1), canAvg: true },
  { label: "Time Played",   key: "timePlayed",    numeric: true,  sortKey: "timePlayed",   fmt: fmtTime, canAvg: true },
];

function buildUnitStats() {
  const units = {};
  filteredPlayers.forEach(p => {
    const unit = playerUnits[p.name] || 'Unknown';
    if (!units[unit]) {
      units[unit] = {
        unit,
        playerCount: 0,
        killsOnFoot: 0, deathsOnFoot: 0, tkOnFoot: 0,
        killsInVeh: 0,
        distanceRun: 0, timePlayed: 0,
        missions: new Set(),
        kdSum: 0,
      };
    }
    const u = units[unit];
    u.playerCount++;
    u.killsOnFoot  += p.killsOnFoot;
    u.deathsOnFoot += p.deathsOnFoot;
    u.tkOnFoot     += p.tkOnFoot;
    u.killsInVeh   += p.killsInVeh;
    u.distanceRun  += p.distanceRun;
    u.timePlayed   += p.timePlayed;
    p.missions.forEach(m => u.missions.add(m));
    u.kdSum        += p.kdFoot;
  });
  return Object.values(units).map(u => {
    u.kd             = u.deathsOnFoot > 0 ? u.killsOnFoot / u.deathsOnFoot : u.killsOnFoot;
    u.killsPerPlayer = u.playerCount  > 0 ? u.killsOnFoot / u.playerCount  : 0;
    u.avgKd          = u.playerCount  > 0 ? u.kdSum        / u.playerCount  : 0;
    u.missionCount   = u.missions.size;
    return u;
  });
}

function renderUnitLeaderboard() {
  const unitStats = buildUnitStats();

  const col = UNIT_COLS[unitSortCol];
  const key = col.sortKey;
  const useAvg = unitAvgCols.has(unitSortCol) && col.canAvg;
  const sorted = [...unitStats];
  if (key) {
    sorted.sort((a, b) => {
      let va = useAvg && a.missionCount > 0 ? a[key] / a.missionCount : a[key];
      let vb = useAvg && b.missionCount > 0 ? b[key] / b.missionCount : b[key];
      if (va == null) va = unitSortAsc ? Infinity : -Infinity;
      if (vb == null) vb = unitSortAsc ? Infinity : -Infinity;
      if (typeof va === 'string') return unitSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      return unitSortAsc ? va - vb : vb - va;
    });
  }

  const byKills = [...unitStats].sort((a, b) => b.killsOnFoot - a.killsOnFoot);
  const rankMap = {};
  byKills.forEach((u, i) => rankMap[u.unit] = i + 1);

  const thead = document.getElementById("unitHead");
  thead.innerHTML = `<tr>${UNIT_COLS.map((c, i) => {
    const arrow = i === unitSortCol ? (unitSortAsc ? " ▲" : " ▼") : " ⇅";
    const clickable = c.sortKey !== null;
    const isAvg = unitAvgCols.has(i);
    const label = isAvg ? `<span style="color:#f0b429;font-size:0.7em;font-weight:400">~/m</span> ${c.label}` : c.label;
    return `<th${clickable ? ` onclick="_sortUnit(${i})"` : ""}${c.canAvg ? ` oncontextmenu="_toggleAvgUnit(${i});return false;" title="Right-click to toggle per-mission avg"` : ""} style="${clickable ? "" : "cursor:default"}">${label}<span class="sort-arrow">${clickable ? arrow : ""}</span></th>`;
  }).join("")}</tr>`;

  const tbody = document.getElementById("unitBody");
  if (!sorted.length) {
    tbody.innerHTML = `<tr><td colspan="${UNIT_COLS.length}" style="text-align:center;padding:20px;color:#888">No data</td></tr>`;
    return;
  }

  tbody.innerHTML = sorted.map(u => {
    const rank = rankMap[u.unit];
    const rankStr = rank === 1 ? `<span class="rank-gold">🥇</span>` :
                    rank === 2 ? `<span class="rank-silver">🥈</span>` :
                    rank === 3 ? `<span class="rank-bronze">🥉</span>` : rank;
    const color = UNIT_COLORS[u.unit] || '#888';
    const cells = UNIT_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${rankStr}</td>`;
      if (ci === 1) return `<td style="text-align:left"><span class="unit-badge" style="background:${color}">${u.unit}</span></td>`;
      let raw = u[col.key];
      let val;
      if (col.canAvg && unitAvgCols.has(ci) && u.missionCount > 0) {
        const avgVal = raw / u.missionCount;
        val = col.fmt ? col.fmt(avgVal) : avgVal.toFixed(2).replace(/\.?0+$/, "");
      } else {
        val = col.fmt ? col.fmt(raw) : (raw == null || raw === "" ? "—" : raw);
      }
      const cls = col.css ? col.css(raw) : "";
      const avgAttr = col.canAvg ? ` oncontextmenu="_toggleAvgUnit(${ci});return false;"` : "";
      return `<td${cls ? ` class="${cls}"` : ""}${avgAttr}>${val}</td>`;
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
}

window._sortUnit = function(col) {
  if (UNIT_COLS[col].sortKey === null) return;
  if (unitSortCol === col) unitSortAsc = !unitSortAsc;
  else { unitSortCol = col; unitSortAsc = !UNIT_COLS[col].numeric; }
  renderUnitLeaderboard();
};
window._toggleAvgUnit = function(col) {
  if (unitAvgCols.has(col)) unitAvgCols.delete(col); else unitAvgCols.add(col);
  renderUnitLeaderboard();
};

// ── SHARED HELPERS ────────────────────────────────────────────────────────
function _rankStr(rank) {
  return rank === 1 ? `<span class="rank-gold">🥇</span>`
       : rank === 2 ? `<span class="rank-silver">🥈</span>`
       : rank === 3 ? `<span class="rank-bronze">🥉</span>`
       : rank;
}

function _mkHead(cols, sortCol, sortAsc, fnName) {
  return `<tr>${cols.map((c, i) => {
    const arrow = i === sortCol ? (sortAsc ? " ▲" : " ▼") : (c.sortKey ? " ⇅" : "");
    const clickable = c.sortKey !== null;
    return `<th${clickable ? ` onclick="${fnName}(${i})"` : ""} style="${clickable ? "" : "cursor:default"}">${c.label}<span class="sort-arrow">${arrow}</span></th>`;
  }).join("")}</tr>`;
}

function _sortArr(data, cols, sortCol, sortAsc) {
  const col = cols[sortCol];
  if (!col || !col.sortKey) return data;
  return [...data].sort((a, b) => {
    let va = a[col.sortKey], vb = b[col.sortKey];
    if (va == null) va = sortAsc ? '\uffff' : '';
    if (vb == null) vb = sortAsc ? '\uffff' : '';
    if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return sortAsc ? va - vb : vb - va;
  });
}

// ── MISSION HISTORY ───────────────────────────────────────────────────────
let mhSortCol = 0;
let mhSortAsc = false;

const MH_COLS = [
  { label: "Date",       key: "date",           numeric: false, sortKey: "dateSort" },
  { label: "Mission",    key: "name",            numeric: false, sortKey: "name" },
  { label: "World",      key: "world",           numeric: false, sortKey: "world" },
  { label: "Players",    key: "players",         numeric: true,  sortKey: "players" },
  { label: "Kills",      key: "kills",           numeric: true,  sortKey: "kills" },
  { label: "Deaths",     key: "deaths",          numeric: true,  sortKey: "deaths" },
  { label: "K/D",        key: "kd",              numeric: true,  sortKey: "kd",    fmt: v => v.toFixed(2), css: kdClass },
  { label: "TK",         key: "tk",              numeric: true,  sortKey: "tk",    css: tkClass },
  { label: "Hits/Kill",  key: "hpk",             numeric: true,  sortKey: "hpk",   fmt: v => v != null ? v.toFixed(1) : "—" },
  { label: "Top Killer", key: "topKiller",       numeric: false, sortKey: "topKillerKills" },
];

function renderMissionHistory() {
  const map = {};
  filteredRows.forEach(r => {
    const src   = r["Source File"] || "";
    const name  = r["Mission"] || src || "—";
    const key   = src || name;
    const world = r["World"] || "—";
    if (!map[key]) {
      const ms = src.match(/(\d{4})_(\d{2})_(\d{2})/);
      map[key] = {
        name, world, dateSort: src,
        date: ms ? `${ms[3]}/${ms[2]}/${ms[1]}` : missionDate(name),
        players: new Set(), kills: 0, deaths: 0, tk: 0, hitsDealt: 0, killerKills: {},
      };
    }
    const m = map[key];
    const pname = r["Username"] || "";
    if (pname) m.players.add(pname);
    const kof = NUM(r["Kills (On Foot)"]);
    m.kills     += kof + NUM(r["Kills (In Vehicle)"]);
    m.deaths    += NUM(r["Deaths (On Foot)"]) + NUM(r["Deaths (In Vehicle)"]);
    m.tk        += NUM(r["Teamkills (On Foot)"]) + NUM(r["Teamkills (In Vehicle)"]);
    m.hitsDealt += NUM(r["Hits Dealt (On Foot)"]) + NUM(r["Hits Dealt (In Vehicle)"]);
    if (pname && kof > 0) m.killerKills[pname] = (m.killerKills[pname] || 0) + kof;
  });

  const data = Object.values(map).map(m => {
    const top = Object.entries(m.killerKills).sort((a, b) => b[1] - a[1])[0];
    return { ...m, players: m.players.size, kd: m.deaths > 0 ? m.kills / m.deaths : m.kills,
      hpk: m.kills > 0 ? m.hitsDealt / m.kills : null,
      topKiller: top ? `${top[0]} (${top[1]})` : "—", topKillerKills: top ? top[1] : 0 };
  });

  const sorted = _sortArr(data, MH_COLS, mhSortCol, mhSortAsc);
  document.getElementById("missionHistHead").innerHTML = _mkHead(MH_COLS, mhSortCol, mhSortAsc, "_sortMH");
  const tbody = document.getElementById("missionHistBody");
  if (!sorted.length) { tbody.innerHTML = `<tr><td colspan="${MH_COLS.length}" style="text-align:center;padding:20px;color:#888">No data</td></tr>`; return; }
  tbody.innerHTML = sorted.map(m => {
    const cells = MH_COLS.map(col => {
      const raw = m[col.key];
      const val = col.fmt ? col.fmt(raw) : (raw == null ? "—" : raw);
      const cls = col.css ? col.css(raw) : "";
      return `<td${cls ? ` class="${cls}"` : ""}>${val}</td>`;
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
}
window._sortMH = function(col) {
  if (mhSortCol === col) mhSortAsc = !mhSortAsc;
  else { mhSortCol = col; mhSortAsc = !MH_COLS[col].numeric; }
  renderMissionHistory();
};

// ── VEHICLE KILLS SECTION ─────────────────────────────────────────────────
let vkSortCol = 0;
let vkSortAsc = false;

const VK_COLS = [
  { label: "Date",    key: "date",    numeric: false, sortKey: "dateSort" },
  { label: "Mission", key: "mission", numeric: false, sortKey: "mission" },
  { label: "Vehicle", key: "vehicle", numeric: false, sortKey: "vehicle" },
  { label: "Killer",  key: "killer",  numeric: false, sortKey: "killer" },
];

function renderVehicleKillsSection() {
  const rows = [];
  filteredRows.forEach(r => {
    const src     = r["Source File"] || "";
    const mission = r["Mission"] || src || "—";
    const pname   = r["Username"] || "";
    const ms      = src.match(/(\d{4})_(\d{2})_(\d{2})/);
    const date    = ms ? `${ms[3]}/${ms[2]}/${ms[1]}` : missionDate(mission);
    const dateSort = src;
    const vkJson  = r["Vehicle Kills (JSON)"] || r["Vehicle Kills (JSON)\r"] || "";
    if (!vkJson) return;
    try {
      const vkList = JSON.parse(vkJson);
      if (!Array.isArray(vkList)) return;
      vkList.forEach(vk => rows.push({
        date, dateSort, mission,
        vehicle: vk.v || "—",
        killer:  pname || "—",
        weapon:  vk.w || "—",
      }));
    } catch(e) {}
  });

  const head = document.getElementById("vehKillsHead");
  const tbody = document.getElementById("vehKillsBody");
  if (!head || !tbody) return;

  const mkArrow = (i) => {
    if (vkSortCol !== i) return '<span class="sort-arrow"></span>';
    return `<span class="sort-arrow">${vkSortAsc ? "▲" : "▼"}</span>`;
  };
  head.innerHTML = `<tr>${VK_COLS.map((c, i) =>
    `<th onclick="window._sortVK(${i})" style="cursor:pointer">${c.label}${mkArrow(i)}</th>`
  ).join("")}</tr>`;

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${VK_COLS.length}" style="text-align:center;padding:20px;color:#888">No vehicle kill data — reimport missions with the updated script to populate this.</td></tr>`;
    return;
  }

  const sorted = _sortArr(rows, VK_COLS, vkSortCol, vkSortAsc);
  tbody.innerHTML = sorted.map(row =>
    `<tr>${VK_COLS.map(col => `<td>${row[col.key] ?? "—"}</td>`).join("")}</tr>`
  ).join("");
}

window._sortVK = function(col) {
  if (vkSortCol === col) vkSortAsc = !vkSortAsc;
  else { vkSortCol = col; vkSortAsc = false; }
  renderVehicleKillsSection();
};

// ── WEAPON LEADERBOARD ────────────────────────────────────────────────────
let wpSortCol = 2;
let wpSortAsc = false;
let wpKillsPerUser = false; // right-click toggle: Total Kills → Kills/User
let wpSearch = "";          // text filter on weapon name
window._wpSearchChange = function(val) { wpSearch = val.trim().toLowerCase(); renderWeaponLeaderboard(); };

const WP_COLS = [
  { label: "#",           key: "_rank",       numeric: false, sortKey: null },
  { label: "Weapon",      key: "weapon",      numeric: false, sortKey: "weapon" },
  { label: "Total Kills", key: "kills",       numeric: true,  sortKey: "kills",  canAvg: true },
  { label: "Users",       key: "users",       numeric: true,  sortKey: "users" },
  { label: "Top User",    key: "topUser",     numeric: false, sortKey: "topUserKills" },
  { label: "Share",       key: "share",       numeric: true,  sortKey: "share",   fmt: v => v.toFixed(1) + "%" },
];

function renderWeaponLeaderboard() {
  const weapons = {};
  let totalKills = 0;
  filteredPlayers.forEach(p => {
    Object.entries(p.weaponKills || {}).forEach(([w, c]) => {
      if (!weapons[w]) weapons[w] = { weapon: w, kills: 0, users: 0, userKills: {} };
      weapons[w].kills += c;
      weapons[w].userKills[p.name] = (weapons[w].userKills[p.name] || 0) + c;
      totalKills += c;
    });
  });
  const data = Object.values(weapons).map(w => {
    w.users = Object.keys(w.userKills).length;
    const top = Object.entries(w.userKills).sort((a, b) => b[1] - a[1])[0];
    w.topUser = top ? `${top[0]} (${top[1]})` : "—";
    w.topUserKills = top ? top[1] : 0;
    w.share = totalKills > 0 ? w.kills / totalKills * 100 : 0;
    w.killsPerUser = w.users > 0 ? w.kills / w.users : 0;
    return w;
  });

  const filtered = wpSearch ? data.filter(w => w.weapon.toLowerCase().includes(wpSearch)) : data;
  const effectiveCols = WP_COLS.map((c, i) =>
    (c.canAvg && wpKillsPerUser) ? { ...c, sortKey: "killsPerUser" } : c
  );
  const sorted = _sortArr(filtered, effectiveCols, wpSortCol, wpSortAsc);
  const byKills = [...data].sort((a, b) => b.kills - a.kills);
  const rankMap = {};
  byKills.forEach((w, i) => rankMap[w.weapon] = i + 1);

  // Build header — amber ~/u prefix on the Kills column when toggled
  document.getElementById("weaponHead").innerHTML = `<tr>${WP_COLS.map((c, i) => {
    const label = (c.canAvg && wpKillsPerUser) ? `<span style="color:#f59e0b">~/u</span> Kills/User` : c.label;
    const arrow = i === wpSortCol ? (wpSortAsc ? " ▲" : " ▼") : (c.sortKey ? " ⇅" : "");
    const clickable = c.sortKey !== null;
    const rcm = c.canAvg ? ` oncontextmenu="_toggleAvgWP(${i});return false;" title="Right-click to toggle kills/user avg"` : "";
    return `<th${clickable ? ` onclick="_sortWP(${i})"` : ""}${rcm} style="${clickable ? "" : "cursor:default"}">${label}<span class="sort-arrow">${arrow}</span></th>`;
  }).join("")}</tr>`;

  const tbody = document.getElementById("weaponBody");
  if (!sorted.length) {
    tbody.innerHTML = `<tr><td colspan="${WP_COLS.length}" style="text-align:center;padding:20px;color:#888">No weapon data — re-import missions with the updated script to populate this.</td></tr>`;
    return;
  }
  tbody.innerHTML = sorted.slice(0, 50).map(w => {
    const cells = WP_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${_rankStr(rankMap[w.weapon])}</td>`;
      const avgAttr = col.canAvg ? ` oncontextmenu="_toggleAvgWP(${ci});return false;"` : "";
      if (col.canAvg && wpKillsPerUser) {
        const val = w.killsPerUser.toFixed(1);
        return `<td${avgAttr} style="color:#f59e0b">${val}</td>`;
      }
      const raw = w[col.key];
      const val = col.fmt ? col.fmt(raw) : (raw == null ? "—" : raw);
      return `<td${avgAttr}>${val}</td>`;
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
}
window._toggleAvgWP = function(col) {
  if (!WP_COLS[col].canAvg) return;
  wpKillsPerUser = !wpKillsPerUser;
  renderWeaponLeaderboard();
};
window._sortWP = function(col) {
  if (WP_COLS[col].sortKey === null) return;
  if (wpSortCol === col) wpSortAsc = !wpSortAsc;
  else { wpSortCol = col; wpSortAsc = !WP_COLS[col].numeric; }
  renderWeaponLeaderboard();
};

// ── MAP STATS ─────────────────────────────────────────────────────────────
let worldSortCol = 2;
let worldSortAsc = false;

const WORLD_COLS = [
  { label: "#",        key: "_rank",    numeric: false, sortKey: null },
  { label: "Map",      key: "world",   numeric: false, sortKey: "world" },
  { label: "Missions", key: "missions",numeric: true,  sortKey: "missions" },
  { label: "Players",  key: "players", numeric: true,  sortKey: "players" },
  { label: "Kills",    key: "kills",   numeric: true,  sortKey: "kills" },
  { label: "Deaths",   key: "deaths",  numeric: true,  sortKey: "deaths" },
  { label: "K/D",      key: "kd",      numeric: true,  sortKey: "kd",  fmt: v => v.toFixed(2), css: kdClass },
  { label: "TK",       key: "tk",      numeric: true,  sortKey: "tk",  css: tkClass },
];

function renderWorldStats() {
  const worlds = {};
  filteredRows.forEach(r => {
    const world = r["World"] || "—";
    const src   = r["Source File"] || r["Mission"] || "";
    if (!worlds[world]) worlds[world] = { world, missions: new Set(), players: new Set(), kills: 0, deaths: 0, tk: 0 };
    const w = worlds[world];
    w.missions.add(src);
    const pname = r["Username"] || "";
    if (pname) w.players.add(pname);
    w.kills  += NUM(r["Kills (On Foot)"]) + NUM(r["Kills (In Vehicle)"]);
    w.deaths += NUM(r["Deaths (On Foot)"]) + NUM(r["Deaths (In Vehicle)"]);
    w.tk     += NUM(r["Teamkills (On Foot)"]) + NUM(r["Teamkills (In Vehicle)"]);
  });
  const data = Object.values(worlds).map(w => ({
    ...w, missions: w.missions.size, players: w.players.size,
    kd: w.deaths > 0 ? w.kills / w.deaths : w.kills,
  }));

  const sorted = _sortArr(data, WORLD_COLS, worldSortCol, worldSortAsc);
  const byMissions = [...data].sort((a, b) => b.missions - a.missions);
  const rankMap = {};
  byMissions.forEach((w, i) => rankMap[w.world] = i + 1);

  document.getElementById("worldHead").innerHTML = _mkHead(WORLD_COLS, worldSortCol, worldSortAsc, "_sortWorld");
  const tbody = document.getElementById("worldBody");
  if (!sorted.length) { tbody.innerHTML = `<tr><td colspan="${WORLD_COLS.length}" style="text-align:center;padding:20px;color:#888">No data</td></tr>`; return; }
  tbody.innerHTML = sorted.map(w => {
    const cells = WORLD_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${_rankStr(rankMap[w.world])}</td>`;
      const raw = w[col.key];
      const val = col.fmt ? col.fmt(raw) : (raw == null ? "—" : raw);
      const cls = col.css ? col.css(raw) : "";
      return `<td${cls ? ` class="${cls}"` : ""}>${val}</td>`;
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
}
window._sortWorld = function(col) {
  if (WORLD_COLS[col].sortKey === null) return;
  if (worldSortCol === col) worldSortAsc = !worldSortAsc;
  else { worldSortCol = col; worldSortAsc = !WORLD_COLS[col].numeric; }
  renderWorldStats();
};

// ── ROLE LEADERBOARD ──────────────────────────────────────────────────────
let roleSortCol = 2;
let roleSortAsc = false;

const ROLE_COLS = [
  { label: "#",       key: "_rank",  numeric: false, sortKey: null },
  { label: "Role",    key: "role",   numeric: false, sortKey: "role" },
  { label: "Kills",   key: "kills",  numeric: true,  sortKey: "kills" },
  { label: "Deaths",  key: "deaths", numeric: true,  sortKey: "deaths" },
  { label: "K/D",     key: "kd",     numeric: true,  sortKey: "kd",    fmt: v => v.toFixed(2), css: kdClass },
  { label: "Avg K/D", key: "avgKd",  numeric: true,  sortKey: "avgKd", fmt: v => v.toFixed(2), css: kdClass },
  { label: "Players", key: "players",numeric: true,  sortKey: "players" },
  { label: "TK",      key: "tk",     numeric: true,  sortKey: "tk",    css: tkClass },
];

function renderRoleLeaderboard() {
  const roles = {};
  filteredPlayers.forEach(p => {
    const role = p.topRole;
    if (!role) return;
    if (!roles[role]) roles[role] = { role, kills: 0, deaths: 0, tk: 0, players: 0, kdSum: 0 };
    const r = roles[role];
    r.kills  += p.killsOnFoot;
    r.deaths += p.deathsOnFoot;
    r.tk     += p.tkOnFoot;
    r.players++;
    r.kdSum  += p.kdFoot;
  });
  const data = Object.values(roles).map(r => ({
    ...r, kd: r.deaths > 0 ? r.kills / r.deaths : r.kills,
    avgKd: r.players > 0 ? r.kdSum / r.players : 0,
  }));

  const sorted = _sortArr(data, ROLE_COLS, roleSortCol, roleSortAsc);
  const byKills = [...data].sort((a, b) => b.kills - a.kills);
  const rankMap = {};
  byKills.forEach((r, i) => rankMap[r.role] = i + 1);

  document.getElementById("roleHead").innerHTML = _mkHead(ROLE_COLS, roleSortCol, roleSortAsc, "_sortRole");
  const tbody = document.getElementById("roleBody");
  if (!sorted.length) { tbody.innerHTML = `<tr><td colspan="${ROLE_COLS.length}" style="text-align:center;padding:20px;color:#888">No data</td></tr>`; return; }
  tbody.innerHTML = sorted.map(r => {
    const cells = ROLE_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${_rankStr(rankMap[r.role])}</td>`;
      const raw = r[col.key];
      const val = col.fmt ? col.fmt(raw) : (raw == null ? "—" : raw);
      const cls = col.css ? col.css(raw) : "";
      return `<td${cls ? ` class="${cls}"` : ""}>${val}</td>`;
    });
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
}
window._sortRole = function(col) {
  if (ROLE_COLS[col].sortKey === null) return;
  if (roleSortCol === col) roleSortAsc = !roleSortAsc;
  else { roleSortCol = col; roleSortAsc = !ROLE_COLS[col].numeric; }
  renderRoleLeaderboard();
};

// ── ATTENDANCE ────────────────────────────────────────────────────────────
let attSortCol = 3;
let attSortAsc = false;

const ATT_COLS = [
  { label: "#",               key: "_rank",       numeric: false, sortKey: null },
  { label: "Player",          key: "name",        numeric: false, sortKey: "name" },
  { label: "Unit",            key: "unit",        numeric: false, sortKey: "unit" },
  { label: "Missions",        key: "missions",    numeric: true,  sortKey: "missions" },
  { label: "Attendance %",    key: "attPct",      numeric: true,  sortKey: "attPct",      fmt: v => v.toFixed(1) + "%" },
  { label: "First Seen",      key: "firstSeen",   numeric: false, sortKey: "firstSeenSort" },
  { label: "Last Seen",       key: "lastSeen",    numeric: false, sortKey: "lastSeenSort" },
  { label: "Avg Time/Mission",key: "avgTime",     numeric: true,  sortKey: "avgTime",     fmt: fmtTime },
];

function renderAttendanceTracker() {
  const totalMissions = new Set(filteredRows.map(r => r["Source File"] || r["Mission"] || "")).size;
  const fmtDate = s => { if (!s) return "—"; const [y, mo, d] = s.split('-'); return `${d}/${mo}/${y}`; };

  const data = filteredPlayers.map(p => {
    const srcDates = (p.missionRows || []).map(r => {
      const src = r["Source File"] || "";
      const ms = src.match(/^(\d{4})_(\d{2})_(\d{2})/);
      return ms ? `${ms[1]}-${ms[2]}-${ms[3]}` : null;
    }).filter(Boolean).sort();
    const unit = playerUnits[p.name] || "Unknown";
    return {
      name: p.name, unit,
      missions: p.missionCount,
      attPct: totalMissions > 0 ? p.missionCount / totalMissions * 100 : 0,
      firstSeen: fmtDate(srcDates[0]),     firstSeenSort: srcDates[0] || "",
      lastSeen:  fmtDate(srcDates[srcDates.length - 1]), lastSeenSort: srcDates[srcDates.length - 1] || "",
      avgTime: p.missionCount > 0 && p.timePlayed > 0 ? p.timePlayed / p.missionCount : 0,
    };
  });

  const sorted = _sortArr(data, ATT_COLS, attSortCol, attSortAsc);
  const byMissions = [...data].sort((a, b) => b.missions - a.missions);
  const rankMap = {};
  byMissions.forEach((p, i) => rankMap[p.name] = i + 1);

  document.getElementById("attendanceHead").innerHTML = _mkHead(ATT_COLS, attSortCol, attSortAsc, "_sortAtt");
  const tbody = document.getElementById("attendanceBody");
  if (!sorted.length) { tbody.innerHTML = `<tr><td colspan="${ATT_COLS.length}" style="text-align:center;padding:20px;color:#888">No data</td></tr>`; return; }
  tbody.innerHTML = sorted.map(p => {
    const color = UNIT_COLORS[p.unit] || '#888';
    const cells = ATT_COLS.map((col, ci) => {
      if (ci === 0) return `<td>${_rankStr(rankMap[p.name])}</td>`;
      if (ci === 1) return `<td style="text-align:left;font-weight:600">${p.name}<button class="career-icon-btn" onclick="event.stopPropagation();openCareerPage('${p.name.replace(/'/g, "\\'")}')">📊</button></td>`;
      if (ci === 2) return `<td><span class="unit-badge" style="background:${color}">${p.unit}</span></td>`;
      const raw = p[col.key];
      const val = col.fmt ? col.fmt(raw) : (raw == null ? "—" : raw);
      return `<td>${val}</td>`;
    });
    return `<tr onclick="openPlayerModal('${p.name.replace(/'/g, "\\'")}')">${cells.join("")}</tr>`;
  }).join("");
}
window._sortAtt = function(col) {
  if (ATT_COLS[col].sortKey === null) return;
  if (attSortCol === col) attSortAsc = !attSortAsc;
  else { attSortCol = col; attSortAsc = !ATT_COLS[col].numeric; }
  renderAttendanceTracker();
};

// ── K/D TREND ─────────────────────────────────────────────────────────────
function refreshKdTrendSubjects() {
  const sel = document.getElementById("kdTrendSubject");
  if (!sel) return;
  const current = sel.value;
  let html = '<option value="__overall__">Overall</option>';
  UNIT_ORDER.forEach(u => {
    if (filteredPlayers.some(p => (playerUnits[p.name] || 'Unknown') === u))
      html += `<option value="${u}">${u}</option>`;
  });
  const names = [...filteredPlayers].sort((a, b) => a.name.localeCompare(b.name)).map(p => p.name);
  if (names.length) html += `<optgroup label="Players">${names.map(n => `<option value="${n}">${n}</option>`).join('')}</optgroup>`;
  sel.innerHTML = html;
  if ([...sel.options].some(o => o.value === current)) sel.value = current;
}

function renderKdTrend() {
  const subject = document.getElementById("kdTrendSubject")?.value || '__overall__';
  const mMap = {};
  filteredRows.forEach(r => {
    const src   = r["Source File"] || r["Mission"] || "";
    const pname = r["Username"] || "";
    if (!src || !pname) return;
    if (!mMap[src]) {
      const ms = src.match(/(\d{4})_(\d{2})_(\d{2})/);
      mMap[src] = { src, dateSort: src, label: ms ? `${ms[3]}/${ms[2]}` : src.slice(0, 10), playerKills: {}, playerDeaths: {} };
    }
    const m = mMap[src];
    m.playerKills[pname]  = (m.playerKills[pname]  || 0) + NUM(r["Kills (On Foot)"]);
    m.playerDeaths[pname] = (m.playerDeaths[pname] || 0) + NUM(r["Deaths (On Foot)"]);
  });

  const missions = Object.values(mMap).sort((a, b) => a.dateSort.localeCompare(b.dateSort));
  const points = missions.map(m => {
    let kills = 0, deaths = 0;
    if (subject === '__overall__') {
      Object.values(m.playerKills).forEach(k  => kills  += k);
      Object.values(m.playerDeaths).forEach(d => deaths += d);
    } else if (UNIT_ORDER.includes(subject)) {
      Object.entries(m.playerKills).forEach(([name, k]) => {
        if ((playerUnits[name] || 'Unknown') === subject) { kills += k; deaths += (m.playerDeaths[name] || 0); }
      });
    } else {
      kills  = m.playerKills[subject]  || 0;
      deaths = m.playerDeaths[subject] || 0;
    }
    return { label: m.label, kd: deaths > 0 ? kills / deaths : kills, kills, deaths };
  }).filter(p => p.kills > 0 || p.deaths > 0);

  const el = document.getElementById("kdTrendChart");
  if (!el) return;
  if (points.length < 2) {
    el.innerHTML = '<p style="color:#888;font-size:0.82rem;padding:8px 0">Not enough mission data to plot a trend.</p>';
    return;
  }

  const W = 900, H = 220;
  const PAD = { top: 16, right: 20, bottom: 44, left: 46 };
  const pw = W - PAD.left - PAD.right;
  const ph = H - PAD.top  - PAD.bottom;
  const maxKd = Math.max(...points.map(d => d.kd), 2);
  const xStep = pw / Math.max(points.length - 1, 1);
  const px = i => PAD.left + i * xStep;
  const py = v => PAD.top  + ph - (v / maxKd) * ph;

  const linePoints  = points.map((d, i) => `${px(i).toFixed(1)},${py(d.kd).toFixed(1)}`).join(' ');
  const areaPoints  = `${px(0).toFixed(1)},${(PAD.top + ph).toFixed(1)} ${linePoints} ${px(points.length - 1).toFixed(1)},${(PAD.top + ph).toFixed(1)}`;
  const lineColor   = UNIT_COLORS[subject] || '#424242';

  const gridSteps = [];
  const _rawStep  = maxKd / 6;
  const _mag      = Math.pow(10, Math.floor(Math.log10(_rawStep || 1)));
  const _norm     = _rawStep / _mag;
  const niceStep  = _norm < 1.5 ? _mag : _norm < 3 ? 2 * _mag : _norm < 7 ? 5 * _mag : 10 * _mag;
  for (let k = 0; k <= maxKd + niceStep * 0.01; k += niceStep) gridSteps.push(parseFloat(k.toFixed(10)));
  const grid = gridSteps.map(k => {
    const y = py(k).toFixed(1);
    const lbl = Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1);
    return `<line x1="${PAD.left}" y1="${y}" x2="${W - PAD.right}" y2="${y}" stroke="#e8e8e8" stroke-width="1"/>
            <text x="${PAD.left - 5}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="10" fill="#999">${lbl}</text>`;
  }).join('');

  const labelEvery = Math.ceil(points.length / 14);
  const xLabels = points.map((d, i) => {
    if (i % labelEvery !== 0 && i !== points.length - 1) return '';
    const x = px(i).toFixed(1), y = (H - PAD.bottom + 13).toFixed(1);
    return `<text x="${x}" y="${y}" text-anchor="end" font-size="9" fill="#999" transform="rotate(-40,${x},${y})">${d.label}</text>`;
  }).join('');

  const refY  = py(1).toFixed(1);
  const refLine = maxKd >= 1
    ? `<line x1="${PAD.left}" y1="${refY}" x2="${W - PAD.right}" y2="${refY}" stroke="#f0b429" stroke-width="1.5" stroke-dasharray="5,4" opacity="0.8"/>
       <text x="${W - PAD.right + 4}" y="${refY}" dominant-baseline="middle" font-size="9" fill="#f0b429">1.0</text>`
    : '';

  const dots = points.map((d, i) =>
    `<circle cx="${px(i).toFixed(1)}" cy="${py(d.kd).toFixed(1)}" r="4" fill="${lineColor}" stroke="white" stroke-width="1.5">
       <title>${d.label}: K/D ${d.kd.toFixed(2)} (${d.kills}K / ${d.deaths}D)</title>
     </circle>`).join('');

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="kd-trend-svg">
    ${grid}
    ${refLine}
    <polygon points="${areaPoints}" fill="${lineColor}" opacity="0.07"/>
    <polyline points="${linePoints}" fill="none" stroke="${lineColor}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}
    ${xLabels}
    <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${PAD.top + ph}" stroke="#ccc"/>
    <line x1="${PAD.left}" y1="${PAD.top + ph}" x2="${W - PAD.right}" y2="${PAD.top + ph}" stroke="#ccc"/>
  </svg>`;
}
window.renderKdTrend = renderKdTrend;

// ── PLAYER COMPARISON ─────────────────────────────────────────────────────
function refreshCompareSelects() {
  const p1 = document.getElementById("compareP1");
  const p2 = document.getElementById("compareP2");
  if (!p1 || !p2) return;
  const prev1 = p1.value, prev2 = p2.value;
  const opts = '<option value="">Select player…</option>' +
    [...filteredPlayers].sort((a, b) => a.name.localeCompare(b.name))
      .map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  p1.innerHTML = opts;
  p2.innerHTML = opts;
  if (prev1 && [...p1.options].some(o => o.value === prev1)) p1.value = prev1;
  if (prev2 && [...p2.options].some(o => o.value === prev2)) p2.value = prev2;
}

function renderComparison() {
  const p1name = document.getElementById("compareP1")?.value;
  const p2name = document.getElementById("compareP2")?.value;
  const el = document.getElementById("compareBody");
  if (!el) return;
  if (!p1name || !p2name || p1name === p2name) {
    el.innerHTML = '<p style="color:#888;font-size:0.82rem;padding:8px 0">Select two different players to compare.</p>';
    return;
  }
  const p1 = filteredPlayers.find(p => p.name === p1name);
  const p2 = filteredPlayers.find(p => p.name === p2name);
  if (!p1 || !p2) return;

  const u1 = playerUnits[p1.name] || 'Unknown', u2 = playerUnits[p2.name] || 'Unknown';
  const c1 = UNIT_COLORS[u1] || '#424242',       c2 = UNIT_COLORS[u2] || '#424242';

  const rows = [
    { label: "Kills (On Foot)",    v1: p1.killsOnFoot,                    v2: p2.killsOnFoot,                    higher: true },
    { label: "Deaths (On Foot)",   v1: p1.deathsOnFoot,                   v2: p2.deathsOnFoot,                   higher: false },
    { label: "K/D (On Foot)",      v1: p1.kdFoot,                         v2: p2.kdFoot,                         higher: true,  fmt: v => v.toFixed(2) },
    { label: "Teamkills",          v1: p1.tkOnFoot + p1.tkInVeh,          v2: p2.tkOnFoot + p2.tkInVeh,          higher: false },
    { label: "Shots Fired",        v1: p1.shotsOnFoot,                    v2: p2.shotsOnFoot,                    higher: false },
    { label: "Shots/Kill",         v1: p1.spkFoot,                        v2: p2.spkFoot,                        higher: false, fmt: v => v != null ? v.toFixed(1) : "—" },
    { label: "Avg Kill Dist (m)",  v1: p1.avgDistFoot,                    v2: p2.avgDistFoot,                    higher: true,  fmt: v => v ? Math.round(v) : "—" },
    { label: "Longest Kill (m)",   v1: p1.maxLongestFoot,                 v2: p2.maxLongestFoot,                 higher: true },
    { label: "Suicides",           v1: p1.suicides,                       v2: p2.suicides,                       higher: false },
    { label: "Distance Run (km)",  v1: p1.distanceRun,                    v2: p2.distanceRun,                    higher: true,  fmt: v => v ? v.toFixed(1) : "—" },
    { label: "Time Played",        v1: p1.timePlayed,                     v2: p2.timePlayed,                     higher: true,  fmt: fmtTime },
    { label: "Kills (In Vehicle)", v1: p1.killsInVeh,                     v2: p2.killsInVeh,                     higher: true },
    { label: "Deaths (In Vehicle)",v1: p1.deathsInVeh,                    v2: p2.deathsInVeh,                    higher: false },
    { label: "K/D (In Vehicle)",   v1: p1.kdVeh,                          v2: p2.kdVeh,                          higher: true,  fmt: v => v.toFixed(2) },
    { label: "Vehicles Destroyed", v1: p1.vehKillsFoot + p1.vehKillsVeh, v2: p2.vehKillsFoot + p2.vehKillsVeh, higher: true },
    { label: "Missions",           v1: p1.missionCount,                   v2: p2.missionCount,                   higher: true },
  ];

  const fv = (v, fmt) => fmt ? fmt(v) : (v == null ? "—" : v);
  el.innerHTML = `<div style="overflow-x:auto"><table class="compare-table">
    <thead><tr>
      <th>Stat</th>
      <th><span class="unit-badge" style="background:${c1}">${u1}</span> ${p1.name}</th>
      <th><span class="unit-badge" style="background:${c2}">${u2}</span> ${p2.name}</th>
    </tr></thead>
    <tbody>${rows.map(r => {
      const v1d = fv(r.v1, r.fmt), v2d = fv(r.v2, r.fmt);
      const differ = r.v1 != null && r.v2 != null && r.v1 !== r.v2;
      const w1 = differ && (r.higher ? r.v1 > r.v2 : r.v1 < r.v2) ? 'compare-win'  : differ ? 'compare-lose' : '';
      const w2 = differ && (r.higher ? r.v2 > r.v1 : r.v2 < r.v1) ? 'compare-win'  : differ ? 'compare-lose' : '';
      return `<tr><td class="compare-label">${r.label}</td><td class="${w1}">${v1d}</td><td class="${w2}">${v2d}</td></tr>`;
    }).join('')}</tbody>
  </table></div>`;
}
window.renderComparison = renderComparison;

// ── SECTION NAV ───────────────────────────────────────────────────────────
const _sectionRenders = {
  'kd-trend':  () => renderKdTrend(),
  'comparison': () => renderComparison(),
};
window.navTo = function(key) {
  const content = document.getElementById('sc-' + key);
  const wasHidden = content && content.style.display === 'none';
  if (wasHidden) toggleSection(key);
  // Re-render sections that use charts (may have rendered while hidden)
  if (wasHidden && _sectionRenders[key]) _sectionRenders[key]();
  // Scroll to the section header
  const header = content ? content.previousElementSibling : null;
  const target = header || content;
  if (target) {
    const top = target.getBoundingClientRect().top + window.pageYOffset - 50;
    window.scrollTo({ top, behavior: 'smooth' });
  }
};

// ── BACK TO TOP ───────────────────────────────────────────────────────────
(function() {
  const btn = document.getElementById('backToTop');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.classList.toggle('visible', window.scrollY > 400);
  }, { passive: true });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
})();

// ── RELEASE DATE + VERSION FOOTER ─────────────────────────────────────────
(function() {
  const el = document.getElementById('releaseDate');
  if (!el) return;
  const [y, m, d] = RELEASE_DATE.split('-');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  el.textContent = `v${VERSION}  ·  ${d} ${months[parseInt(m, 10) - 1]} ${y}`;
})();
