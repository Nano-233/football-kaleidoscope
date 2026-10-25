// Match centre: plays back a built match (scripts/build_site.py) with the newsroom feed, team stats, a scout view and clips.
// No framework: one state object, a canvas for the pitch, and the side panel re-rendered from state when something it shows changes.
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const L = 105, W = 68, cv = $('pitch'), g = cv.getContext('2d'), PAD = 26;
const SX = (cv.width - 2 * PAD) / L, SY = (cv.height - 2 * PAD) / W, px = x => PAD + x * SX, py = y => PAD + y * SY;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COL = { home: css('--home'), away: css('--away'), accent: css('--accent'), good: css('--good'), bad: css('--bad'), pitch: css('--pitch'), line: css('--pitch-line') };

const TABS = { fan: ['Live', 'Ask', 'Recap', 'Players', 'Stats', 'Clips'], analyst: ['Feed', 'Board', 'What if', 'Ask', 'Recap', 'Stats', 'Clips'], scout: ['Players', 'Ask', 'Clips'] };
const VOICE = { fan: 'fan', analyst: 'analyst', scout: 'analyst' };  // which newsroom feed each persona reads
const GROUPS = {
  All: null,
  Passing: ['pass', 'cross', 'ball_receipt'],
  Shooting: ['shot', 'goal'],
  Carrying: ['carry', 'take_on', 'dispossessed', 'miscontrol'],
  Defending: ['tackle', 'interception', 'block', 'clearance', 'pressure', 'duel', 'fifty_fifty', 'ball_recovery', 'dribbled_past'],
  Keeping: ['save', 'keeper_claim'],
  Discipline: ['foul', 'foul_won', 'card', 'offside', 'error'],
};
const GOOD = new Set(['complete', 'won', 'goal', 'held']), BAD = new Set(['intercepted', 'deflected', 'incomplete', 'out', 'offside', 'lost', 'off_target', 'blocked']);
const GOOD_T = new Set(['goal', 'interception', 'ball_recovery', 'block', 'keeper_claim', 'foul_won']), BAD_T = new Set(['miscontrol', 'dispossessed', 'dribbled_past', 'foul', 'error', 'offside']);
const verdict = e => GOOD.has(e.outcome) || (!e.outcome && GOOD_T.has(e.type)) ? 1 : BAD.has(e.outcome) || (!e.outcome && BAD_T.has(e.type)) ? -1 : 0;
const LABEL = { ball_receipt: 'Received', ball_recovery: 'Recovery', fifty_fifty: '50-50', foul_won: 'Foul won', keeper_claim: 'Claim', dribbled_past: 'Dribbled past', take_on: 'Take-on' };
const HARD = .5;  // a hard pass: difficulty (1 - its completion chance when chosen) of 0.5 or more, as in analytics/stats.py
const SPOTTED = { duty: 'without the ball', tiring: 'tiring', spell: 'control vs chaos', flank: 'down one flank' };
const kindLabel = m => (m.type === 'alert' ? `Spotted · ${SPOTTED[m.kind] || m.kind}` : m.type.replace(/_/g, ' '));  // an alert: a shift the newsroom flagged unasked
const label = ty => LABEL[ty] || ty.charAt(0).toUpperCase() + ty.slice(1).replace(/_/g, ' ');
const STAT_ROWS = [  // key, label, format, lower-is-better
  ['goals', 'Goals'], ['xg', 'Expected goals (xG)', v => v.toFixed(2)], ['shots', 'Shots'], ['shots_on_target', 'On target'], ['big_chances', 'Big chances'],
  ['passes', 'Passes'], ['pass_accuracy', 'Pass accuracy', v => Math.round(v * 100) + '%'], ['avg_pass_length_m', 'Average pass length', v => conv('len', v, 1)],
  ['avg_pass_difficulty', 'Average pass difficulty (0-1)', v => v.toFixed(2)], ['hard_pass_accuracy', 'Hard passes completed', v => Math.round(v * 100) + '%'], ['progressive_passes', 'Progressive passes'],
  ['final_third_passes', 'Final-third passes'], ['ppda', 'PPDA (lower = harder press)', v => v.toFixed(1), true], ['possession_gains', 'Possession gains'],
  ['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['take_on_rate', 'Take-on success', v => Math.round(v * 100) + '%'],
  ['top_shot_speed_kmh', 'Fastest shot', v => conv('spd', v)], ['corners', 'Corners'], ['fouls', 'Fouls', null, true], ['yellow_cards', 'Yellow cards', null, true], ['red_cards', 'Red cards', null, true],
];
const FAN_ROWS = ['goals', 'xg', 'shots', 'shots_on_target', 'corners', 'yellow_cards'];

const S = {
  D: null, F: null, n: 0, dt: .2, id: null, t: 0, playing: false, last: 0,
  persona: 'fan', tab: 'Live', player: null, group: 'All', clip: null, custom: [], panelKey: '', info: {}, slots: [],
  bd: null, profile: loadProfile(), asks: [], askVer: 0, askDraft: '', board: { mode: 'Shape', team: 0, phase: 'in_possession' }, wi: { team: 1, change: {}, formation: '', busy: false, res: null, err: '' },
};
const SUGGEST = {
  fan: ["Who's been the best player so far?", 'Why is the game going this way?', 'Explain the last goal', 'How is my player doing?'],
  analyst: ['How did each team set up in and out of possession?', 'Where did the press win the ball back?', 'What changed after the substitutions?', 'Why did they concede?'],
  scout: ['Report on the highest-rated player', 'Who won their duels, and how?', 'Compare the two centre-forwards'],
};
// the metric a viewer follows (scripts/build_site.py FOCUS: top three players by the end of each minute; control is the rhythm reader's)
const METRICS = { '': 'No focus metric', xg: 'Chances (xG)', pressing: 'Pressing and winning it back', running: 'Running', passing: 'Hard passes', duels: 'Duels won', control: 'Control vs chaos' };
const METRIC_UNIT = { xg: v => `xG ${v.toFixed(2)}`, pressing: v => `${v} pressures, tackles and interceptions`, running: v => conv('dist', v, 1), passing: v => `${v} hard passes completed`, duels: v => `${v} duels won` };
function metricCard() {
  const k = S.profile.metric;
  if (!k || !S.D) return '';
  if (k === 'control') return rhythmStrip() || `<div class="label">Your metric · control vs chaos</div><div class="mono">The first five minutes are not over yet.</div>`;
  const rows = (S.D.leaders || {})[k];
  if (!rows) return '';
  const now = rows[Math.min(rows.length - 1, Math.floor(S.t / 60))] || [];
  return `<div class="label">Your metric · ${esc(METRICS[k])} · after ${esc(clockAt(S.t))}</div>${now.length ? `<table class="stats"><tbody>${now.map(([id, v], i) =>
    `<tr><td>${i + 1}. ${num(id)} ${esc(S.info[id].name)} <span class="mono">${esc(S.D.meta[S.info[id].team ? 'away' : 'home'].code)}</span></td><td>${esc(METRIC_UNIT[k](v))}</td>
     <td><button class="link" data-player="${esc(id)}">Profile</button></td></tr>`).join('')}</tbody></table>` : '<div class="mono">Nothing yet.</div>'}`;
}
const LANGS = ['English', 'Español', 'Français', 'Deutsch', 'Português', 'Italiano', '中文', '日本語', 'العربية'];
function loadProfile() {
  const base = { depth: 'standard', language: 'English', club: '', player: '', playerId: '', metric: '' };
  try { return Object.assign(base, JSON.parse(localStorage.getItem('mn-profile'))); } catch { return base; }
}
// published lines in the reader's language: fetched in small batches from the localizer, the original shown until it arrives
const TR = {}, trQueue = new Set();
let trTimer = null;
// units: every number with a unit is shown in the reader's choice; published text is converted before it is translated
const UNITS = { len: { m: [1, 'm'], yd: [1.09361, 'yd'], ft: [3.28084, 'ft'] }, spd: { kmh: [1, 'km/h'], mph: [.621371, 'mph'], ms: [1 / 3.6, 'm/s'] }, dist: { km: [1, 'km'], mi: [.621371, 'mi'] } };
const units = () => { const [len, spd, dist] = (S.profile.units || 'm,kmh,km').split(','); return { len, spd, dist }; };
function conv(kind, v, dp = 0) {
  if (v == null) return '–';
  const [f, u] = UNITS[kind][units()[kind]] || UNITS[kind][Object.keys(UNITS[kind])[0]];
  return `${(v * f).toFixed(dp)} ${u}`;
}
function unitsText(s) {
  if (!s || (S.profile.units || 'm,kmh,km') === 'm,kmh,km') return s;
  return s.replace(/(\d+(?:\.\d+)?)\s?(km\/h|kph|m\/s|km|metres|meters|metre|meter|m)\b/g, (all, n, w) => {
    const dp = (n.split('.')[1] || '').length;
    if (w === 'km/h' || w === 'kph') return conv('spd', +n, dp);
    if (w === 'm/s') return conv('spd', +n * 3.6, dp);
    if (w === 'km') return conv('dist', +n, Math.max(dp, 1));
    return conv('len', +n, dp);
  });
}
function tr(text) {
  const lang = S.profile.language;
  text = unitsText(text);
  if (!text || lang === 'English') return text;
  const hit = (TR[lang] || {})[text];
  if (hit) return hit;
  trQueue.add(text);
  clearTimeout(trTimer); trTimer = setTimeout(flushTr, 150);
  return text;
}
async function flushTr() {
  const lang = S.profile.language, texts = [...trQueue].filter(t => !(TR[lang] || {})[t]).slice(0, 20);
  trQueue.clear();
  if (!texts.length) return;
  try {
    const r = await fetch('api/translate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ language: lang, texts }) });
    if (!r.ok) return;
    const out = (await r.json()).texts;
    TR[lang] = TR[lang] || {};
    texts.forEach((t, i) => { TR[lang][t] = out[i]; });
    S.trVer = (S.trVer || 0) + 1; $('lt-line').dataset.id = ''; setTabs(); render(true);
  } catch { /* no server: the lines stay in English */ }
}
function saveProfile() { try { localStorage.setItem('mn-profile', JSON.stringify(S.profile)); } catch { /* storage off: the profile lasts for this visit */ } }

// ---------------------------------------------------------------- data
async function boot() {
  const q = new URLSearchParams(location.search);
  S.persona = TABS[q.get('p')] ? q.get('p') : 'fan';
  S.tab = TABS[S.persona][0];
  const index = await fetch('data/index.json').then(r => r.json());
  $('match').innerHTML = index.map(m => `<option value="${esc(m.id)}">${esc(m.home)} v ${esc(m.away)}</option>`).join('');  // no final score: it would spoil the replay
  const want = index.some(m => m.id === q.get('m')) ? q.get('m') : index[0].id;
  $('match').value = want;
  setPersona(S.persona);
  await load(want);
  if (+q.get('t') > 0) S.t = Math.min(+q.get('t'), S.D.meta.duration);  // a link to a moment: ?t=<match seconds>&tab=<panel>
  if (TABS[S.persona].includes(q.get('tab'))) { S.tab = q.get('tab'); setTabs(); }
  render(true);
  let seen = false;
  try { seen = !!localStorage.getItem('mn-welcomed'); } catch { /* storage off: show it */ }
  if (!seen && !q.get('t') && !q.get('tab')) $('welcome').hidden = false;  // first visit to the plain link: say what this is, offer three ways in
}

// the welcome card's ways in (shown once per browser); using anything else on the page counts as "explore on my own"
function welcomeDone() { $('welcome').hidden = true; try { localStorage.setItem('mn-welcomed', '1'); } catch { /* storage off */ } }
document.addEventListener('pointerdown', e => { if (!$('welcome').hidden && !e.target.closest('#welcome')) welcomeDone(); }, true);
document.addEventListener('keydown', () => { if (!$('welcome').hidden) welcomeDone(); }, true);
$('welcome').addEventListener('click', e => {
  const b = e.target.closest('[data-welcome]');
  if (!b) return;
  welcomeDone();
  const w = b.dataset.welcome;
  if (w === 'highlights') playHighlights(true);
  if (w === 'analyst') {  // the first shape change, on the feed, where the newsroom explains it from the tracking
    setPersona('analyst');
    const sc = (S.D.newsroom.analyst || []).find(it => it.moment.type === 'shape_change' && it.approved && !it.fallback) || (S.D.newsroom.analyst || [])[0];
    if (sc) seek(sc.moment.t + 1);
    S.tab = 'Feed'; setTabs(); render(true);
  }
  if (w === 'ask') { S.tab = 'Ask'; setTabs(); render(true); setTimeout(() => $('ask-q') && $('ask-q').focus(), 50); }
});

async function load(id) {
  $('loading').hidden = false; S.playing = false; stopLive();
  const base = `data/${encodeURIComponent(id)}/`;
  const [D, fb, hb, zb] = await Promise.all([fetch(base + 'match.json').then(r => r.json()), fetch(base + 'frames.bin').then(r => r.arrayBuffer()), fetch(base + 'heads.bin').then(r => r.arrayBuffer()),
    fetch(base + 'ball_z.bin').then(r => (r.ok ? r.arrayBuffer() : null)).catch(() => null)]);  // ball height (cm), for the 3D view; older builds have none
  const q = new Int16Array(fb), h = new Int8Array(hb), n = D.n_frames, F = new Float32Array(n * 72);
  for (let i = 0; i < n; i++) {  // layout of scripts/build_site.py pack_frames: ball xy, carrier, poss, 22 x (x, y) as int16 at 0.05 m; facing int8 at 1/40 rad
    const o = i * 72, k = i * 48;
    F[o] = i * D.meta.dt; F[o + 1] = q[k] / 20; F[o + 2] = q[k + 1] / 20; F[o + 3] = q[k + 2]; F[o + 4] = q[k + 3];
    for (let j = 0; j < 44; j++) F[o + 5 + j] = q[k + 4 + j] / 20;
    for (let j = 0; j < 22; j++) F[o + 50 + j] = h[i * 22 + j] / 40;
  }
  Object.assign(S, { D, F, n, dt: D.meta.dt, id, t: 0, player: null, clip: null, bd: null, custom: loadCustom(id), panelKey: '', Z: zb ? new Int16Array(zb) : null });
  if (S.v3) teams3d();
  S.info = {};
  for (const side of ['home', 'away']) for (const p of [...D.meta[side].players, ...D.meta[side].bench]) S.info[p.id] = { ...p, team: side === 'home' ? 0 : 1 };
  S.slots = [...D.meta.home.players, ...D.meta.away.players].map(p => p.id);
  S.subs = D.events.filter(e => e.type === 'substitution' || e.type === 'outfield_keeper');
  S.goals = D.events.filter(e => e.type === 'goal' || e.type === 'own_goal');
  S.clockEv = D.events.filter(e => e.clock);
  $('h-code').textContent = D.meta.home.code; $('h-name').textContent = D.meta.home.name;
  $('sb-match').textContent = `${D.meta.home.name} v ${D.meta.away.name}`;
  $('a-code').textContent = D.meta.away.code; $('a-name').textContent = D.meta.away.name;
  $('seek').max = D.meta.duration;
  $('club').innerHTML = '<option value="">Neutral</option>' + ['home', 'away'].map(k => `<option${S.profile.club === D.meta[k].name ? ' selected' : ''}>${esc(D.meta[k].name)}</option>`).join('');
  if (S.profile.playerId && !S.info[S.profile.playerId]) Object.assign(S.profile, { player: '', playerId: '' });
  if (S.profile.club && ![D.meta.home.name, D.meta.away.name].includes(S.profile.club)) S.profile.club = '';  // a club from another match is not playing in this one
  S.asks = [];
  marks();
  syncUrl();
  $('loading').hidden = true;
  render(true);
}

const customKey = id => 'mn-clips-' + id;
function loadCustom(id) { try { return JSON.parse(localStorage.getItem(customKey(id))) || []; } catch { return []; } }
function saveCustom() { try { localStorage.setItem(customKey(S.id), JSON.stringify(S.custom)); } catch { /* storage off: clips last for this visit */ } }
function syncUrl() { history.replaceState(null, '', `?m=${encodeURIComponent(S.id)}&p=${S.persona}`); }

// ---------------------------------------------------------------- time helpers
const frameAt = t => Math.min(S.n - 1, Math.max(0, Math.round(t / S.dt)));
function lastBefore(list, t) { let lo = 0, hi = list.length - 1, r = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (list[m].t <= t) { r = m; lo = m + 1; } else hi = m - 1; } return r; }
function clockAt(t) { const i = lastBefore(S.clockEv, t); return i < 0 ? "1'" : S.clockEv[i].clock; }
function scoreAt(t) { let h = 0, a = 0; for (const e of S.goals) if (e.t <= t) { h = e.home ?? h; a = e.away ?? a; } return [h, a]; }
function periodAt(t) {
  const m = S.D.meta;
  if (t >= m.duration - .5) return 'Full time';
  const ends = S.D.events.find(e => e.type === 'half_end'), starts2 = S.D.events.find(e => e.type === 'half_start' && e.period === 2);
  if (ends && t >= ends.t && (!starts2 || t < starts2.t)) return 'Half-time';
  return t < (ends ? ends.t : m.half_time) ? 'First half' : 'Second half';
}
function slotOf(pid, t) {  // which tracking slot a player occupies at time t (substitutions move ids between slots)
  const cur = [...S.slots];
  for (const e of S.subs) { if (e.t > t) break; cur[e.slot] = e.on; }
  return cur.indexOf(pid);
}
function who(k, t) { let id = S.slots[k]; for (const e of S.subs) { if (e.t > t) break; if (e.slot === k) id = e.on; } return S.info[id]; }
const feed = () => (S.live ? [...S.live.items.values()] : S.D.newsroom[VOICE[S.persona]] || []);
const statsAt = t => S.D.team_stats[Math.min(S.D.team_stats.length - 1, Math.floor(t / 60))];

// ---------------------------------------------------------------- pitch
function drawPitch() {
  g.fillStyle = COL.pitch; g.fillRect(0, 0, cv.width, cv.height);
  g.fillStyle = 'rgba(255,255,255,.025)';
  for (let i = 0; i < 12; i += 2) g.fillRect(px(i * L / 12), py(0), L / 12 * SX, W * SY);
  g.strokeStyle = COL.line; g.lineWidth = 1.5;
  g.strokeRect(px(0), py(0), L * SX, W * SY);
  g.beginPath(); g.moveTo(px(L / 2), py(0)); g.lineTo(px(L / 2), py(W)); g.stroke();
  g.beginPath(); g.arc(px(L / 2), py(W / 2), 9.15 * SX, 0, 7); g.stroke();
  g.fillStyle = COL.line; g.beginPath(); g.arc(px(L / 2), py(W / 2), 2, 0, 7); g.fill();
  for (const s of [0, 1]) {
    g.strokeRect(px(s ? L - 16.5 : 0), py(W / 2 - 20.16), 16.5 * SX, 40.32 * SY);
    g.strokeRect(px(s ? L - 5.5 : 0), py(W / 2 - 9.16), 5.5 * SX, 18.32 * SY);
    g.strokeRect(px(s ? L : -1.6), py(W / 2 - 3.66), 1.6 * SX, 7.32 * SY);
    g.beginPath(); g.arc(px(s ? L - 11 : 11), py(W / 2), 1.6, 0, 7); g.fill();
    g.beginPath(); g.arc(px(s ? L - 11 : 11), py(W / 2), 9.15 * SX, s ? Math.PI - .93 : -.93, s ? Math.PI + .93 : .93); g.stroke();
  }
}

// ---------------------------------------------------------------- 3D view (web/view3d.js, three.js): the same interpolated frame
const KICKS = new Set(['pass', 'cross', 'shot', 'clearance']);
function teams3d() {
  const shade = c => { const n = parseInt(c.slice(1), 16), d = v => Math.round(v * .55).toString(16).padStart(2, '0'); return '#' + d(n >> 16) + d((n >> 8) & 255) + d(n & 255); };
  View3D.setTeams([{ shirt: COL.home, shorts: shade(COL.home), keeper: '#e8c547' }, { shirt: COL.away, shorts: shade(COL.away), keeper: '#7bd389' }]);
}
function draw3d(t, f) {
  const i = Math.max(0, Math.min(S.n - 1, Math.round(t / S.dt)));
  for (let j = 0; j < 22; j++) f[50 + j] = S.F[i * 72 + 50 + j];  // facing from the nearest frame
  const fi = Math.min(S.n - 1.001, Math.max(0, t / S.dt)), i0 = Math.floor(fi), u = fi - i0;
  const zk = k => S.Z[Math.max(0, Math.min(S.n - 1, k))], z0 = zk(i0 - 1), z1 = zk(i0), z2 = zk(i0 + 1), z3 = zk(i0 + 2);  // Catmull-Rom through the 5 Hz heights: a smooth arc, no corner at the top
  const z = S.Z ? Math.max(0, z1 + .5 * u * (z2 - z0 + u * (2 * z0 - 5 * z1 + 4 * z2 - z3 + u * (3 * (z1 - z2) + z3 - z0)))) / 100 : 0;
  const sel = S.persona === 'scout' && S.player ? slotOf(S.player, t) : -1, fol = S.profile.playerId ? slotOf(S.profile.playerId, t) : -1;
  // who is kicking: a pass, cross, shot or clearance whose moment falls inside the kick (the foot meets the ball at the event's time)
  const kicks = new Map(), ev = S.D.events, span = View3D.KICK_S, hit = View3D.KICK_HIT * span;
  for (let j = lastBefore(ev, t + span - hit); j >= 0 && ev[j].t > t - hit; j--) {
    const e = ev[j];
    if (!KICKS.has(e.type) || !e.player || e.body_part === 'head' || e.body_part === 'hands') continue;
    const k = slotOf(e.player, e.t);
    if (k >= 0 && !kicks.has(k)) kicks.set(k, [(t - (e.t - hit)) / span, e.foot || (S.info[e.player] || {}).foot || 'right']);
  }
  View3D.update(f, t, z, k => who(k, t), { highlight: sel >= 0 ? sel : fol, follow: sel >= 0 ? sel : fol >= 0 ? fol : f[3], accent: COL.accent, kicks });
}
function toggle3d() {
  if (!window.View3D) return toast('The 3D view is still loading (it needs the three.js library from the internet).');
  S.v3 = !S.v3;
  if (S.v3 && !$('pitch3d')) { View3D.init(cv.parentElement); View3D.setMode($('cam3d').value); }
  if (S.v3) teams3d();
  $('v3d').setAttribute('aria-pressed', String(S.v3)); $('cam3d-wrap').hidden = !S.v3;
  $('view-mode').textContent = S.v3 ? '3D · animated players' : '2D · tracking at 5 Hz';
  draw();
}

const FS = new Float32Array(72);
function frame(t) {  // 5 Hz tracking, drawn interpolated (Catmull-Rom for players, linear for the ball), as in viewer/index.html
  const fi = Math.min(S.n - 1.001, Math.max(0, t / S.dt)), i = Math.floor(fi), u = fi - i, F = S.F, at = (k, j) => F[Math.max(0, Math.min(S.n - 1, k)) * 72 + j];
  FS[1] = at(i, 1) + (at(i + 1, 1) - at(i, 1)) * u; FS[2] = at(i, 2) + (at(i + 1, 2) - at(i, 2)) * u; FS[3] = u < .5 ? at(i, 3) : at(i + 1, 3);
  for (let j = 5; j < 49; j++) {
    const p0 = at(i - 1, j), p1 = at(i, j), p2 = at(i + 1, j), p3 = at(i + 2, j);
    FS[j] = Math.abs(p2 - p1) > 14 ? p1 : .5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u + (-p0 + 3 * p1 - 3 * p2 + p3) * u * u * u);
  }
  return FS;
}

function draw() {
  if (!S.D) return;
  const t = S.t, f = frame(t);
  HITS.length = 0;
  const three = S.v3 && window.View3D && !boardOn() && !S.bd && !viewOn();  // the analysis overlays (board, breakdowns, shapes) are drawn on the 2D pitch
  cv.style.display = three ? 'none' : '';
  const c3 = $('pitch3d');
  if (c3) c3.style.display = three ? '' : 'none';
  if (three) { draw3d(t, f); hud(t); return; }
  drawPitch();
  if (S.persona === 'scout' && S.player) drawActions(t);
  const sel = S.persona === 'scout' && S.player ? slotOf(S.player, t) : -1, fol = S.profile.playerId ? slotOf(S.profile.playerId, t) : -1;
  for (let k = 0; k < 22; k++) {
    const x = f[5 + 2 * k], y = f[6 + 2 * k];
    if (y < -5) continue;  // sent off
    const X = px(x), Y = py(y), home = k < 11, p = who(k, t), dim = sel >= 0 && k !== sel;
    g.globalAlpha = boardOn() ? .22 : dim ? .45 : 1;
    g.beginPath(); g.arc(X, Y, 9, 0, 7); g.fillStyle = home ? COL.home : COL.away; g.fill();
    if (p && p.role === 'GK') { g.lineWidth = 2; g.strokeStyle = '#0c0d0e'; g.stroke(); }
    g.fillStyle = '#0c0d0e'; g.font = '600 10px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(p ? p.number : '', X, Y + .5);
    if (k === f[3]) { g.beginPath(); g.arc(X, Y, 13, 0, 7); g.strokeStyle = 'rgba(255,255,255,.8)'; g.lineWidth = 1.5; g.stroke(); }
    if (k === sel) { g.beginPath(); g.arc(X, Y, 15, 0, 7); g.strokeStyle = COL.accent; g.lineWidth = 3; g.stroke(); }
    if (k === fol && k !== sel) { g.setLineDash([3, 3]); g.beginPath(); g.arc(X, Y, 15, 0, 7); g.strokeStyle = COL.accent; g.lineWidth = 2; g.stroke(); g.setLineDash([]); }
  }
  g.globalAlpha = 1;
  if (viewOn()) { g.save(); (S.view.kind === 'shape' ? paintShape : paintNetwork)(S.view.team, S.view); g.restore(); }
  else if (boardOn()) drawBoard(t);
  if (!boardOn() && !S.bd) drawTags(t, f);
  if (S.bd && S.bd.hold) { g.save(); g.globalAlpha = Math.min(1, (performance.now() - S.bd.at) / 450); paintStop(S.bd.b.stops[S.bd.i], f); g.restore(); }
  g.beginPath(); g.arc(px(f[1]), py(f[2]), 4.5, 0, 7); g.fillStyle = '#fff'; g.fill(); g.strokeStyle = '#0c0d0e'; g.lineWidth = 1.5; g.stroke();
  hud(t);
}

function drawActions(t) {
  const types = GROUPS[S.group];
  for (const e of S.D.events) {
    if (e.t > t) break;
    if (e.player !== S.player || e.x == null || (types && !types.includes(e.type)) || e.type === 'pressure' && S.group === 'All') continue;
    const v = verdict(e), c = v > 0 ? COL.good : v < 0 ? COL.bad : 'rgba(255,255,255,.7)', recent = t - e.t < 20;
    g.globalAlpha = recent ? 1 : .55;
    if (e.end_x != null && ['pass', 'cross', 'shot', 'carry', 'clearance'].includes(e.type)) {
      g.strokeStyle = c; g.lineWidth = e.type === 'shot' ? 2 : 1.2; g.setLineDash(e.type === 'carry' ? [4, 3] : []);
      g.beginPath(); g.moveTo(px(e.x), py(e.y)); g.lineTo(px(e.end_x), py(e.end_y)); g.stroke(); g.setLineDash([]);
      hitSeg(px(e.x), py(e.y), px(e.end_x), py(e.end_y), evTip(e), () => jumpTo(e.t));
    }
    hitPt(px(e.x), py(e.y), evTip(e), () => jumpTo(e.t));
    g.fillStyle = c; g.beginPath(); g.arc(px(e.x), py(e.y), e.type === 'shot' || e.type === 'goal' ? 5 : 3, 0, 7); g.fill();
  }
  g.globalAlpha = 1;
}

// on-pitch tags: the man on the ball by name; a sprint that is his fastest of the match yet, or a distance milestone (analytics/tools.py
// player_tags); the speed of a shot and the length, difficulty and speed of a hard pass, at the ball, for a few seconds
const TAG_S = 3, surname = id => (S.info[id] ? S.info[id].name.split(' ').slice(-1)[0] : '');
function drawTags(t, f) {
  const at = k => [px(f[5 + 2 * k]), py(f[6 + 2 * k])], shown = new Set();
  for (const r of S.D.milestones || []) {
    if (r.t > t) break;
    const k = t - r.t <= 2 * TAG_S && !shown.has(r.player) ? slotOf(r.player, t) : -1;
    if (k < 0) continue;
    const [X, Y] = at(k);
    tag(X, Y - 14, `${surname(r.player)} · ${tr(r.text)}`, '#e8c547');
    shown.add(r.player);
  }
  for (const r of S.D.tags || []) {
    if (r.t > t) break;
    if (t - r.t > TAG_S || shown.has(r.player)) continue;
    const k = slotOf(r.player, t);
    if (k < 0) continue;
    const [X, Y] = at(k);
    tag(X, Y - 14, `${surname(r.player)} · ${r.kind === 'speed' ? conv('spd', r.value, 1) : conv('dist', r.value) + ' ' + tr('covered')}`, COL.accent);
    shown.add(r.player);
  }
  const c = f[3] >= 0 && f[3] < 22 ? who(f[3], t) : null;
  if (c && !shown.has(c.id)) { const [X, Y] = at(f[3]); tag(X, Y - 14, `${c.number} ${surname(c.id)}`); }
  const ev = S.D.events;
  for (let i = lastBefore(ev, t); i >= 0 && t - ev[i].t <= TAG_S; i--) {
    const e = ev[i], hard = ['pass', 'cross'].includes(e.type) && e.difficulty >= HARD && e.outcome === 'complete';
    if ((e.type !== 'shot' || !e.speed) && !hard) continue;
    tag(px(e.x), py(e.y) + 26, `${tr(e.type === 'shot' ? 'Shot' : 'Hard pass')} · ${quality(e)}`, e.type === 'shot' ? '#fff' : COL.good);
    break;
  }
}

let hudKey = '';
function hud(t) {
  const [h, a] = scoreAt(t), clock = clockAt(t), per = periodAt(t), key = `${h}${a}${clock}${per}`;
  if (key !== hudKey) {
    hudKey = key;
    $('h-score').textContent = h; $('a-score').textContent = a; $('clock').textContent = per === 'Full time' ? 'FT' : clock; $('period').textContent = per;
    $('sb-clock').textContent = `${per === 'Full time' ? 'FT' : clock} · ${per}`;
    for (const tm of [0, 1]) {  // the scorers under each side, as far as the replay has got (an own goal is listed for the side it counted for)
      $(tm ? 'a-goals' : 'h-goals').innerHTML = S.D.events.filter(e => (e.type === 'goal' || e.type === 'own_goal') && e.team === tm && e.t <= t)
        .map(e => `<span>${esc(surname(e.player))} ${esc(e.clock)}${e.type === 'own_goal' ? ' (og)' : ''}</span>`).join('');
    }
  }
  if (document.activeElement !== $('seek')) $('seek').value = t;
  const item = feed().filter(it => it.approved && t >= it.overlay.t_in && t < it.overlay.t_out).pop();
  $('overlay').hidden = !item;
  if (item && $('lt-line').dataset.id !== String(item.moment.id)) {
    $('lt-line').dataset.id = item.moment.id; $('lt-line').textContent = tr(item.line);
    if (S.playing && !S.reel) sayLine(tr(item.line));
    $('lt-kind').textContent = `${kindLabel(item.moment)} · ${item.moment.clock}`;
  }
  $('bd').hidden = !S.bd;
  if (S.bd) {
    const b = S.bd.b, st = b.stops[S.bd.i];
    $('overlay').hidden = true;
    const k = `${b.title} · ${st ? `${S.bd.i + 1}/${b.stops.length} · ${BD_KIND[st.kind]}` : 'the finish'}`, cap = S.bd.hold ? tr(st.caption) : 'Playing the move…';
    if ($('bd-kind').textContent !== k) $('bd-kind').textContent = k;
    if ($('bd-cap').textContent !== cap) $('bd-cap').textContent = cap;
  }
  $('reel').hidden = (!S.reel && !S.view) || !!S.bd;
  if (S.reel) $('reel').textContent = `${S.reel.name} · ${S.reel.who}${S.reel.segs.length > 1 ? ` · ${S.reel.i + 1}/${S.reel.segs.length} · ${S.reel.segs[S.reel.i].clock}` : ''}`;
  else if (S.view) $('reel').innerHTML = `${esc(S.view.title)} <button class="link" id="hide-view">Hide</button>`;
  const c = S.clip, tl = $('tl-clip');
  tl.hidden = !c;
  if (c) { const d = S.D.meta.duration; tl.style.left = (c.start / d * 100) + '%'; tl.style.width = ((c.end - c.start) / d * 100) + '%'; }
}

function marks() {
  const d = S.D.meta.duration;
  $('tl-marks').innerHTML = S.D.events.filter(e => e.type === 'goal' || e.type === 'own_goal' || e.type === 'card')
    .map(e => `<i class="${e.type === 'card' ? (e.card === 'yellow' ? 'card' : 'red') : 'goal'}" style="left:${e.t / d * 100}%" title="${esc(e.clock + ' ' + label(e.type))}"></i>`).join('');
}

// ---------------------------------------------------------------- playback
function tick(now) {
  if (S.bd) bdTick(now);
  else if (S.playing && S.D) {
    S.t = Math.min(S.D.meta.duration, S.t + (now - S.last) / 1000 * +$('speed').value);
    if (S.clip && S.t >= S.clip.end) S.t = S.clip.start;  // a selected clip loops
    if (S.reel && S.t >= S.reel.segs[S.reel.i].end) {  // a reel plays its moments one after another
      if (++S.reel.i < S.reel.segs.length) { S.t = S.reel.segs[S.reel.i].start; if (S.reel.segs[S.reel.i].say) sayLine(tr(S.reel.segs[S.reel.i].say)); }
      else { S.reel = null; setPlaying(false); toast('End of the reel.'); }
    }
    if (S.t >= S.D.meta.duration) setPlaying(false);
  }
  S.last = now;
  draw(); render();
  requestAnimationFrame(tick);
}
function setPlaying(on) { S.playing = on; $('play').setAttribute('aria-label', on ? 'Pause' : 'Play'); $('play-icon').setAttribute('d', on ? 'M4 2.5h3v11H4zM9 2.5h3v11H9z' : 'M4 2.5v11l9-5.5z'); }
function seek(t) { S.t = Math.max(0, Math.min(S.D.meta.duration, t)); draw(); render(); }
const KEY = e => ['goal', 'own_goal', 'penalty', 'var_check', 'goal_disallowed'].includes(e.type) || (e.type === 'shot' && e.xg > .15) || (e.type === 'card' && e.card !== 'yellow');

// ---------------------------------------------------------------- panel
function setPersona(p) {
  S.persona = p; S.tab = TABS[p][0];
  for (const b of $('persona').children) b.setAttribute('aria-selected', b.dataset.p === p);
  $('tabs').innerHTML = TABS[p].map(t => `<button role="tab" data-tab="${t}" aria-selected="${t === S.tab}">${t}</button>`).join('');
  if (S.id) syncUrl();
  render(true);
}

function render(force) {
  if (!S.D) return;
  const minute = Math.floor(S.t / 60), shown = feed().filter(it => it.moment.t <= S.t).length + (S.profile.playerId ? playerNotes().length : 0);
  const key = [S.persona, S.tab, S.tab === 'Clips' || S.tab === 'Ask' ? '' : minute, S.tab === 'Clips' ? S.D.clips.filter(x => x.t <= S.t + .5).length : S.tab === 'Ask' ? S.D.events.filter(e => e.t <= S.t && /goal|substitution/.test(e.type)).length : '', (S.D.recaps || []).filter(r => r.t <= S.t + .5).length, shown, S.player, S.group, S.clip && S.clip.id, S.custom.length, S.askVer, S.liveVer, S.profile.playerId, S.profile.metric, JSON.stringify(S.board), JSON.stringify(S.wi), S.bench && (S.bench.step + S.bench.busy + !!S.bench.r + S.bench.err), S.trVer, S.profile.language, S.profile.club].join('|');
  if (!force && key === S.panelKey) return;
  S.panelKey = key;
  const body = $('panel-body'), keep = body.scrollTop, same = body.dataset.view === S.persona + S.tab + S.player;
  body.innerHTML = VIEWS[S.tab]();
  body.dataset.view = S.persona + S.tab + S.player;
  if (same) body.scrollTop = keep;
}

const VIEWS = {
  Live: () => metricCard() + liveStrip() + feedView('fan'),
  Feed: () => metricCard() + feedView('analyst'),
  Stats: () => S.persona === 'fan' ? compareView() : tableView(),
  Players: () => S.player ? playerView() : squadView(),
  Clips: () => clipsView(),
  Ask: () => askView(),
  Recap: () => recapView(),
  Board: () => boardView(),
  'What if': () => whatIfView(),
};

// the followed player's moments as short notes, straight from the events (no model, nothing to check)
function playerNotes() {
  const id = S.profile.playerId, nm = S.profile.player;
  if (!id) return [];
  const out = [];
  for (const e of S.D.events) {
    if (e.t > S.t) break;
    let line = null;
    if (e.type === 'goal' && e.player === id) line = `GOAL! ${nm} scores.`;
    else if (e.type === 'shot' && e.player === id && e.outcome !== 'goal') line = `${nm} shoots: ${e.outcome.replace('_', ' ')} (xG ${e.xg.toFixed(2)}).`;
    else if ((e.type === 'pass' || e.type === 'cross') && e.player === id && e.assist) line = `${nm} sets up the goal.`;
    else if ((e.type === 'pass' || e.type === 'cross') && e.player === id && e.key_pass) line = `${nm} creates a chance.`;
    else if (e.type === 'take_on' && e.player === id && e.outcome === 'won') line = `${nm} beats his man.`;
    else if (e.type === 'tackle' && e.player === id && e.outcome === 'won') line = `${nm} wins the ball back.`;
    else if (e.type === 'save' && e.player === id) line = `${nm} makes a save.`;
    else if (e.type === 'card' && e.player === id) line = `${nm} is booked (${e.card.replace('_', ' ')}).`;
    else if (e.type === 'substitution' && (e.on === id || e.off === id)) line = e.on === id ? `${nm} comes on.` : `${nm} is substituted.`;
    if (line) out.push({ t: e.t, clock: e.clock, line });
  }
  for (const r of S.D.milestones || []) if (r.player === id && r.t <= S.t) out.push({ t: r.t, clock: r.clock, line: `${nm}: ${r.text}!` });
  return out.sort((a, b) => a.t - b.t);
}

// whether a feed item is about the viewer's club: the event's own team where there is one (the scoring side for a goal), else the side the
// line names first
function isOurs(it, club) {
  const M = S.D.meta, e = it.moment.id != null && S.D.events.find(x => x.id === it.moment.id);
  if (e && (e.team === 0 || e.team === 1)) return M[e.team ? 'away' : 'home'].name === club;
  const line = it.line || '', other = club === M.home.name ? M.away.name : M.home.name, a = line.indexOf(club), b = line.indexOf(other);
  return a >= 0 && (b < 0 || a < b);
}
function feedView(mode) {
  const notes = mode === 'fan' ? playerNotes().map(n => ({ note: true, moment: { t: n.t, clock: n.clock, type: 'your player' }, line: n.line, approved: true })) : [];
  for (const r of S.D.milestones || []) if (r.t <= S.t && r.player !== S.profile.playerId) notes.push({ note: true, milestone: true, moment: { t: r.t, clock: r.clock, type: 'milestone' }, line: `${S.info[r.player].name}: ${r.text}.`, approved: true });
  const items = [...feed().filter(it => it.moment.t <= S.t), ...notes].sort((a, b) => a.moment.t - b.moment.t).reverse();
  if (!items.length) return `<div class="empty">${esc(tr('The newsroom files its first story when something happens.'))}</div>`;
  const club = S.profile.club;  // moments that name the viewer's club are marked as theirs
  return `<ul class="feed ${mode}">` + items.map(it => {
    if (it.note) return `<li class="note"><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(it.milestone ? tr('Milestone') : S.profile.player)}</span>
      <button class="jump" data-seek="${it.moment.t - 6}">Watch</button></div><p>${esc(tr(it.line))}</p></li>`;
    if (it.pending) return `<li class="pending"><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(kindLabel(it.moment))}</span>
      <span class="badge live">${it.pending === 'drafting' ? 'Newsroom drafting · checking' : 'Queued for the newsroom'}</span></div><p>${tr('The desk is on it…')}</p></li>`;
    if (!it.approved && mode === 'fan') return '';
    const badge = !it.approved ? `<span class="badge warn">Held by fact-checker</span>`
      : it.fallback ? `<span class="badge warn" title="Every agent draft was held, so a plain line built from the facts went out">Drafts held · plain line</span>`
      : it.rewrites ? `<span class="badge ok">Fact-checked · rewritten ${it.rewrites}×</span>` : `<span class="badge ok">Fact-checked</span>`;
    const ours = club && isOurs(it, club);
    return `<li class="${it.moment.type === 'alert' ? 'alert' : ''}${ours ? ' mine' : ''}"><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(tr(kindLabel(it.moment)))}</span>${ours ? `<span class="badge mine">${esc(tr('Your club'))}</span>` : ''}${mode === 'analyst' ? badge : ''}${it.latency_s != null ? `<span class="badge live" title="From the moment arriving to the line going out, live on Foundry">Live · ${it.latency_s} s</span>` : ''}
      <button class="jump" data-share="${it.moment.t}|${esc(it.moment.type)}">Share</button>${bdBtn(it.moment.t)}<button class="jump" data-seek="${it.moment.t - 8}">Watch</button></div>
      <p${it.approved ? '' : ' class="rejected"'}>${esc(tr(it.line))}</p>${mode === 'analyst' ? trail(it) : ''}</li>`;
  }).join('') + '</ul>';
}

function trail(it) {
  const tr = it.trail || {};
  const attempts = (tr.attempts || []).map((a, i) => `<h4>Commentator · draft ${i + 1} ${a.approved ? '· approved' : '· rejected'}</h4>
    <div class="${a.approved ? '' : 'rejected'}">${esc(a.line)}</div>${a.issues.length ? `<pre>${esc(a.issues.join('\n'))}</pre>` : ''}`).join('');
  let picks;
  try { picks = JSON.parse(String(tr.stats).replace(/^```(json)?|```$/g, '')); } catch { picks = null; }
  const stats = Array.isArray(picks) ? picks.map(p => `<div><b>${esc(p.metric)}</b> ${esc(p.value)}${p.meaning ? ` · ${esc(p.meaning)}` : ''}</div>`).join('') : `<pre>${esc(tr.stats)}</pre>`;
  return `<details class="trail"><summary>How the newsroom wrote this</summary><div class="trail-body">
    <h4>Stats analyst</h4>${stats}<h4>Tactics analyst</h4><div>${esc(tr.tactics)}</div>${attempts}</div></details>`;
}

function fmtStat(row, v) { return v == null ? '–' : row[2] ? row[2](v) : v; }
function winNow() {
  const W_ = S.D.board && S.D.board.winprob;
  if (!W_) return null;
  let r = W_.series[0];
  for (const x of W_.series) { if (x.t > S.t) break; r = x; }
  return r;
}
function winBar() {
  const w = winNow(), M = S.D.meta;
  if (!w) return '';
  const over = S.t >= M.duration - 1, p = x => over ? Math.round(x * 100) : x < .005 ? '<1' : x > .995 ? '>99' : Math.round(x * 100);  // nothing is certain before the final whistle
  const club = S.profile.club, side = club === M.home.name ? 'home' : club === M.away.name ? 'away' : '';
  const mine = side ? (() => {  // the fan's own side: score from their end and the chance to win
    const g = S.D.events.filter(e => e.t <= S.t && (e.type === 'goal' || e.type === 'own_goal')).pop(), sc = g ? [g.home, g.away] : [0, 0];
    const us = side === 'home' ? 0 : 1;
    return `<div class="mono" style="margin-top:4px"><span class="badge mine">${esc(tr('Your club'))}</span> ${esc(club)} ${sc[us]}–${sc[1 - us]} · ${p(w[side])}% ${esc(tr('to win'))}</div>`;
  })() : '';
  return `<div class="label">${esc(tr('Win probability'))} · ${esc(clockAt(S.t))}</div>
    <div class="winbar"><i style="flex:${w.home};background:var(--home)"></i><i style="flex:${w.draw};background:var(--surface-3)"></i><i style="flex:${w.away};background:var(--away)"></i></div>
    <div class="cmp-head" style="display:grid;grid-template-columns:1fr 1fr 1fr;font-size:12px;margin-top:4px"><span>${esc(M.home.code)} ${p(w.home)}%</span><span style="text-align:center">${esc(tr('Draw'))} ${p(w.draw)}%</span><span style="text-align:right">${esc(M.away.code)} ${p(w.away)}%</span></div>${mine}`;
}
function compareView() {
  const [h, a] = statsAt(S.t), M = S.D.meta;
  const rows = STAT_ROWS.filter(r => FAN_ROWS.includes(r[0])).map(r => {
    const hv = h[r[0]] ?? 0, av = a[r[0]] ?? 0, tot = hv + av || 1, lead = r[3] ? (hv < av ? 'h' : av < hv ? 'a' : '') : (hv > av ? 'h' : av > hv ? 'a' : '');
    return `<div class="cmp-row"><div class="cmp-head"><span>${fmtStat(r, hv)}</span><span>${r[1]}</span><span>${fmtStat(r, av)}</span></div>
      <div class="cmp-bar"><i class="${lead === 'h' ? 'lead h' : ''}" style="flex:${hv / tot || .001}"></i><i class="${lead === 'a' ? 'lead a' : ''}" style="flex:${av / tot || .001}"></i></div></div>`;
  }).join('');
  return `${winBar()}<div class="label">${esc(M.home.code)} vs ${esc(M.away.code)} · after ${esc(clockAt(S.t))}</div><div class="compare">${rows}</div>`;
}

function tableView() {
  const [h, a] = statsAt(S.t), M = S.D.meta;
  const rows = STAT_ROWS.map(r => {
    const hv = h[r[0]], av = a[r[0]], better = (x, y) => x != null && y != null && (r[3] ? x < y : x > y);
    return `<tr><td>${r[1]}</td><td class="${better(hv, av) ? 'lead' : ''}">${fmtStat(r, hv)}</td><td class="${better(av, hv) ? 'lead' : ''}">${fmtStat(r, av)}</td></tr>`;
  }).join('');
  return `<div class="label">Team statistics · after ${esc(clockAt(S.t))}</div>
    <table class="stats"><thead><tr><th>Metric</th><th>${esc(M.home.code)}</th><th>${esc(M.away.code)}</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="label">Source</div><div class="mono">Computed from the event stream by the analytics layer; the newsroom may only quote these numbers.</div>${truthPanel()}`;
}

// what the reader saw against what the engine actually did: the data is synthetic, so the truth is known
let EVAL = null;
fetch('data/eval.json').then(r => (r.ok ? r.json() : null)).then(j => { EVAL = j; }).catch(() => {});
function evalBlock() {
  if (!EVAL) return '';
  const e = EVAL.summary, name = { formation: 'Formations and changes', goals: 'Scorers and minutes', best: 'Best player by rating', pressing: 'Which side pressed harder',
    bench: 'Bench assistant names the tested pick', control: 'Which side controlled the game', duties: 'Who did which defensive job', fatigue: 'Who is tiring',
    milestones: 'Milestones (braces, hat-tricks)', passing: 'Most hard passes completed' };
  return `<div class="label">Newsroom answers scored against the truth</div>
    <table class="stats"><tbody>${Object.entries(e.accuracy).map(([k, v]) => `<tr><td>${name[k] || k}</td><td>${Math.round(v * 100)}%</td></tr>`).join('')}
      <tr><td><b>All ${e.answers} answers, ${e.matches} matches</b></td><td class="lead">${Math.round(e.overall * 100)}%</td></tr>
      ${e.changes && e.changes.true ? `<tr><td>System changes found (formation and minute)</td><td>${e.changes.found} of ${e.changes.true}</td></tr>
      <tr><td>…with the manager's real reason</td><td>${e.changes.reasons_right} of ${e.changes.found}</td></tr>
      <tr><td>…minute off by, on average</td><td>${e.changes.mean_minute_error}</td></tr>` : ''}
      <tr><td>Approved by the fact-checker</td><td>${Math.round(e.approved * 100)}%</td></tr>
      <tr><td>Tool calls · seconds per answer</td><td>${e.avg_tool_calls} · ${e.avg_seconds}</td></tr></tbody></table>
    <div class="mono" style="margin-top:6px">The engine records what really happened, so every answer can be marked against it.</div>`;
}
function truthPanel() {
  return truthMatch() + evalBlock();
}
function truthMatch() {
  if (!S.D.reading || S.t < S.D.meta.duration - 1) return '<div class="label">Tactics read against the truth</div><div class="mono">Shown at full time: the engine\'s own record of every system change, against what the newsroom read from the tracking data.</div>';
  return '<div class="label">Tactics read against the truth</div>' + S.D.reading.map(r => {
    const sc = r.score;
    const tch = r.truth.changes.map(c => `${esc(c.at)} ${esc(c.previous)} → ${esc(c.formation)} <span class="mono">(${esc(c.reason)})</span>`).join('<br>') || 'none';
    const rch = r.read.changes.map(c => `${esc(c.at)} ${esc(c.formation)} <span class="mono">${c.phase === 'in_possession' ? 'with the ball' : 'without the ball'}</span>`).join('<br>') || 'none';
    return `<div class="truth"><b>${esc(r.team)}</b>
      <table class="stats"><tbody>
        <tr><td>Set up in (engine)</td><td>${esc(r.truth.starting)}</td></tr>
        <tr><td>Read, with / without the ball</td><td class="${sc.starting_right ? 'lead' : ''}">${esc(r.read.starting.in_possession)} / ${esc(r.read.starting.out_of_possession)}</td></tr>
        <tr><td>System changes (engine)</td><td>${tch}</td></tr>
        <tr><td>Changes read</td><td>${rch}</td></tr>
        <tr><td>Found · false alarms</td><td>${sc.found}/${sc.true_changes} · ${sc.false_alarms}</td></tr>
        <tr><td>Minutes agreeing with the set system</td><td>${Math.round(sc.windows_agreeing * 100)}%</td></tr>
      </tbody></table></div>`;
  }).join('');
}

function squadView() {
  const P = S.D.players, M = S.D.meta;
  const list = side => {
    const starters = M[side].players.map(p => p.id), onAt = Object.fromEntries(S.D.events.filter(e => e.type === 'substitution').map(e => [e.on, e.t]));
    const used = M[side].bench.filter(p => onAt[p.id] !== undefined && onAt[p.id] <= S.t).map(p => p.id);  // a substitute appears once he is on
    return [...starters, ...used].map(id => {
      const p = S.info[id], s = P[id] || {};
      return `<li><button data-player="${esc(id)}"><span class="num">${p.number}</span><span>${esc(p.name)}${p.star ? ' <span class="badge">Star</span>' : ''}${starters.includes(id) ? '' : ' <span class="sub">(sub)</span>'}</span>
        <span class="role">${esc(p.label || p.role)}</span><span class="rt">${s.rating ?? '–'}</span></button></li>`;
    }).join('');
  };
  return `<div class="label">${esc(M.home.name)} · ${esc(M.home.formation)}</div><ul class="squad">${list('home')}</ul>
    <div class="label">${esc(M.away.name)} · ${esc(M.away.formation)}</div><ul class="squad">${list('away')}</ul>
    <div class="mono">Ratings (1–10) are for each player's whole match. Select a player for every action.</div>`;
}

const ROLE_KV = {
  GK: [['saves', 'Saves'], ['keeper_claims', 'Claims'], ['passes_completed', 'Passes completed'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%'], ['long_passes', 'Long passes'], ['errors', 'Errors']],
  CB: [['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['clearances', 'Clearances'], ['aerial_won', 'Aerials won'], ['progressive_passes', 'Prog. passes'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  FB: [['progressive_passes', 'Prog. passes'], ['crosses', 'Crosses'], ['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['carry_metres', 'Carry metres', v => Math.round(v)], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  MID: [['passes_completed', 'Passes completed'], ['progressive_passes', 'Prog. passes'], ['key_passes', 'Key passes'], ['tackles_won', 'Tackles won'], ['pressures', 'Pressures'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  ATT: [['goals', 'Goals'], ['xg', 'xG', v => v.toFixed(2)], ['shots', 'Shots'], ['key_passes', 'Key passes'], ['take_ons_won', 'Take-ons won'], ['progressive_carries', 'Prog. carries']],
};
const kvFor = role => ROLE_KV[role] || ROLE_KV[{ DM: 'MID', CM: 'MID', AM: 'ATT', WG: 'ATT', ST: 'ATT' }[role] || 'MID'];

const REELS = {
  Highlights: e => ['goal', 'save'].includes(e.type) || (e.type === 'shot') || ((e.type === 'pass' || e.type === 'cross') && (e.key_pass || e.assist)) || (e.type === 'take_on' && e.outcome === 'won') || (e.type === 'tackle' && e.outcome === 'won'),
  Shots: e => e.type === 'shot',
  'Chances created': e => (e.type === 'pass' || e.type === 'cross') && (e.key_pass || e.assist),
  'Progressive passes': e => e.type === 'pass' && e.progressive && e.outcome === 'complete',
  'Hard passes': e => (e.type === 'pass' || e.type === 'cross') && e.difficulty >= HARD && e.outcome === 'complete',
  'Duels won': e => (e.type === 'tackle' || e.type === 'take_on' || e.type === 'duel' || e.type === 'fifty_fifty') && e.outcome === 'won',
  'Duels lost': e => ((e.type === 'tackle' || e.type === 'take_on' || e.type === 'duel') && e.outcome === 'lost') || ['dribbled_past', 'dispossessed', 'miscontrol'].includes(e.type),
  Defending: e => ['tackle', 'interception', 'block', 'clearance', 'ball_recovery'].includes(e.type),
};
function reelFor(pid, name) {
  const segs = [];
  for (const e of S.D.events) {
    if (e.player !== pid || !REELS[name](e)) continue;
    const a = Math.max(0, e.t - 5), b = e.t + 3, last = segs[segs.length - 1];
    if (last && a <= last.end) last.end = b; else segs.push({ start: a, end: b, clock: e.clock });
  }
  return segs;
}
const HL_KINDS = ['goal', 'own_goal', 'big_chance', 'penalty', 'red_card', 'var_overturned'];
function playHighlights(all) {
  const items = feed().filter(it => it.approved && HL_KINDS.includes(it.moment.type) && (all || it.moment.t <= S.t));
  if (!items.length) return toast('No highlights yet.');
  S.clip = null; S.view = null;
  S.reel = { name: 'Highlights', who: S.D.meta.home.code + ' v ' + S.D.meta.away.code, i: 0,
    segs: items.map(it => ({ start: Math.max(0, it.moment.t - 10), end: it.moment.t + 4, clock: it.moment.clock, say: it.line })) };
  seek(S.reel.segs[0].start); setPlaying(true); sayLine(tr(S.reel.segs[0].say));
}
function playReel(pid, name) {
  const segs = reelFor(pid, name);
  if (!segs.length) return toast(`No ${name.toLowerCase()} for ${S.info[pid].name}.`);
  S.clip = null;
  S.reel = { name, who: S.info[pid].name, segs, i: 0 };
  seek(segs[0].start); setPlaying(true);
  toast(`${name}: ${S.info[pid].name}, ${segs.length} moment${segs.length > 1 ? 's' : ''}.`);
}
function heatSvg(grid, team) {  // 6 x 4 zones, own goal on the left, attacking left to right
  const w = 300, h = 194, cw = w / 6, ch = h / 4, mx = Math.max(...grid.flat(), .001);
  const cells = grid.map((col, i) => col.map((v, j) => `<rect x="${i * cw}" y="${j * ch}" width="${cw - 1}" height="${ch - 1}" fill="${team ? 'var(--away)' : 'var(--home)'}" fill-opacity="${(.08 + .9 * v / mx).toFixed(2)}"/>`).join('')).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Where he spent his time; his own goal on the left">${cells}
    <rect x="0" y="0" width="${w}" height="${h}" fill="none" stroke="var(--line-strong)"/><line x1="${w / 2}" x2="${w / 2}" y1="0" y2="${h}" stroke="var(--line-strong)"/></svg>`;
}
const TERM_LABEL = { attack: 'Attack', passing: 'Passing', carrying: 'Carrying', defending: 'Defending', keeping: 'Keeping', conceded: 'Goals conceded', result: 'Result', discipline: 'Discipline' };
const PER90_LABEL = { passes: 'Passes', passes_completed: 'Passes completed', progressive_passes: 'Progressive passes', key_passes: 'Key passes', shots: 'Shots', xg: 'xG', take_ons: 'Take-ons',
  take_ons_won: 'Take-ons won', tackles: 'Tackles', tackles_won: 'Tackles won', interceptions: 'Interceptions', aerial_won: 'Aerials won', aerial_lost: 'Aerials lost',
  dribbled_past: 'Dribbled past', dispossessed: 'Dispossessed', pressures: 'Pressures', recoveries: 'Recoveries', fouls: 'Fouls', saves: 'Saves' };

function profileBlocks(pid, p) {
  const pr = (S.D.profiles || {})[pid];
  if (!pr) return '';
  const ph = pr.physical || {}, team = p.team;
  const terms = Object.entries(pr.rating_terms || {}).filter(([, v]) => Math.abs(v) >= .05).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const mt = Math.max(.1, ...terms.map(([, v]) => Math.abs(v)));
  const vs = Object.entries(pr.per90_vs_role || {}).filter(([, v]) => v.role_avg > .3).map(([k, v]) => [k, v.him, v.role_avg, (v.him - v.role_avg) / v.role_avg])
    .sort((a, b) => Math.abs(b[3]) - Math.abs(a[3])).slice(0, 6);
  const reels = S.persona === 'scout' ? Object.keys(REELS).filter(r => r !== 'Highlights') : ['Shots', 'Chances created', 'Duels won'];
  return `<div class="label">Physical</div><div class="kv"><div><b>${conv('dist', ph.distance_km, 1)}</b><span>Distance</span></div><div><b>${ph.sprints ?? '–'}</b><span>Sprints</span></div><div><b>${conv('spd', ph.top_speed_kmh, 1)}</b><span>Top speed</span></div></div>
    ${pr.heat_map_6x4 ? `<div class="label">Where he played</div>${heatSvg(pr.heat_map_6x4, team)}` : ''}
    ${terms.length ? `<div class="label">What made his rating</div><table class="stats"><tbody>${terms.map(([k, v]) => `<tr><td>${TERM_LABEL[k] || k}</td>
      <td style="width:45%"><span class="termbar ${v < 0 ? 'neg' : ''}" style="width:${Math.round(Math.abs(v) / mt * 100)}%"></span></td><td class="${v >= 0 ? 'lead' : ''}">${v > 0 ? '+' : ''}${v.toFixed(2)}</td></tr>`).join('')}</tbody></table>` : ''}
    ${vs.length ? `<div class="label">Per 90 against his position</div><table class="stats"><thead><tr><th>Metric</th><th>Him</th><th>Avg</th></tr></thead><tbody>${vs.map(([k, him, avg, d]) => `<tr><td>${PER90_LABEL[k] || k}</td>
      <td class="${(d > 0) !== ['aerial_lost', 'dribbled_past', 'dispossessed', 'fouls'].includes(k) ? 'lead' : ''}">${him}</td><td>${avg}</td></tr>`).join('')}</tbody></table>` : ''}
    <div class="label">Reels</div><div class="chips">${reels.map(r => `<button class="chip" data-reel="${esc(r)}">${esc(r)}</button>`).join('')}</div>`;
}

function fatigueBlock(pid, tm) {  // high-intensity running by 15 minutes (analytics/tools.py fatigue), completed windows only
  const F_ = (S.D.fatigue || [])[tm], q = F_ && Object.values(F_.players).find(x => x.id === pid);
  if (!q) return '';
  const ws = Object.entries(q.hi_m_per_min_by_window).filter(([w]) => (F_.window_ends || {})[w] <= S.t);  // completed windows only
  if (ws.length < 2) return '';
  const mx = Math.max(...ws.map(([, v]) => v), 1);
  return `<div class="label">Running hard · metres a minute above ${conv('spd', 19.8, 1)}</div>
    <div style="display:flex;gap:4px;align-items:flex-end;height:46px">${ws.map(([w, v]) => `<i title="${esc(w)}: ${v} m a minute" style="flex:1;background:var(--accent);opacity:.75;height:${Math.max(3, v / mx * 44)}px;border-radius:2px"></i>`).join('')}</div>
    <div class="mono" style="margin-top:4px">${ws[0][0].split('-')[0]} to ${ws[ws.length - 1][0].split('-')[1]}${q.latest_drop_pct != null && ws.length === Object.keys(q.hi_m_per_min_by_window).length ? ` · down ${q.latest_drop_pct}% on his first half-hour (team ${F_.team_drop_pct}%)${q.tiring ? ' · tiring' : ''}` : ''}</div>`;
}
function dutyBlock(pid, tm) {  // his job without the ball, window by window (completed windows only, as on the board)
  const D_ = (S.D.duties || [])[tm];
  const ws = D_ ? D_.rows.filter(r => r.t1 <= S.t && r.players[pid]).map(r => [r, r.players[pid]]) : [];
  if (!ws.length) return '';
  const ch = D_.changes.filter(c => c.id === pid && c.confirmed_t <= S.t);
  return `<div class="label">Without the ball</div><table class="stats"><thead><tr><th>Window</th><th>Closes down</th><th>Where</th><th>Tracks</th><th>Holds</th></tr></thead><tbody>
    ${ws.map(([r, q]) => `<tr><td>${clockAt(r.t0)}–${clockAt(r.t1)}</td><td>${pct(q.press)}</td><td>${q.press_height_m != null ? conv('len', q.press_height_m) : '–'}</td>
      <td>${q.tracks && q.tracks_share >= .5 ? esc(q.tracks) : 'zone'}</td><td>${esc(q.holds || '–')}</td></tr>`).join('')}</tbody></table>
    ${ch.length ? `<div class="mono" style="margin-top:6px">${ch.map(c => `${esc(c.at)}: ${esc(c.change)}`).join('<br>')}</div>` : ''}`;
}

function playerView() {
  const p = S.info[S.player], s = S.D.players[S.player] || {}, side = p.team ? 'away' : 'home';
  const kv = kvFor(p.role).map(([k, l, f]) => `<div><b>${s[k] == null ? '–' : f ? f(s[k]) : s[k]}</b><span>${l}</span></div>`).join('');
  const types = GROUPS[S.group];
  const acts = S.D.events.filter(e => e.player === S.player && e.type !== 'goal' && (types ? types.includes(e.type) : e.type !== 'pressure'));
  const rows = acts.map(e => {
    const v = verdict(e), out = e.outcome ? `<span class="${v > 0 ? 'ok' : v < 0 ? 'no' : ''}">${esc(e.outcome.replace(/_/g, ' '))}</span>` : '<span></span>';
    return `<li class="${e.t > S.t ? 'future' : ''}"><span class="clk">${esc(e.clock)}</span><span>${label(e.type)}${e.xg ? ` · xG ${e.xg.toFixed(2)}` : ''}${quality(e) ? ` · ${quality(e)}` : ''}</span>${out}
      <span><button class="link" data-seek="${e.t - 3}">Watch</button><button class="link" data-clip-event="${e.id}">Clip</button></span></li>`;
  }).join('');
  const chips = Object.keys(GROUPS).map(gname => `<button class="chip" data-group="${gname}" aria-pressed="${gname === S.group}">${gname}</button>`).join('');
  const following = S.profile.playerId === S.player;
  return `<div class="row-between"><button class="back" data-player="">← All players</button>
    <button class="btn${following ? ' primary' : ''}" data-follow="${esc(S.player)}">${following ? 'Following' : 'Follow'}</button></div>
    <div class="player-head"><span class="big-num">${p.number}</span><div><h2>${esc(p.name)}</h2>
      <div class="sub">${esc(S.D.meta[side].name)} · ${esc(p.label || p.role)}${(p.traits || []).length ? ' · ' + esc(p.traits.map(x => x.replace(/_/g, ' ')).join(', ')) : ''}</div>
      <div class="sub">${s.minutes ?? 0} minutes played${p.foot ? ` · ${p.foot === 'left' ? 'Left' : 'Right'}-footed, weak foot ${Math.round(p.weak_foot)}/20` : ''}</div></div><span class="rating" title="Full-match rating">${s.rating ?? '–'}</span></div>
    <div class="label">Full match</div><div class="kv">${kv}</div>
    ${s.passes ? `<div class="label">Pass quality${s.shots ? ' and shooting' : ''}</div><div class="kv"><div><b>${conv('len', s.avg_pass_length_m, 1)}</b><span>Avg pass length</span></div>
      <div><b>${s.avg_pass_difficulty?.toFixed(2) ?? '–'}</b><span>Avg difficulty</span></div><div><b>${s.hard_passes_completed}/${s.hard_passes}</b><span>Hard passes completed</span></div>
      ${s.shots ? `<div><b>${conv('spd', s.top_shot_speed_kmh)}</b><span>Fastest shot</span></div>` : ''}</div>` : ''}
    <div class="bar" style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
      ${S.persona === 'scout' ? `<button class="btn primary" data-report="${esc(S.player)}">Scout report</button>` : `<button class="btn primary" data-hismatch="${esc(S.player)}">His match</button>`}
      <button class="btn" data-reel="Highlights">Highlights reel</button></div>
    ${profileBlocks(S.player, p)}${fatigueBlock(S.player, p.team)}${dutyBlock(S.player, p.team)}
    <div class="label">Actions (${acts.length})</div><div class="chips">${chips}</div>
    <ul class="actions" style="margin-top:10px">${rows || '<li><span></span><span class="empty">No actions in this group.</span></li>'}</ul>
    ${S.group === 'All' ? '<div class="mono" style="margin-top:8px">Pressures are under Defending.</div>' : ''}`;
}

// ---------------------------------------------------------------- hover and click on what is drawn over the pitch
const HITS = [];  // refilled every frame by whatever draws: {pt or seg, tip, go}
const hitPt = (x, y, tip, go) => HITS.push({ x, y, tip, go });
const hitSeg = (x, y, x2, y2, tip, go) => HITS.push({ x, y, x2, y2, tip, go });
const pname = id => (S.info[id] ? `${S.info[id].number} ${S.info[id].name}` : '');
function evTip(e) {
  const out = e.outcome ? ` · ${e.outcome.replace(/_/g, ' ')}` : '';
  const to = e.receiver ? ` to ${pname(e.receiver)}` : e.target ? ` against ${pname(e.target)}` : '';
  return `${e.clock} · ${label(e.type)}${to}${out}${e.xg != null ? ` · xG ${e.xg.toFixed(2)}` : ''}${quality(e) ? ' · ' + quality(e) : ''}\nclick to watch`;
}
function quality(e) {  // pass length, difficulty and ball speed; shot speed
  if (e.type === 'shot' && e.speed) return conv('spd', e.speed * 3.6);
  if (['pass', 'cross'].includes(e.type) && e.length != null) return `${conv('len', e.length)} · difficulty ${(e.difficulty ?? 0).toFixed(2)} · ${conv('spd', e.speed * 3.6)}`;
  return '';
}
function jumpTo(t) { S.reel = null; S.bd = null; seek(t - 1.5); setPlaying(false); }
function nextPass(tm, a, b, row) {  // a passing-network link: each click watches the next completed pass between the two
  const t0 = row.t0 ?? 0, t1 = row.t1 ?? S.t;
  const ps = S.D.events.filter(e => e.team === tm && e.player === a && e.receiver === b && e.outcome === 'complete' && e.t >= t0 && e.t <= t1);
  if (!ps.length) return;
  const e = ps.find(e => e.t > S.t + .5) || ps[0];
  jumpTo(e.t); toast(`${pname(a)} → ${pname(b)}: pass ${ps.indexOf(e) + 1} of ${ps.length}, ${e.clock}`);
}
function hitAt(ev) {
  const r = cv.getBoundingClientRect(), mx = (ev.clientX - r.left) * cv.width / r.width, my = (ev.clientY - r.top) * cv.height / r.height;
  let best = null, bd = 1e9;
  for (const h of HITS) {
    let d;
    if (h.x2 == null) d = Math.hypot(h.x - mx, h.y - my) - 4;  // points win over the lines they sit on
    else { const dx = h.x2 - h.x, dy = h.y2 - h.y, u = Math.max(0, Math.min(1, ((mx - h.x) * dx + (my - h.y) * dy) / (dx * dx + dy * dy || 1))); d = Math.hypot(h.x + u * dx - mx, h.y + u * dy - my); }
    if (d < (h.x2 == null ? 8 : 5) && d <= bd) { bd = d; best = h; }
  }
  return best;
}

// ---------------------------------------------------------------- breakdowns: the move behind a goal or big chance, paused and drawn on
const BD_KIND = { regain: 'Winning it back', shape: 'The shapes', options: 'The pass', shot: 'The shot' };
const BD_RATE = 1.2;  // the move plays at about real speed between the stops
function bdBtn(t) { const i = (S.D.breakdowns || []).findIndex(b => Math.abs(b.t - t) <= 3); return i < 0 ? '' : `<button class="jump" data-bd="${i}">Breakdown</button>`; }
function startBreakdown(b) { S.reel = null; S.view = null; S.clip = null; S.bd = { b, i: 0, hold: 0, at: 0 }; seek(b.start); setPlaying(true); }
function bdTick(now) {
  const bd = S.bd, st = bd.b.stops[bd.i], dt = now - S.last;
  if (bd.hold) {
    if (!S.playing) bd.hold += dt;  // paused: the stop waits
    else if (now >= bd.hold) { bd.hold = 0; bd.i++; }
  } else if (S.playing) {
    S.t = Math.min(S.D.meta.duration, S.t + dt / 1000 * BD_RATE);
    if (st && S.t >= st.t) { S.t = st.t; bd.at = now; bd.hold = now + Math.max(5000, st.caption.length * 60); sayLine(tr(st.caption)); }
    else if (!st && S.t >= bd.b.end) { S.bd = null; setPlaying(false); toast('End of the breakdown.'); }
  }
}
function bdNext() {
  const bd = S.bd; if (!bd) return;
  if (bd.hold) { bd.hold = 0; bd.i++; } else { const st = bd.b.stops[bd.i]; S.t = st ? st.t : bd.b.end; }
  setPlaying(true);
}
function tag(x, y, text, col) {  // a label with a dark backing, centred on x, bottom at y
  g.font = '600 14px "Barlow Condensed", "Segoe UI", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'alphabetic';
  const w = g.measureText(text).width + 10;
  g.fillStyle = 'rgba(12,13,14,.82)'; g.fillRect(x - w / 2, y - 16, w, 19);
  g.fillStyle = col || '#f1f2f4'; g.fillText(text, x, y - 2);
}
function arrow(x1, y1, x2, y2, col) {
  const a = Math.atan2(y2 - y1, x2 - x1);
  g.strokeStyle = g.fillStyle = col; g.lineWidth = 2.5; g.setLineDash([7, 5]);
  g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2 - 8 * Math.cos(a), y2 - 8 * Math.sin(a)); g.stroke(); g.setLineDash([]);
  g.beginPath(); g.moveTo(x2, y2); g.lineTo(x2 - 12 * Math.cos(a - .45), y2 - 12 * Math.sin(a - .45)); g.lineTo(x2 - 12 * Math.cos(a + .45), y2 - 12 * Math.sin(a + .45)); g.closePath(); g.fill();
}
function paintStop(st, f) {
  const P = k => [px(f[5 + 2 * k]), py(f[6 + 2 * k])], lw = g.lineWidth;
  if (st.hull) {  // the defenders' block
    const pts = st.hull[1].map(P);
    g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    g.fillStyle = teamCol(st.hull[0]) + '1c'; g.fill(); g.strokeStyle = teamCol(st.hull[0]); g.lineWidth = 1; g.setLineDash([4, 4]); g.stroke(); g.setLineDash([]);
  }
  if (st.between) {  // the space between the defenders' back line and the line in front of it
    const [a, b, y0, y1] = st.between, x0 = px(Math.min(a, b)), x1 = px(Math.max(a, b));
    g.fillStyle = 'rgba(79,156,249,.16)'; g.fillRect(x0, py(y0), x1 - x0, py(y1) - py(y0));
    tag((x0 + x1) / 2, py(y0) - 4, 'Space between the lines', COL.accent);
  }
  if (st.last_line) {
    const x = px(st.last_line[1]);
    g.strokeStyle = teamCol(st.last_line[0]); g.lineWidth = 2; g.setLineDash([10, 6]); g.beginPath(); g.moveTo(x, py(0)); g.lineTo(x, py(W)); g.stroke(); g.setLineDash([]);
    tag(x, py(0) + 18, 'Last line');
  }
  for (const ln of st.lines || []) for (const row of ln.rows) {  // each line of each team, joined across the pitch
    const pts = row.map(P).sort((p, q) => p[1] - q[1]);
    g.strokeStyle = teamCol(ln.team); g.lineWidth = 2.5; g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke();
  }
  (st.lines || []).forEach((ln, i) => { g.font = '700 22px "Barlow Condensed", sans-serif'; const w = g.measureText(ln.label).width; const x = i ? cv.width - PAD - w / 2 - 8 : PAD + w / 2 + 8; tag(x, cv.height - 6, ln.label, teamCol(ln.team)); });
  if (st.zone) {  // the numbers around the ball
    const [x, y, r, us, them] = st.zone;
    g.strokeStyle = 'rgba(255,255,255,.7)'; g.lineWidth = 1.5; g.setLineDash([3, 5]); g.beginPath(); g.ellipse(px(x), py(y), r * SX, r * SY, 0, 0, 7); g.stroke(); g.setLineDash([]);
    tag(px(x), py(y) - r * SY - 4, `${us} v ${them} around the ball`);
  }
  for (const [a, b, c] of st.tris || []) {  // passing triangles
    const pts = [a, b, c].map(P);
    g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    g.fillStyle = 'rgba(79,156,249,.14)'; g.fill();
  }
  for (const [a, b, chosen] of st.opts || []) {  // every open lane from the passer; the one he picked is solid
    const [x1, y1] = P(a), [x2, y2] = P(b);
    g.strokeStyle = COL.accent; g.lineWidth = chosen ? 4 : 1.8; g.setLineDash(chosen ? [] : [6, 5]);
    g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); g.setLineDash([]);
  }
  for (const [k, x0, y0, x1, y1] of st.runs || []) arrow(px(x0), py(y0), px(x1), py(y1), COL.good);
  if (st.cone) {  // the shooter's view of the goal
    const [x, y] = P(st.cone[0]), gx = px(st.cone[1]);
    g.beginPath(); g.moveTo(x, y); g.lineTo(gx, py(W / 2 - 3.66)); g.lineTo(gx, py(W / 2 + 3.66)); g.closePath();
    g.fillStyle = 'rgba(232,93,93,.18)'; g.fill(); g.strokeStyle = 'rgba(232,93,93,.8)'; g.lineWidth = 1.5; g.stroke();
  }
  for (const k of [...(st.ring || []), ...(st.keeper || [])]) { const [x, y] = P(k); g.strokeStyle = '#fff'; g.lineWidth = 2.5; g.beginPath(); g.ellipse(x, y + 3, 16, 11, 0, 0, 7); g.stroke(); }
  for (const k of st.names || []) { const [x, y] = P(k), p = who(k, S.t); if (p) tag(x, y - 15, p.name.split(' ').pop()); }
  g.lineWidth = lw;
}

// ---------------------------------------------------------------- analyst board (the toolkit's numbers, last completed window only)
const viewOn = () => !!(S.view && S.view.kind !== 'move');
const boardOn = () => viewOn() || S.persona === 'analyst' && S.tab === 'Board' && S.D && S.D.board && S.board.mode !== 'Momentum';
const toWorld = (team, x, y) => (team ? [L - x, W - y] : [x, y]);
const lastWin = (rows, w) => { const i = Math.floor(S.t / w) - 1; return i >= 0 ? rows[Math.min(i, rows.length - 1)] : null; };
const teamCol = tm => (tm ? COL.away : COL.home);
const num = id => (S.info[id] ? S.info[id].number : '');

function paintShape(tm, sh) {
  if (!sh) return;
  {
      const [lx] = toWorld(tm, sh.line, 0);
      g.setLineDash([6, 5]); g.strokeStyle = teamCol(tm); g.lineWidth = 1.5; g.beginPath(); g.moveTo(px(lx), py(0)); g.lineTo(px(lx), py(W)); g.stroke(); g.setLineDash([]);
      for (const [id, x, y] of sh.positions) {
        const [wx, wy] = toWorld(tm, x, y);
        hitPt(px(wx), py(wy), `${pname(id)}\naverage position ${conv('len', x)} from his own goal`);
        g.beginPath(); g.arc(px(wx), py(wy), 12, 0, 7); g.fillStyle = 'rgba(12,13,14,.75)'; g.fill(); g.lineWidth = 2.5; g.strokeStyle = teamCol(tm); g.stroke();
        g.fillStyle = '#f1f2f4'; g.font = '600 11px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(num(id), px(wx), py(wy) + .5);
      }
  }
}
function paintNetwork(tm, row) {
  if (!row) return;
  {
      const max = Math.max(1, ...row.links.map(l => l[2]));
      for (const [a, b, n] of row.links) {
        if (!row.nodes[a] || !row.nodes[b]) continue;
        const [ax, ay] = toWorld(tm, ...row.nodes[a]), [bx, by] = toWorld(tm, ...row.nodes[b]);
        g.globalAlpha = .25 + .75 * n / max; g.strokeStyle = teamCol(tm); g.lineWidth = 1 + 7 * n / max;
        g.beginPath(); g.moveTo(px(ax), py(ay)); g.lineTo(px(bx), py(by)); g.stroke();
        hitSeg(px(ax), py(ay), px(bx), py(by), `${pname(a)} → ${pname(b)}\n${n} completed passes · click to watch them in turn`, () => nextPass(tm, a, b, row));
      }
      g.globalAlpha = 1;
      for (const [id, xy] of Object.entries(row.nodes)) {
        const [wx, wy] = toWorld(tm, ...xy);
        g.beginPath(); g.arc(px(wx), py(wy), 11, 0, 7); g.fillStyle = teamCol(tm); g.fill();
        hitPt(px(wx), py(wy), `${pname(id)}\naverage position on the ball · ${row.links.reduce((s, l) => s + (l[0] === id || l[1] === id ? l[2] : 0), 0)} passes in his links`);
        g.fillStyle = '#0c0d0e'; g.font = '600 10px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(num(id), px(wx), py(wy) + .5);
      }
  }
}
// out of possession, read from tracking (analytics/tools.py duties): where each man holds his space, how much he closes the ball down, whom he tracks
const dutyRow = tm => { const D_ = (S.D.duties || [])[tm]; return D_ ? D_.rows.filter(r => r.t1 <= S.t).pop() || null : null; };  // windows restart at the second-half kick-off
const pct = v => (v == null ? '–' : Math.round(v * 100) + '%');
function dutyTip(id, q) {
  return `${pname(id)}\ncloses the ball down ${pct(q.press)} of their possession${q.press_height_m != null ? `, ${conv('len', q.press_height_m)} from his goal` : ''}`
    + `\ntracks a man ${pct(q.mark)}${q.tracks ? `, mostly ${q.tracks} (${pct(q.tracks_share)})` : ''}${q.holds ? `\nholds his space ${q.holds}` : ''}`;
}
function paintDuties(tm, row) {
  if (!row) return;
  const at = {};
  for (const [id, q] of Object.entries(row.players)) if (q.holds_xy) at[id] = toWorld(tm, ...q.holds_xy);
  const opp = (S.D.duties[1 - tm] || {}).rows, orow = opp && opp.find(r => r.t0 === row.t0);
  for (const [id, q] of Object.entries(row.players)) {  // a line to the man he tracks, where that man held his own space
    const o = q.tracks_id && orow && orow.players[q.tracks_id];
    if (!at[id] || !o || !o.holds_xy || q.tracks_share < .5) continue;
    const [ox, oy] = toWorld(1 - tm, ...o.holds_xy);
    g.setLineDash([4, 4]); g.strokeStyle = 'rgba(241,242,244,.45)'; g.lineWidth = 1.5; g.beginPath(); g.moveTo(px(at[id][0]), py(at[id][1])); g.lineTo(px(ox), py(oy)); g.stroke(); g.setLineDash([]);
    g.beginPath(); g.arc(px(ox), py(oy), 5, 0, 7); g.strokeStyle = teamCol(1 - tm); g.lineWidth = 2; g.stroke();
  }
  for (const [id, q] of Object.entries(row.players)) {
    if (!at[id]) continue;
    const [X, Y] = [px(at[id][0]), py(at[id][1])], r = 9 + Math.min(10, q.press * 80);  // the ring grows with how much he closes down
    hitPt(X, Y, dutyTip(id, q));
    g.beginPath(); g.arc(X, Y, r, 0, 7); g.fillStyle = 'rgba(12,13,14,.75)'; g.fill(); g.lineWidth = 2.5; g.strokeStyle = teamCol(tm); g.stroke();
    g.fillStyle = '#f1f2f4'; g.font = '600 11px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(num(id), X, Y + .5);
  }
}
function drawBoard(t) {
  const B = S.D.board, tm = S.board.team;
  g.save();
  if (S.board.mode === 'Shape') {
    const row = lastWin(B.shape[tm], B.shape_window);
    paintShape(tm, row && row[S.board.phase]);
  } else if (S.board.mode === 'Network') {
    paintNetwork(tm, lastWin(B.network[tm], B.net_window));
  } else if (S.board.mode === 'Duties') {
    paintDuties(tm, dutyRow(tm));
  } else if (S.board.mode === 'Press') {
    for (const [rt, team, x, y, by, how] of B.regains) {
      if (rt > t || rt < t - B.net_window || team !== tm) continue;
      g.globalAlpha = .35 + .65 * (1 - (t - rt) / B.net_window);
      g.beginPath(); g.arc(px(x), py(y), 6, 0, 7); g.fillStyle = teamCol(tm); g.fill(); g.strokeStyle = '#0c0d0e'; g.lineWidth = 1; g.stroke();
      hitPt(px(x), py(y), `${clockAt(rt)} · ball won${by ? ` by ${pname(by)}` : ''}${how ? ` (${label(how).toLowerCase()})` : ''}\nclick to watch`, () => jumpTo(rt));
    }
  }
  g.restore();
}

function momentumSvg(h = 150) {
  const M = S.D.board.momentum, n = Math.max(1, Math.floor(S.t / M.window_s)), rows = M.series.slice(0, n), w = 368, mid = h / 2 - 5;
  const mx = Math.max(.3, ...rows.flatMap(r => r.xg)), bw = w / Math.max(rows.length, 18), bh = mid - 10;
  const bars = rows.map((r, i) => `<rect x="${i * bw + 1}" y="${mid - r.xg[0] / mx * bh}" width="${bw - 2}" height="${r.xg[0] / mx * bh}" fill="var(--home)"/>
    <rect x="${i * bw + 1}" y="${mid}" width="${bw - 2}" height="${r.xg[1] / mx * bh}" fill="var(--away)"/>`).join('');
  const tilt = rows.map((r, i) => `${i * bw + bw / 2},${mid - (r.field_tilt_home - .5) * 2 * bh}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="xG by five minutes for both teams, with field tilt">
    <line x1="0" x2="${w}" y1="${mid}" y2="${mid}" stroke="var(--line-strong)"/>${bars}
    <polyline points="${tilt}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    <text x="0" y="12" fill="var(--text-3)" font-size="10">${esc(S.D.meta.home.code)} xG ↑</text><text x="0" y="${h - 4}" fill="var(--text-3)" font-size="10">${esc(S.D.meta.away.code)} xG ↓ · line: field tilt</text></svg>`;
}

function liveStrip() {  // the fan's glance at the game: who is on top, five minutes at a time, and the chances now
  if (!S.D.board || S.t < 300) return '';
  return `${winBar()}${rhythmStrip()}<div class="label" style="margin-top:10px">${esc(tr('Who is making the chances · five minutes at a time'))}</div>${momentumSvg(90)}`;
}
// control vs chaos by five minutes (analytics/tools.py rhythm): a chip per finished window, the side in control in its colour
const RH_COL = st => (st.startsWith('control') ? (st.endsWith(S.D.meta.home.name) ? 'var(--home)' : 'var(--away)') : st === 'chaos' ? 'var(--bad)' : st === 'stop-start' ? 'var(--text-3)' : 'var(--surface-3)');
function rhythmStrip(full) {
  const R = S.D.board && S.D.board.rhythm;
  if (!R) return '';
  const done = R.by_window.filter(r => r.t1 <= S.t), last = done[done.length - 1];
  if (!last) return '';
  const word = st => (st.startsWith('control') ? `${st.slice(9)} in control` : st === 'chaos' ? 'Chaos' : st === 'stop-start' ? 'Stop-start' : 'Even');
  const plain = st => (st.startsWith('control') ? `${st.slice(9)} keeping the ball and building their moves` : st === 'chaos' ? 'End to end: the ball keeps changing hands'
    : st === 'stop-start' ? 'Lots of stoppages, little flowing play' : 'Neither side on top');  // the fan's version of the same reading
  const tip = r => `${r.window}: ${word(r.state)} · settled ${r.settled_pct}% · broken ${r.broken_pct}% · ${r.turnovers_per_min} turnovers a minute · ${r.passes_per_min} passes a minute · ball in play ${r.ball_in_play_pct}%`;
  const chips = R.by_window.map(r => `<i title="${esc(r.t1 <= S.t ? tip(r) : '')}" style="flex:1;height:10px;border-radius:2px;background:${r.t1 <= S.t ? RH_COL(r.state) : 'transparent'};border:1px solid var(--line)"></i>`).join('');
  const spells = full ? R.spells.filter(x => x.end_t <= S.t) : [];
  return `<div class="label" style="margin-top:10px">${esc(tr('Control vs chaos'))} · ${esc(tr(word(last.state)))} (${esc(last.window)})</div>
    <div style="display:flex;gap:2px">${chips}</div>
    <div class="mono" style="margin-top:4px">${S.persona === 'fan' ? esc(tr(plain(last.state))) : `Settled ${last.settled_pct}% · broken ${last.broken_pct}% · ${last.turnovers_per_min} turnovers and ${last.passes_per_min} passes a minute of play`}</div>
    ${full ? `<div class="mono" style="margin-top:4px">Settled: a move of four passes or fifteen seconds. Broken: lost inside eight seconds with at most one pass.</div>
      ${spells.length ? `<table class="stats" style="margin-top:6px"><tbody>${spells.map(x => `<tr><td>${esc(word(x.state))}</td><td>${esc(x.start)}–${esc(x.to)}</td></tr>`).join('')}</tbody></table>` : ''}` : ''}`;
}
function winSvg() {
  const W_ = S.D.board.winprob, rows = W_.series.filter(r => r.t <= S.t), w = 368, h = 110, dx = w / Math.max(W_.series[W_.series.length - 1].t / 60, 1);
  const line = k => rows.map(r => `${(r.t / 60 * dx).toFixed(1)},${(h - r[k] * h).toFixed(1)}`).join(' ');
  const tps = W_.turning_points.filter(tp => tp.t <= S.t).map(tp => `<line x1="${(tp.t / 60 * dx).toFixed(1)}" x2="${(tp.t / 60 * dx).toFixed(1)}" y1="0" y2="${h}" stroke="var(--line-strong)" stroke-dasharray="3 3"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Win probability for each side over the match; dashed lines mark turning points">${tps}
    <polyline points="${line('home')}" fill="none" stroke="var(--home)" stroke-width="2"/><polyline points="${line('away')}" fill="none" stroke="var(--away)" stroke-width="2"/>
    <polyline points="${line('draw')}" fill="none" stroke="var(--text-3)" stroke-width="1.5" stroke-dasharray="4 3"/></svg>
    <div class="mono">Simple model: the league scoring rate blended with each side's xG so far; dashed lines are the turning points.</div>`;
}
function boardView() {
  const B = S.D.board;
  if (!B) return '<div class="empty">Rebuild the match data (scripts/build_site.py) for the board.</div>';
  const M = S.D.meta, b = S.board, chip = (attr, v, txt, on) => `<button class="chip" data-${attr}="${esc(v)}" aria-pressed="${on}">${esc(txt)}</button>`;
  let body = '';
  if (b.mode === 'Shape') {
    const row = lastWin(B.shape[b.team], B.shape_window), sh = row && row[b.phase];
    body = !row ? '<div class="empty">The first five minutes are not over yet.</div>' : !sh ? '<div class="empty">Too little of that phase in this window.</div>' :
      `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · ${clockAt(row.t0)}–${clockAt(row.t1)} · ${b.phase === 'in_possession' ? 'with the ball' : 'without the ball'}</div>
       <div class="kv"><div><b>${esc(sh.formation)}</b><span>Shape</span></div><div><b>${conv('len', sh.line)}</b><span>Defensive line</span></div><div><b>${conv('len', sh.length).split(' ')[0]} × ${conv('len', sh.width)}</b><span>Block</span></div></div>
       <div class="mono" style="margin-top:8px">Average positions over the last completed five minutes; the dashed line is the defensive line. The two phases are read separately: a team can line up one way with the ball and another without it.</div>`;
  } else if (b.mode === 'Network') {
    const row = lastWin(B.network[b.team], B.net_window);
    body = !row ? '<div class="empty">The first fifteen minutes are not over yet.</div>' :
      `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · passing network ${clockAt(row.t0)}–${clockAt(row.t1)}</div>
       <table class="stats"><tbody>${row.links.slice(0, 6).map(([a, c, n]) => `<tr><td>${num(a)} ${esc(S.info[a].name)} → ${num(c)} ${esc(S.info[c].name)}</td><td>${n}</td></tr>`).join('')}</tbody></table>
       <div class="mono" style="margin-top:8px">Entries into the final third, left / centre / right: ${row.entries.join(' / ')}</div>`;
  } else if (b.mode === 'Duties') {
    const D_ = (S.D.duties || [])[b.team], row = dutyRow(b.team);
    if (!D_) body = '<div class="empty">Rebuild the match data (scripts/build_site.py) for duties.</div>';
    else {
      const ps = row ? Object.entries(row.players).sort((x, y) => y[1].press - x[1].press) : [];
      const seen = D_.changes.filter(c => c.confirmed_t <= S.t), sc = D_.score;
      body = !row ? '<div class="empty">The first fifteen minutes are not over yet.</div>' :
        `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · without the ball · ${clockAt(row.t0)}–${clockAt(row.t1)}</div>
        <table class="stats"><thead><tr><th>Player</th><th title="Share of their possession spent closing the ball down">Closes down</th><th title="Where, from his own goal">Where</th><th>Tracks</th></tr></thead><tbody>
        ${ps.map(([id, q]) => `<tr title="${esc(dutyTip(id, q))}"><td>${num(id)} ${esc(S.info[id].name)}</td><td>${pct(q.press)}</td><td>${q.press_height_m != null ? conv('len', q.press_height_m) : '–'}</td><td>${q.tracks && q.tracks_share >= .5 ? esc(q.tracks) : '<span class="mono">zone</span>'}</td></tr>`).join('')}</tbody></table>
        <div class="label">Changes</div>${seen.length ? `<ul class="actions">${seen.map(c => `<li><span class="clk">${esc(c.at)}</span><span>${esc(c.player)} ${esc(c.change)}</span><span></span><span><button class="link" data-seek="${c.t}">Watch</button></span></li>`).join('')}</ul>` : '<div class="mono">None confirmed yet (a change counts once two windows show it).</div>'}
        <div class="mono" style="margin-top:8px">Read from tracking alone, as with real data: who closes the ball down, who follows a man (dashed line to him) and where each holds his space (the ring grows with closing down). Against the engine's own instructions: ${pct(sc && sc.frame_agreement)} of moments agree; the man read as tracked is the one he was given ${pct(sc && sc.tracked_man_right)} of the time; pressing height ${sc ? conv('len', sc.press_height_read_m) : '–'} read, ${sc ? conv('len', sc.press_height_true_m) : '–'} true.</div>`;
    }
  } else if (b.mode === 'Press') {
    const rows = B.pressing[b.team], cur = Math.floor(S.t / B.net_window);
    body = `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · pressing by 15 minutes</div>
      <table class="stats"><thead><tr><th>Window</th><th>PPDA</th><th>Height</th><th>Counter-press</th></tr></thead><tbody>${rows.slice(0, cur).map(r => `<tr><td>${esc(r.window.join('–'))}</td><td>${r.ppda ?? '–'}</td><td>${conv('len', r.action_height_m)}</td><td>${r.counterpress_wins}</td></tr>`).join('') || '<tr><td colspan="4">First window not over yet.</td></tr>'}</tbody></table>
      <div class="mono" style="margin-top:8px">PPDA: opponent passes per defensive action in their build-up (lower = harder press). Dots on the pitch: where the ball was won in the last fifteen minutes.</div>`;
  } else {
    const sp = B.momentum.dominant_spells.filter(x => x.end_t <= S.t);
    body = `${winBar()}<div class="label">Win probability over the match</div>${winSvg()}${rhythmStrip(true)}<div class="label">Momentum · xG by five minutes</div>${momentumSvg()}
      <div class="label">Spells of dominance</div>${sp.length ? `<table class="stats"><tbody>${sp.map(x => `<tr><td>${esc(x.team)} ${esc(x.start)}–${esc(x.end)}</td><td>xG ${x.xg} v ${x.opponent_xg}</td></tr>`).join('')}</tbody></table>` : '<div class="mono">None yet.</div>'}`;
  }
  return `<div class="chips">${['Shape', 'Network', 'Press', 'Duties', 'Momentum'].map(m => chip('bmode', m, m, b.mode === m)).join('')}</div>
    ${b.mode !== 'Momentum' ? `<div class="chips" style="margin-top:8px">${[0, 1].map(t => chip('bteam', t, M[t ? 'away' : 'home'].code, b.team === t)).join('')}
      ${b.mode === 'Shape' ? chip('bphase', 'in_possession', 'With the ball', b.phase === 'in_possession') + chip('bphase', 'out_of_possession', 'Without the ball', b.phase === 'out_of_possession') : ''}</div>` : ''}
    <div style="margin-top:12px">${body}</div>`;
}

// ---------------------------------------------------------------- what if: replay to now, change one instruction, play the rest many times
const WI_SLIDERS = [['press', 'Pressing', 'passive', 'aggressive'], ['line_height', 'Defensive line', 'deep', 'high'], ['directness', 'Build-up', 'patient', 'direct'], ['risk', 'Risk', 'safe', 'adventurous']];
const clockMin = c => { const [a, b] = c.replace("'", '').split('+'); return +a + (+b || 0); };
function whatIfView() {
  const M = S.D.meta, w = S.wi, side = w.team ? 'away' : 'home', rec = M.recipe;
  if (!rec) return '<div class="empty">This match was made before replays were recorded; rebuild it with scripts/run_match.py or scripts/matchday.py.</div>';
  const base = Object.assign({ press: .5, line_height: .5, directness: .5, risk: .5 }, rec[side].tactics), now = clockMin(clockAt(S.t));
  const forms = ['4-3-3', '4-4-2', '4-2-3-1', '3-5-2', '3-4-3', '5-3-2', '4-5-1', '5-4-1'];
  const sl = WI_SLIDERS.map(([k, name, lo, hi]) => { const v = w.change[k] ?? base[k];
    const was = Math.round(base[k] * 100), now_ = Math.round(v * 100), changed = v !== base[k];
    return `<div class="row"><span>${name}</span><div class="wi-track" style="--set:${base[k] * 100}%"><input type="range" min="0" max="1" step="0.05" value="${v}" data-wi="${k}" aria-label="${name}" title="The tick is the team's own setting (${was})"></div>
      <span class="wi-val${changed ? ' chg' : ''}">${changed ? `${was} → ${now_}<button class="wi-undo" data-wiundo="${k}" title="Back to the team's setting (${was})" aria-label="Undo ${name}">↺</button>` : now_}</span></div>
      <div class="mono wi-scale"><span>${lo}</span><span>${hi}</span></div>`; }).join('');
  const r = w.res;
  const pct = x => Math.round(x * 100) + '%';
  const res = w.busy && !r ? `<div class="thinking">Playing the rest of the match 24 times from ${now}': the first pairs arrive in about half a minute</div>`
    : w.err ? `<div class="empty">${esc(w.err)}</div>`
    : r ? `<div class="label">${esc(r.team_name)} from ${esc(r.at.clock)} (${r.at.score[0]}–${r.at.score[1]} at the time)</div>
      <table class="stats"><thead><tr><th>Rest of the match</th><th>As it was</th><th>With the change</th></tr></thead><tbody>
      ${[['Win', 'win', pct], ['Draw', 'draw', pct], ['Loss', 'loss', pct], ['Goals for', 'goals_for'], ['Goals against', 'goals_against'], ['xG for, after', 'xg_for_after'], ['xG against, after', 'xg_against_after'], ['Shots for, after', 'shots_for_after'], ['Shots against, after', 'shots_against_after']]
        .map(([n, k, f]) => { const a = r.as_it_was[k], b = r.with_change[k]; return `<tr><td>${n}</td><td>${f ? f(a) : a}</td><td class="${b !== a ? 'lead' : ''}">${f ? f(b) : b}</td></tr>`; }).join('')}</tbody></table>
      <div class="mono" style="margin-top:6px">${r.runs} simulations each way from the same moment, paired so only the change differs; ${r.runs_with_a_different_result} of ${r.runs} pairs ended with a different score.${r.replay_matches === false ? ' Note: the replay did not reproduce the match exactly.' : ''}</div>
      ${w.busy ? `<div class="thinking">${r.runs} of ${r.of} pairs in; the numbers settle as the rest finish</div>` : '<div class="bar" style="margin-top:10px"><button class="btn" data-act="wi-explain">Ask the newsroom to explain</button></div>'}` : '';
  return `<div class="chips">${[0, 1].map(t => `<button class="chip" data-witeam="${t}" aria-pressed="${w.team === t}">${esc(M[t ? 'away' : 'home'].name)}</button>`).join('')}</div>
    ${benchView(side)}
    <div class="editor" style="margin-top:10px"><h3>From ${now}', ${esc(M[side].name)} change to</h3>${sl}
      <div class="row"><span>System</span><select id="wi-form"><option value="">Keep the current system</option>${forms.map(f => `<option${f === w.formation ? ' selected' : ''}>${f}</option>`).join('')}</select><span></span></div>
      <div class="bar"><button class="btn primary" data-act="wi-run"${w.busy ? ' disabled' : ''}>Play it out</button><button class="btn" data-act="wi-reset" title="Every setting back to what the team is doing">Reset all</button></div></div>
    ${res}`;
}
// the bench assistant (POST /api/assistant): reads the game, proposes two changes, replays the rest of the match with and without each, picks from the results
const CH_WORD = { press: 'pressing', line_height: 'defensive line', compactness: 'compactness', width: 'width', directness: 'directness', risk: 'risk', shoot: 'shooting' };
const chText = ch => Object.entries(ch).map(([k, v]) => (k === 'formation' ? `switch to ${v}` : `${CH_WORD[k] || k} to ${Math.round(v * 100)}`)).join(', ');
function benchView(side) {
  const b = S.bench;
  const head = `<div class="editor" style="margin-top:10px"><h3>Bench assistant</h3><div class="mono">Reads ${esc(S.D.meta[side].name)}'s game at ${esc(clockAt(S.t))}, proposes two changes, then replays the rest of the match with and without each before recommending one.</div>
    <div class="bar" style="margin-top:8px"><button class="btn primary" data-act="bench"${b && b.busy ? ' disabled' : ''}>Advise the manager</button></div>`;
  if (!b || b.side !== side) return head + '</div>';
  if (b.busy) return head + `<div class="thinking">${esc(b.step || 'Reading the game')}</div></div>`;
  if (b.err) return head + `<div class="empty">${esc(b.err)}</div></div>`;
  const r = b.r, pct = x => Math.round(x * 100) + '%';
  const rows = r.tests.map(x => `<tr${r.recommendation && JSON.stringify(x.change) === JSON.stringify(r.recommendation) ? ' class="lead"' : ''}><td>${esc(chText(x.change))}<div class="mono">${esc(x.why)}</div></td>
    <td>${pct(x.as_it_was.win)} / ${pct(x.as_it_was.draw)} / ${pct(x.as_it_was.loss)}</td><td>${pct(x.with_change.win)} / ${pct(x.with_change.draw)} / ${pct(x.with_change.loss)}</td><td>${x.points_gain > 0 ? '+' : ''}${x.points_gain}</td></tr>`).join('');
  return head + `<div class="label">${esc(r.team)} at ${esc(r.minute)} · ${esc(r.score)}</div>
    <p>${esc(tr(r.answer || r.held || ''))}</p>${r.approved ? '<span class="badge ok">Fact-checked</span>' : '<span class="badge warn">Held by fact-checker</span>'}
    <table class="stats" style="margin-top:8px"><thead><tr><th>Change tried</th><th>As it was W/D/L</th><th>With it</th><th>Points</th></tr></thead><tbody>${rows || '<tr><td colspan="4">No usable proposal.</td></tr>'}</tbody></table>
    <div class="mono" style="margin-top:6px">${r.tests[0] ? `${r.tests[0].runs} paired replays each (same random numbers with and without). ` : ''}${r.recommendation ? `Recommended: ${esc(chText(r.recommendation))}.` : 'Recommended: keep it as it is.'} Points = 3 × win + draw, expected.</div>
    ${r.recommendation ? '<div class="bar" style="margin-top:8px"><button class="btn" data-act="bench-apply">Load it into the sliders</button></div>' : ''}</div>`;
}
// server-sent events from a POST response: on(kind, data) for each event (comment lines, the keepalives, are skipped)
async function readEvents(res, on) {
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let k;
    while ((k = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, k); buf = buf.slice(k + 2);
      const kind = (block.match(/^event: (.*)$/m) || [])[1];
      if (kind) on(kind, JSON.parse((block.match(/^data: (.*)$/m) || [])[1] || 'null'));
    }
  }
}
async function runBench() {
  const side = S.wi.team ? 'away' : 'home', b = S.bench = { side, busy: true, step: '' };
  render(true);
  try {
    const res = await fetch('api/assistant', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ match: S.id, t: S.t, team: S.wi.team }) });
    if (!res.ok) throw new Error(res.status === 404 || res.status === 405 ? NO_SERVER : await res.text());
    await readEvents(res, (kind, data) => {
        if (kind === 'step') { b.step = data.tool === 'what_if' ? `Replaying the rest of the match with: ${chText(Object.fromEntries(Object.entries(data.args).filter(([k_]) => k_ !== 'team')))}` : `${data.agent.replace('_', ' ')}: ${data.tool.replace(/_/g, ' ')}`; render(true); }
        if (kind === 'advice') b.r = data;
        if (kind === 'error') throw new Error(data.error);
    });
    if (!b.r) throw new Error('The assistant did not answer.');
  } catch (err) { b.err = err.message.startsWith('The ') ? err.message : 'The assistant did not answer: ' + err.message; }
  b.busy = false; render(true);
}
async function runWhatIf() {
  const w = S.wi, ch = { ...w.change };
  if (w.formation) ch.formation = w.formation;
  if (!Object.keys(ch).length) return toast('Change at least one instruction first.');
  w.busy = true; w.err = ''; w.res = null; w.t = S.t; render(true);
  try {
    const r = await fetch('api/whatif', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ match: S.id, t: S.t, team: w.team, change: ch }) });
    if (!r.ok) throw new Error(r.status === 404 || r.status === 405 ? NO_SERVER : await r.text());
    await readEvents(r, (kind, data) => {  // partial results after each finished pair, then the full result
      if (kind === 'partial' || kind === 'result') { w.res = data; render(true); }
      if (kind === 'error') { w.res = null; throw new Error(data.error); }
    });
    if (!w.res || w.res.runs < w.res.of) throw new Error('the simulation stopped early');
  } catch (err) { w.err = err.message.startsWith('The ') ? err.message : 'The simulation did not run: ' + err.message; }
  w.busy = false; render(true);
}

// ---------------------------------------------------------------- recaps (written in advance by the newsroom, shown once their moment has come)
function recapView() {
  const want = S.persona === 'analyst' ? 'analyst' : 'fan';
  const rs = (S.D.recaps || []).filter(r => r.profile.persona === want && r.t <= S.t + .5).reverse();
  const hl = `<div class="bar" style="margin-bottom:12px"><button class="btn primary" data-act="highlights">Play highlights</button>
    <span class="mono" style="margin-left:8px">The key moments so far, back to back${voiceOn ? ', narrated' : '; switch Voice on to hear them narrated'}.</span></div>`;
  if (!rs.length) return hl + '<div class="empty">The half-time report arrives at the break.</div>';
  return hl + '<ul class="feed asks">' + rs.map(r => {
    const title = r.kind === 'half_time' ? 'Half-time report' : 'Full-time report';
    const watch = (r.watch || []).map(w => `<button class="link" data-seek="${w.t - 6}">Watch ${esc(w.clock)} ${esc(label(w.type).toLowerCase())}</button>`).join(' ');
    const steps = r.steps.map(st => `<li><b>${esc(st.agent.replace('_', ' '))}</b> → ${esc(st.tool.replace(/_/g, ' '))}</li>`).join('');
    const issues = (r.issues || []).flat().filter(Boolean);
    const body = r.approved ? tr(r.answer).split(/\n\s*\n/).map(x => `<p>${esc(x.trim())}</p>`).join('')  // a report the fact-checker held is not published: its draft and the reasons stay one click away
      : `<p class="rejected">The fact-checker held this report after ${r.rewrites} rewrite${r.rewrites === 1 ? '' : 's'}, so it is not published.</p>
        <details class="trail"><summary>The held draft and why</summary><div class="trail-body"><p class="rejected">${esc(r.held || '')}</p><ul>${issues.slice(-4).map(i => `<li>${esc(i)}</li>`).join('')}</ul></div></details>`;
    return `<li><div class="q">${title}</div><div class="meta">${r.approved ? `<span class="badge ok">Fact-checked${r.rewrites ? ` · rewritten ${r.rewrites}×` : ''}</span>` : '<span class="badge">Held by the fact-checker</span>'}</div>
      ${body}${watch ? `<div class="watch">${watch}</div>` : ''}
      <details class="trail"><summary>How the newsroom worked it out (${r.steps.length} steps)</summary><div class="trail-body"><ol class="steps">${steps}</ol></div></details></li>`;
  }).join('') + '</ul>';
}

// ---------------------------------------------------------------- ask the newsroom
function askView() {
  const P = S.profile;
  // suggestions that fit the match so far: no goal or substitution questions before there is one, and the side that conceded by name
  const past = S.D.events.filter(e => e.t <= S.t), lastGoal = past.filter(e => e.type === 'goal' || e.type === 'own_goal').pop(), subs = past.some(e => e.type === 'substitution');
  const conceded = lastGoal && S.D.meta[lastGoal.team ? 'home' : 'away'].name;
  const sug = SUGGEST[S.persona].map(q => (q === 'How is my player doing?' && P.player ? `How is ${P.player} doing?` : q === 'Why did they concede?' && conceded ? `Why did ${conceded} concede?` : q))
    .filter(q => q !== 'How is my player doing?' && !(q === 'Why did they concede?') && !(q === 'Explain the last goal' && !lastGoal) && !(q === 'What changed after the substitutions?' && !subs));
  const opt = (v, cur, txt) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(txt || v)}</option>`;
  const hist = S.asks.slice().reverse().map(a => {
    if (a.pending) return `<li><div class="q">${esc(a.q)}</div><div class="thinking">The newsroom is working on it</div>
      ${(a.live || []).length ? `<ol class="steps live">${a.live.map(st => `<li><b>${esc(st.agent.replace('_', ' '))}</b> ${esc(st.tool.replace(/_/g, ' '))}${Object.values(st.args || {}).some(Boolean) ? ` <span class="mono">${esc(Object.values(st.args).filter(Boolean).join(', '))}</span>` : ''}</li>`).join('')}</ol>` : ''}</li>`;
    if (a.error) return `<li><div class="q">${esc(a.q)}</div><div class="empty">${esc(a.error)}</div></li>`;
    const r = a.r;
    const badge = r.approved ? `<span class="badge ok">Fact-checked${r.rewrites ? ` · rewritten ${r.rewrites}×` : ''}</span>` : '<span class="badge warn">Held by fact-checker</span>';
    const steps = r.steps.map(st => `<li><b>${esc(st.agent.replace('_', ' '))}</b> → ${esc(st.tool.replace(/_/g, ' '))}${Object.values(st.args || {}).some(Boolean) ? ` <span class="mono">${esc(Object.values(st.args).filter(Boolean).join(', '))}</span>` : ''}</li>`).join('');
    const watch = (r.watch || []).map(w => `<button class="link" data-seek="${w.t - 6}">Watch ${esc(w.clock)} ${esc(label(w.type).toLowerCase())}</button>`).join(' ')
      + (r.views || []).map((v, i) => `<button class="link" data-ansview="${S.asks.indexOf(a)}:${i}">${v.kind === 'move' ? 'Replay' : 'Show'}: ${esc(v.title)}</button>`).join(' ');
    return `<li><div class="q">${esc(a.q)}</div><div class="meta">${badge}${r.cached ? '<span class="badge">cached</span>' : ''}<span class="mono">as of ${esc(a.clock)}</span></div>
      <p${r.approved ? '' : ' class="rejected"'}>${esc(r.answer || r.held)}</p>${watch ? `<div class="watch">${watch}</div>` : ''}
      <details class="trail"><summary>How the newsroom worked it out (${r.steps.length} steps)</summary><div class="trail-body"><ol class="steps">${steps}</ol>
      <h4>Findings handed to the writer</h4><pre>${esc(r.findings)}</pre></div></details></li>`;
  }).join('');
  return `<div class="ask-controls">
      <label>Depth <select id="p-depth">${opt('quick', P.depth, 'Quick')}${opt('standard', P.depth, 'Standard')}${opt('deep', P.depth, 'Deep')}</select></label>
      ${P.player ? `<span class="badge">Following ${esc(P.player)}</span>` : ''}${P.club ? `<span class="badge">${esc(P.club)} fan</span>` : ''}</div>
    <div class="ask-box"><input type="text" id="ask-q" placeholder="Ask about this match" value="${esc(S.askDraft)}" maxlength="300" aria-label="Your question"><button class="btn primary" data-act="ask">Ask</button></div>
    <div class="chips" style="margin:10px 0 4px">${sug.map(q => `<button class="chip" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div>
    <div class="mono" style="margin-bottom:6px">Answers use only what has happened up to the current minute.</div>
    <ul class="feed asks">${hist}</ul>`;
}

const NO_SERVER = 'The ask desk needs the newsroom server (python -m server.app).';
async function askNewsroom(q) {
  q = (q || '').trim();
  if (!q) return;
  const P = S.profile, a = { q, pending: true, clock: clockAt(S.t) };
  S.asks.push(a); S.askDraft = ''; S.askVer++; render(true);
  try {
    const res = await fetch('api/ask?stream=1', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match: S.id, question: q, t: Math.max(60, S.t), profile: { persona: S.persona, depth: P.depth, language: P.language, club: P.club, player: P.player, units: P.units, metric: P.metric ? METRICS[P.metric] : '' } }) });
    if (!res.ok) throw new Error(res.status === 404 || res.status === 405 || res.status === 501 ? NO_SERVER : res.status === 429 ? await res.text() : `The newsroom could not answer (${res.status}).`);
    // server-sent events: each step of the newsroom as it happens, then the answer
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    a.live = [];
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, k); buf = buf.slice(k + 2);
        const kind = (block.match(/^event: (.*)$/m) || [])[1], data = JSON.parse((block.match(/^data: (.*)$/m) || [])[1] || 'null');
        if (kind === 'step') { a.live.push(data); S.askVer++; render(true); }
        if (kind === 'answer') a.r = data;
      }
    }
    if (!a.r) throw new Error('The newsroom could not answer.');
  } catch (err) { a.error = err.message.startsWith('The ') ? err.message : NO_SERVER; }
  a.pending = false; S.askVer++; render(true);
}

// ---------------------------------------------------------------- clips
const span = c => `${clockAt(c.start)} – ${clockAt(c.end)} · ${(c.end - c.start).toFixed(1)} s`;
const allClips = () => [...S.custom, ...S.D.clips];
function clipsView() {
  const c = S.clip, d = S.D.meta.duration;
  const editor = c ? `<div class="editor" id="editor"><h3>Edit clip</h3>
      <input type="text" id="clip-title" value="${esc(c.title)}" aria-label="Clip title">
      ${['start', 'end'].map(k => `<div class="row"><span>${k === 'start' ? 'Start' : 'End'}</span>
        <input type="range" id="clip-${k}" min="${Math.max(0, c.t - 60)}" max="${Math.min(d, c.t + 60)}" step="0.2" value="${c[k]}">
        <span class="nudge"><button data-nudge="${k}:-1" aria-label="${k} back one second">−1</button><button data-nudge="${k}:1" aria-label="${k} forward one second">+1</button></span></div>`).join('')}
      <div class="mono" id="clip-span">${span(c)}</div>
      <div class="bar"><button class="btn" data-act="preview">Preview</button><button class="btn primary" data-act="export">Export JSON</button>
        ${c.custom ? '<button class="btn" data-act="delete">Delete</button>' : ''}<button class="btn" data-act="close">Close</button></div></div>` : '';
  const li = x => `<li><button data-clip="${esc(x.id)}" aria-current="${!!c && c.id === x.id}"><span>${esc(x.title)}</span><span class="dur">${(x.end - x.start).toFixed(0)} s</span></button></li>`;
  const seen = x => x.t <= S.t + .5;  // only what has happened by now: a later goal in the list would spoil the replay
  const bds = (S.D.breakdowns || []).map((b, i) => !seen(b) ? '' : `<li><button data-bd="${i}"><span>${esc(b.title)}</span><span class="dur">${b.stops.length} stops</span></button></li>`).join('');
  return editor + (bds ? `<div class="label">Breakdowns: the move paused and drawn on the pitch</div><ul class="clips">${bds}</ul>` : '') + (S.custom.length ? `<div class="label">Your clips</div><ul class="clips">${S.custom.map(li).join('')}</ul>` : '')
    + `<div class="label">Suggested by the newsroom (${S.D.clips.filter(seen).length})</div><ul class="clips">${S.D.clips.filter(seen).map(li).join('') || '<li class="empty">The first suggestions arrive with the first chances.</li>'}</ul>
    <div class="mono" style="margin-top:10px">Clips export as JSON: the events and tracking frames inside the range, ready for an overlay or edit tool.</div>
    <div class="label">Broadcast pack</div>
    <div class="mono" style="margin-bottom:8px">Timed overlay cues (lower thirds) in ${esc(S.profile.language)}, the half-time and full-time reports as studio talking points, and the clip list, as one JSON file.</div>
    <button class="btn" data-act="pack">Export broadcast pack</button>`;
}

function openClip(c) { S.clip = { ...c }; seek(c.start); setPlaying(true); render(true); }
function clipFromEvent(id) {
  const e = S.D.events.find(x => x.id === +id), p = S.info[e.player];
  const c = { id: 'c' + Date.now(), custom: true, kind: e.type, t: e.t, start: Math.max(0, e.t - 6), end: Math.min(S.D.meta.duration, e.t + 4), title: `${e.clock} ${label(e.type)} · ${p ? p.name : ''}` };
  S.custom.unshift(c); saveCustom();
  S.tab = 'Clips'; setTabs();
  openClip(c); toast('Clip added. Adjust the range, then export.');
}
function updateClip(k, v) {
  const c = S.clip;
  c[k] = Math.round(v * 10) / 10;
  if (c.end - c.start < 1) k === 'start' ? c.start = c.end - 1 : c.end = c.start + 1;
  const stored = S.custom.find(x => x.id === c.id);
  if (stored) { Object.assign(stored, { start: c.start, end: c.end, title: c.title }); saveCustom(); }
  $('clip-start').value = c.start; $('clip-end').value = c.end;
  $('clip-span').textContent = span(c);
  const dur = document.querySelector(`[data-clip="${CSS.escape(c.id)}"] .dur`);
  if (dur) dur.textContent = `${(c.end - c.start).toFixed(0)} s`;
  seek(k === 'start' ? c.start : c.end - .1);
}
function exportClip() {
  const c = S.clip, M = S.D.meta, i0 = frameAt(c.start), i1 = frameAt(c.end), F = S.F, r = v => Math.round(v * 100) / 100;
  const cols = ['t', 'ball_x', 'ball_y', 'carrier', 'poss', ...Array.from({ length: 22 }, (_, k) => [`x${k}`, `y${k}`]).flat(), ...Array.from({ length: 22 }, (_, k) => `head${k}`)];
  const frames = [];
  for (let i = i0; i <= i1; i++) { const o = i * 72; frames.push([...Array.from(F.subarray(o, o + 49), r), ...Array.from(F.subarray(o + 50, o + 72), r)]); }
  const squad = side => [...M[side].players, ...M[side].bench].map(p => ({ id: p.id, name: p.name, number: p.number, role: p.role }));
  const out = {
    format: 'matchday-newsroom/clip@1', title: c.title, synthetic: true,
    match: { id: S.id, home: M.home.name, away: M.away.name, final_score: M.score },
    range: { start: c.start, end: c.end, clock_start: clockAt(c.start), clock_end: clockAt(c.end) },
    squads: { home: squad('home'), away: squad('away') }, slots_at_start: Array.from({ length: 22 }, (_, k) => (who(k, c.start) || {}).id),
    events: S.D.events.filter(e => e.t >= c.start && e.t <= c.end),
    commentary: Object.fromEntries(Object.entries(S.D.newsroom).map(([v, items]) => [v, items.filter(it => it.approved && it.moment.t >= c.start && it.moment.t <= c.end).map(it => ({ t: it.moment.t, clock: it.moment.clock, line: it.line }))])),
    tracking: { hz: Math.round(1 / S.dt), columns: cols, units: 'metres from the top-left corner, 105 x 68 pitch; heading in radians', frames },
  };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
  a.download = `${S.id}_${c.title.replace(/[^\w]+/g, '_').replace(/^_|_$/g, '')}.json`;
  a.click(); URL.revokeObjectURL(a.href);
  toast(`Exported ${frames.length} frames and ${out.events.length} events.`);
}

async function exportPack() {
  const voice = VOICE[S.persona], items = (S.D.newsroom[voice] || []).filter(it => it.approved), lang = S.profile.language;
  const recs = (S.D.recaps || []).filter(r => r.approved && r.profile.persona === (S.persona === 'analyst' ? 'analyst' : 'fan'));  // held reports are not published
  let texts = [...items.map(it => it.line), ...recs.map(r => r.answer || '')];
  if (lang !== 'English') {  // the whole pack in one language: translate what is not cached yet, in batches
    for (let i = 0; i < texts.length; i += 20) { texts.slice(i, i + 20).forEach(t => trQueue.add(t)); await flushTr(); }
    texts = texts.map(t => (TR[lang] || {})[t] || t);
  }
  const out = {
    format: 'matchday-newsroom/broadcast@1', synthetic: true, match: { id: S.id, home: S.D.meta.home.name, away: S.D.meta.away.name, final_score: S.D.meta.score },
    language: lang, persona: S.persona,
    overlays: items.map((it, i) => ({ t_in: it.overlay.t_in, t_out: it.overlay.t_out, clock: it.moment.clock, kind: it.moment.type, text: texts[i], fact_checked: true })),
    talking_points: recs.map((r, i) => ({ when: r.kind, depth: r.profile.depth, text: texts[items.length + i] })),
    clips: S.D.clips.map(c => ({ title: c.title, start: c.start, end: c.end, kind: c.kind })),
  };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' }));
  a.download = `${S.id}_broadcast_${lang}.json`; a.click(); URL.revokeObjectURL(a.href);
  toast(`Exported ${out.overlays.length} overlay cues, ${out.talking_points.length} reports and ${out.clips.length} clips.`);
}

// a shareable card: the moment's line over a snapshot of the pitch at that instant (drawn by the same painter as the replay)
async function shareCard(t, kind, text) {
  const keep = S.t, keepView = S.view;
  S.t = t; S.view = null; draw();
  const W_ = 1200, H_ = 630, c = document.createElement('canvas'); c.width = W_; c.height = H_;
  const x = c.getContext('2d'), M = S.D.meta, [h, a] = scoreAt(t);
  x.fillStyle = css('--bg'); x.fillRect(0, 0, W_, H_);
  x.drawImage(cv, 40, 140, 660, 432);
  S.t = keep; S.view = keepView; draw();
  x.fillStyle = css('--text'); x.font = '700 30px "Barlow Condensed", sans-serif'; x.fillText('MATCHDAY NEWSROOM', 40, 64);
  x.fillStyle = css('--text-3'); x.font = '600 18px "Barlow Condensed", sans-serif'; x.fillText('SYNTHETIC MATCH · FICTIONAL CLUBS', 40, 92);
  x.fillStyle = css('--text'); x.font = '700 56px "Barlow Condensed", sans-serif'; x.textAlign = 'right';
  x.fillText(`${M.home.code} ${h}–${a} ${M.away.code}`, W_ - 40, 72); x.textAlign = 'left';
  x.fillStyle = css('--accent'); x.font = '700 24px "Barlow Condensed", sans-serif'; x.fillText(`${kind.replace(/_/g, ' ').toUpperCase()} · ${clockAt(t)}`, 730, 170);
  x.fillStyle = css('--text'); x.font = '400 26px "Segoe UI", system-ui, sans-serif';
  let y = 214, line = '';
  for (const w of text.split(/\s+/)) {  // wrap into the right-hand column
    if (x.measureText(line + w).width > 430) { x.fillText(line, 730, y); y += 36; line = ''; if (y > 560) { line = '…'; break; } }
    line += w + ' ';
  }
  x.fillText(line, 730, y);
  const blob = await new Promise(r => c.toBlob(r, 'image/png')), name = `${S.id}_${clockAt(t).replace(/\W/g, '')}.png`;
  const file = new File([blob], name, { type: 'image/png' });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: `${M.home.name} ${h}–${a} ${M.away.name}` }); return; } catch { /* cancelled: fall back to a download */ }
  }
  const aEl = document.createElement('a'); aEl.href = URL.createObjectURL(blob); aEl.download = name; aEl.click(); URL.revokeObjectURL(aEl.href);
  toast('Card saved.');
}

// spoken commentary: each lower-third line read out as it appears, in the reader's language (Azure AI Speech via the server)
let voiceOn = false, speaking = null;
async function sayLine(text) {
  if (!voiceOn || !text) return;
  try {
    const r = await fetch('api/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, language: S.profile.language, persona: S.persona }) });
    if (!r.ok) { voiceOn = false; $('voice').setAttribute('aria-pressed', 'false'); return toast('Voice needs the newsroom server with Azure AI Speech.'); }
    const url = URL.createObjectURL(await r.blob());
    if (speaking) speaking.pause();
    speaking = new Audio(url); speaking.play();
  } catch { /* no server: stay quiet */ }
}

let toastTimer;
function toast(msg) { const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 2600); }
function setTabs() {
  for (const b of $('tabs').children) { b.setAttribute('aria-selected', b.dataset.tab === S.tab); b.textContent = tr(b.dataset.tab); }  // tab names in the reader's language
  if (S.tab === 'What if') fetch('api/warm', { method: 'POST' }).catch(() => {});  // the simulation workers start scaling out while the viewer sets up the change
}

// ---------------------------------------------------------------- events
$('persona').onclick = e => { const b = e.target.closest('button'); if (b) setPersona(b.dataset.p); };
$('tabs').onclick = e => { const b = e.target.closest('button'); if (b) { S.tab = b.dataset.tab; setTabs(); render(true); if (document.body.classList.contains('panel-off')) panelOpen(true); } };
// the side panel, like an IDE's side bar: drag its edge to resize it, collapse it to give the replay the whole width (remembered on this device)
const PANEL_KEY = 'mn.panel', layoutEl = document.querySelector('.layout');
const panelSave = v => { try { localStorage.setItem(PANEL_KEY, JSON.stringify(v)); } catch (_) { /* storage blocked: the panel still works */ } };
const panelPrefs = (() => { try { return JSON.parse(localStorage.getItem(PANEL_KEY)) || {}; } catch (_) { return {}; } })();
function panelWidth(w) { w = Math.round(Math.max(300, Math.min(w, innerWidth * .6))); layoutEl.style.setProperty('--panel-w', w + 'px'); return w; }
function panelOpen(on) {
  document.body.classList.toggle('panel-off', !on);
  $('panel-toggle').setAttribute('aria-expanded', String(on)); $('panel-toggle').title = on ? 'Hide the panel' : 'Show the panel';
  panelSave({ ...panelPrefs, off: !on }); panelPrefs.off = !on;
}
if (panelPrefs.w) panelWidth(panelPrefs.w);
if (panelPrefs.off) panelOpen(false);
$('panel-toggle').onclick = () => panelOpen(document.body.classList.contains('panel-off'));
$('sash').onpointerdown = e => {
  if (document.body.classList.contains('panel-off')) return;
  e.preventDefault(); $('sash').setPointerCapture(e.pointerId); document.body.classList.add('resizing');
  const move = ev => { panelPrefs.w = panelWidth(innerWidth - ev.clientX); };
  const up = () => { $('sash').onpointermove = $('sash').onpointerup = null; document.body.classList.remove('resizing'); panelSave(panelPrefs); };
  $('sash').onpointermove = move; $('sash').onpointerup = up;
};
$('sash').ondblclick = () => { panelPrefs.w = panelWidth(440); panelSave(panelPrefs); };
$('match').onchange = e => load(e.target.value);
$('play').onclick = () => setPlaying(!S.playing);
// the next key moment, from its build-up (6 s before), playing: a moment whose build-up has already started counts as passed, so repeated presses move on
$('next').onclick = () => { const e = S.D.events.find(e => e.t - 6 > S.t + 1 && KEY(e)); if (e) { seek(e.t - 6); setPlaying(true); } };
$('seek').oninput = e => { S.reel = null; S.bd = null; seek(+e.target.value); };
$('bd-next').onclick = bdNext;
$('bd-exit').onclick = () => { S.bd = null; setPlaying(false); };
const tipEl = $('pitch-tip');
cv.addEventListener('mousemove', e => {
  const h = hitAt(e);
  tipEl.hidden = !h; cv.style.cursor = h && h.go ? 'pointer' : '';
  if (!h) return;
  const w = cv.parentElement.getBoundingClientRect();
  tipEl.textContent = h.tip;
  tipEl.style.left = Math.min(e.clientX - w.left + 14, w.width - tipEl.offsetWidth - 8) + 'px'; tipEl.style.top = (e.clientY - w.top + 14) + 'px';
});
cv.addEventListener('mouseleave', () => { tipEl.hidden = true; });
$('reel').addEventListener('click', e => { if (e.target.id === 'hide-view') { S.view = null; render(true); } });
document.addEventListener('keydown', e => {
  if (e.target.matches('input[type=text], select')) return;
  if (e.key === ' ') { e.preventDefault(); setPlaying(!S.playing); }
  if (e.key === 'n') $('next').click();
  if (e.key === 'Escape') { S.view = null; S.reel = null; S.bd = null; }
  if (e.key === 'ArrowRight' && e.target.type !== 'range') seek(S.t + 5);
  if (e.key === 'ArrowLeft' && e.target.type !== 'range') seek(S.t - 5);
});
$('panel-body').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  const d = b.dataset;
  if (d.bd !== undefined) startBreakdown(S.D.breakdowns[+d.bd]);
  else if (d.seek) { seek(+d.seek); setPlaying(true); }
  else if (d.player !== undefined) {
    S.player = d.player || null; S.group = 'All';
    if (S.player && !['Players'].includes(S.tab)) { if (!TABS[S.persona].includes('Players')) setPersona('scout'); S.tab = 'Players'; setTabs(); }  // from the metric card
    render(true);
  }
  else if (d.group) { S.group = d.group; render(true); }
  else if (d.clipEvent) clipFromEvent(d.clipEvent);
  else if (d.clip) openClip(allClips().find(c => c.id === d.clip));
  else if (d.nudge) { const [k, s] = d.nudge.split(':'); updateClip(k, S.clip[k] + +s); }
  else if (d.act === 'preview') { seek(S.clip.start); setPlaying(true); }
  else if (d.act === 'export') exportClip();
  else if (d.act === 'pack') exportPack();
  else if (d.act === 'delete') { S.custom = S.custom.filter(c => c.id !== S.clip.id); saveCustom(); S.clip = null; render(true); }
  else if (d.act === 'close') { S.clip = null; render(true); }
  else if (d.bmode) { S.board.mode = d.bmode; render(true); }
  else if (d.bteam !== undefined) { S.board.team = +d.bteam; render(true); }
  else if (d.bphase) { S.board.phase = d.bphase; render(true); }
  else if (d.ansview) {
    const [ai, vi] = d.ansview.split(':').map(Number), v = S.asks[ai].r.views[vi];
    if (v.kind === 'move') { S.view = null; S.reel = { name: 'The move', who: v.title.replace('The move: ', ''), segs: [{ start: Math.max(0, v.start - 2), end: v.end + 3, clock: '' }], i: 0 }; seek(v.start - 2); setPlaying(true); }
    else { S.view = v; setPlaying(false); toast('Shown on the pitch. Press Esc or Hide to go back to the live picture.'); }
    render(true);
  }
  else if (d.share) {
    const [st, kind] = d.share.split('|'), li = b.closest('li');
    shareCard(+st, kind, li.querySelector('p').textContent);
  }
  else if (d.witeam !== undefined) { S.wi = { team: +d.witeam, change: {}, formation: '', busy: false, res: null, err: '' }; render(true); }
  else if (d.act === 'highlights') playHighlights();
  else if (d.act === 'wi-run') runWhatIf();
  else if (d.act === 'bench') runBench();
  else if (d.act === 'bench-apply') { const rc = S.bench.r.recommendation; S.wi.change = Object.fromEntries(Object.entries(rc).filter(([k]) => k !== 'formation')); S.wi.formation = rc.formation || ''; render(true); }
  else if (d.wiundo) { delete S.wi.change[d.wiundo]; render(true); }
  else if (d.act === 'wi-reset') { S.wi = { team: S.wi.team, change: {}, formation: '', busy: false, res: null, err: '' }; render(true); }
  else if (d.act === 'wi-explain') {
    const w = S.wi, r = w.res, parts = Object.entries(w.change).map(([k, v]) => `${k.replace('_', ' ')} ${Math.round(v * 100)}%`).concat(w.formation ? [`formation ${w.formation}`] : []);
    S.tab = 'Ask'; setTabs();
    askNewsroom(`What if ${r.team_name} had changed to ${parts.join(', ')} from ${r.at.clock}? Run what_if and explain what the simulations show and why.`);
  }
  else if (d.reel) playReel(S.player, d.reel);
  else if (d.report) { const nm = S.info[d.report].name; S.tab = 'Ask'; setTabs(); askNewsroom(`Write a scout report on ${nm}: strengths, weaknesses and how he compares with his position, with the evidence.`); }
  else if (d.hismatch) { const nm = S.info[d.hismatch].name; S.tab = 'Ask'; setTabs(); askNewsroom(`Tell the story of ${nm}'s match so far: his key moments, how he played and his rating.`); }
  else if (d.act === 'ask') askNewsroom($('ask-q').value);
  else if (d.ask) askNewsroom(d.ask);
  else if (d.follow !== undefined) {
    const on = S.profile.playerId !== d.follow;
    Object.assign(S.profile, on ? { playerId: d.follow, player: S.info[d.follow].name } : { playerId: '', player: '' });
    saveProfile(); render(true);
    toast(on ? `Following ${S.info[d.follow].name}: highlighted on the pitch and at the centre of your answers.` : 'Not following anyone.');
  }
});
$('panel-body').addEventListener('input', e => {  // the readout follows the slider while it is dragged; the change is taken on release
  const k = e.target.dataset.wi; if (!k) return;
  const v = e.target.closest('.row').querySelector('.wi-val'), was = Math.round(parseFloat(e.target.parentElement.style.getPropertyValue('--set')));
  v.classList.toggle('chg', Math.round(e.target.value * 100) !== was); v.textContent = Math.round(e.target.value * 100) !== was ? `${was} → ${Math.round(e.target.value * 100)}` : String(was);
});
$('panel-body').addEventListener('change', e => {
  if (e.target.dataset.wi) { S.wi.change[e.target.dataset.wi] = +e.target.value; render(true); }
  if (e.target.id === 'wi-form') { S.wi.formation = e.target.value; render(true); }
  if (e.target.id === 'p-depth') { S.profile.depth = e.target.value; saveProfile(); }
});
$('panel-body').addEventListener('keydown', e => { if (e.target.id === 'ask-q' && e.key === 'Enter') askNewsroom(e.target.value); });
$('club').onchange = e => { S.profile.club = e.target.value; saveProfile(); render(true); };
function setUnits(u) {
  S.profile.units = u; saveProfile();
  const [len, spd, dist] = u.split(','); $('u-len').value = len; $('u-spd').value = spd; $('u-dist').value = dist;
  for (const b of $('u-preset').children) b.setAttribute('aria-selected', b.dataset.u === u);
  $('lt-line').dataset.id = ''; render(true);
}
$('u-preset').onclick = e => { const b = e.target.closest('button'); if (b) setUnits(b.dataset.u); };
for (const id of ['u-len', 'u-spd', 'u-dist']) $(id).onchange = () => setUnits([$('u-len').value, $('u-spd').value, $('u-dist').value].join(','));
setUnits(S.profile.units || 'm,kmh,km');
// live run: the server streams the match from here on its own clock and the newsroom covers each moment as it arrives (GET /api/live)
const liveKey = m => `${m.t}|${m.type}`;
function stopLive(keep) {
  if (!S.live) return;
  S.live.src.close();
  if (!keep) S.live = null;
  $('live').setAttribute('aria-pressed', 'false'); S.liveVer = (S.liveVer || 0) + 1; render(true);
}
function startLive() {
  const speed = Math.max(1, +$('speed').value), q = new URLSearchParams({ match: S.id, persona: S.persona, from: S.t.toFixed(1), speed });
  const src = new EventSource('api/live?' + q), L = S.live = { src, items: new Map(), started: false };
  const bump = () => { S.liveVer = (S.liveVer || 0) + 1; };
  const on = (kind, fn) => src.addEventListener(kind, ev => { if (S.live === L) { fn(JSON.parse(ev.data)); bump(); } });
  $('live').setAttribute('aria-pressed', 'true'); S.reel = null; S.clip = null; S.bd = null; setPlaying(false); toast('Connecting to the newsroom…');
  on('start', d => { L.started = true; S.t = d.t; setPlaying(true); toast(`Live from ${clockAt(d.t)} at ${d.speed}×: ${d.moments} moments to cover. Commentary is written as they happen.`); });
  on('clock', d => { if (Math.abs(S.t - d.t) > .5) S.t = d.t; if (!S.playing) setPlaying(true); });
  on('moment', m => L.items.set(liveKey(m), { moment: m, pending: 'queued', approved: false }));
  on('drafting', d => { const it = L.items.get(liveKey(d.moment)); if (it) it.pending = 'drafting'; });
  on('published', it => L.items.set(liveKey(it.moment), it));
  on('end', () => { L.done = true; stopLive(true); toast('Live run finished. The feed shows what the newsroom wrote live.'); });
  src.onerror = () => {  // no reconnect: that would start a second run from the same minute
    if (S.live !== L || L.done) return;
    L.done = true; stopLive(L.started);
    toast(L.started ? 'Live connection dropped; the feed keeps what arrived.' : 'Live run needs the newsroom server, runs one at a time and has a daily allowance: another may be on. Try again shortly.');
  };
  render(true);
}
$('live').onclick = () => {  // start; stop (the live feed stays); then back to the stored feed
  if (!S.live) return startLive();
  if (S.live.src.readyState !== EventSource.CLOSED) { stopLive(true); return toast('Live run stopped. Click again for the stored feed.'); }
  stopLive(); toast('Back to the stored newsroom feed.');
};
$('v3d').onclick = toggle3d;
$('cam3d').onchange = e => { if (window.View3D) View3D.setMode(e.target.value); };
$('voice').onclick = () => { voiceOn = !voiceOn; $('voice').setAttribute('aria-pressed', String(voiceOn)); if (!voiceOn && speaking) speaking.pause(); toast(voiceOn ? 'Commentary will be read out as the lines appear.' : 'Voice off.'); };
$('lang').innerHTML = LANGS.map(l => `<option${l === S.profile.language ? ' selected' : ''}>${l}</option>`).join('');
$('metric').innerHTML = Object.entries(METRICS).map(([k, l]) => `<option value="${k}"${k === S.profile.metric ? ' selected' : ''}>${l}</option>`).join('');
$('metric').onchange = e => { S.profile.metric = e.target.value; saveProfile(); render(true); };
$('lang').onchange = e => { S.profile.language = e.target.value; saveProfile(); $('lt-line').dataset.id = ''; setTabs(); render(true); };
$('panel-body').addEventListener('input', e => {
  if (e.target.id === 'clip-start' || e.target.id === 'clip-end') { setPlaying(false); updateClip(e.target.id.slice(5), +e.target.value); }
  if (e.target.id === 'ask-q') S.askDraft = e.target.value;
  if (e.target.id === 'clip-title') { S.clip.title = e.target.value; const s = S.custom.find(x => x.id === S.clip.id); if (s) { s.title = e.target.value; saveCustom(); } }
});
cv.addEventListener('click', e => {  // a drawn event or link jumps to it; otherwise click a player on the pitch to open his actions
  const h = hitAt(e);
  if (h && h.go) return h.go();
  if (!TABS[S.persona].includes('Players')) return;
  const r = cv.getBoundingClientRect(), mx = (e.clientX - r.left) * cv.width / r.width, my = (e.clientY - r.top) * cv.height / r.height, f = frame(S.t);
  let best = -1, bd = 16;
  for (let k = 0; k < 22; k++) { const d = Math.hypot(px(f[5 + 2 * k]) - mx, py(f[6 + 2 * k]) - my); if (d < bd) { bd = d; best = k; } }
  if (best >= 0) { S.player = who(best, S.t).id; S.group = 'All'; S.tab = 'Players'; setTabs(); render(true); }
});

boot().catch(err => { $('loading').textContent = 'Could not load the match data. Run scripts/build_site.py first. (' + err.message + ')'; });
requestAnimationFrame(tick);
document.addEventListener('click', e => { const st = $('settings'); if (st.open && !st.contains(e.target)) st.open = false; });  // the settings flyout closes like a menu
