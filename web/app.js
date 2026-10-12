// Match centre: plays back a built match (scripts/build_site.py) with the newsroom feed, team stats, a scout view and clips.
// No framework: one state object, a canvas for the pitch, and the side panel re-rendered from state when something it shows changes.
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const L = 105, W = 68, cv = $('pitch'), g = cv.getContext('2d'), PAD = 26;
const SX = (cv.width - 2 * PAD) / L, SY = (cv.height - 2 * PAD) / W, px = x => PAD + x * SX, py = y => PAD + y * SY;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COL = { home: css('--home'), away: css('--away'), accent: css('--accent'), good: css('--good'), bad: css('--bad'), pitch: css('--pitch'), line: css('--pitch-line') };

const TABS = { fan: ['Live', 'Ask', 'Recap', 'Players', 'Stats', 'Clips'], analyst: ['Feed', 'Board', 'Ask', 'Recap', 'Stats', 'Clips'], scout: ['Players', 'Ask', 'Clips'] };
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
const label = ty => LABEL[ty] || ty.charAt(0).toUpperCase() + ty.slice(1).replace(/_/g, ' ');
const STAT_ROWS = [  // key, label, format, lower-is-better
  ['goals', 'Goals'], ['xg', 'Expected goals (xG)', v => v.toFixed(2)], ['shots', 'Shots'], ['shots_on_target', 'On target'], ['big_chances', 'Big chances'],
  ['passes', 'Passes'], ['pass_accuracy', 'Pass accuracy', v => Math.round(v * 100) + '%'], ['progressive_passes', 'Progressive passes'],
  ['final_third_passes', 'Final-third passes'], ['ppda', 'PPDA (lower = harder press)', v => v.toFixed(1), true], ['possession_gains', 'Possession gains'],
  ['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['take_on_rate', 'Take-on success', v => Math.round(v * 100) + '%'],
  ['corners', 'Corners'], ['fouls', 'Fouls', null, true], ['yellow_cards', 'Yellow cards', null, true], ['red_cards', 'Red cards', null, true],
];
const FAN_ROWS = ['goals', 'xg', 'shots', 'shots_on_target', 'corners', 'yellow_cards'];

const S = {
  D: null, F: null, n: 0, dt: .2, id: null, t: 0, playing: false, last: 0,
  persona: 'fan', tab: 'Live', player: null, group: 'All', clip: null, custom: [], panelKey: '', info: {}, slots: [],
  profile: loadProfile(), asks: [], askVer: 0, askDraft: '', board: { mode: 'Shape', team: 0, phase: 'in_possession' },
};
const SUGGEST = {
  fan: ["Who's been the best player so far?", 'Why is the game going this way?', 'Explain the last goal', 'How is my player doing?'],
  analyst: ['How did each team set up in and out of possession?', 'Where did the press win the ball back?', 'What changed after the substitutions?', 'Why did they concede?'],
  scout: ['Report on the highest-rated player', 'Who won their duels, and how?', 'Compare the two centre-forwards'],
};
const LANGS = ['English', 'Español', 'Français', 'Deutsch', 'Português', 'Italiano', '中文', '日本語', 'العربية'];
function loadProfile() {
  const base = { depth: 'standard', language: 'English', club: '', player: '', playerId: '' };
  try { return Object.assign(base, JSON.parse(localStorage.getItem('mn-profile'))); } catch { return base; }
}
// published lines in the reader's language: fetched in small batches from the localizer, the original shown until it arrives
const TR = {}, trQueue = new Set();
let trTimer = null;
function tr(text) {
  const lang = S.profile.language;
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
    S.trVer = (S.trVer || 0) + 1; $('lt-line').dataset.id = ''; render(true);
  } catch { /* no server: the lines stay in English */ }
}
function saveProfile() { try { localStorage.setItem('mn-profile', JSON.stringify(S.profile)); } catch { /* storage off: the profile lasts for this visit */ } }

// ---------------------------------------------------------------- data
async function boot() {
  const q = new URLSearchParams(location.search);
  S.persona = TABS[q.get('p')] ? q.get('p') : 'fan';
  S.tab = TABS[S.persona][0];
  const index = await fetch('data/index.json').then(r => r.json());
  $('match').innerHTML = index.map(m => `<option value="${esc(m.id)}">${esc(m.home)} ${m.score[0]}–${m.score[1]} ${esc(m.away)}</option>`).join('');
  const want = index.some(m => m.id === q.get('m')) ? q.get('m') : index[0].id;
  $('match').value = want;
  setPersona(S.persona);
  await load(want);
}

async function load(id) {
  $('loading').hidden = false; S.playing = false;
  const base = `data/${encodeURIComponent(id)}/`;
  const [D, fb, hb] = await Promise.all([fetch(base + 'match.json').then(r => r.json()), fetch(base + 'frames.bin').then(r => r.arrayBuffer()), fetch(base + 'heads.bin').then(r => r.arrayBuffer())]);
  const q = new Int16Array(fb), h = new Int8Array(hb), n = D.n_frames, F = new Float32Array(n * 72);
  for (let i = 0; i < n; i++) {  // layout of scripts/build_site.py pack_frames: ball xy, carrier, poss, 22 x (x, y) as int16 at 0.05 m; facing int8 at 1/40 rad
    const o = i * 72, k = i * 48;
    F[o] = i * D.meta.dt; F[o + 1] = q[k] / 20; F[o + 2] = q[k + 1] / 20; F[o + 3] = q[k + 2]; F[o + 4] = q[k + 3];
    for (let j = 0; j < 44; j++) F[o + 5 + j] = q[k + 4 + j] / 20;
    for (let j = 0; j < 22; j++) F[o + 50 + j] = h[i * 22 + j] / 40;
  }
  Object.assign(S, { D, F, n, dt: D.meta.dt, id, t: 0, player: null, clip: null, custom: loadCustom(id), panelKey: '' });
  S.info = {};
  for (const side of ['home', 'away']) for (const p of [...D.meta[side].players, ...D.meta[side].bench]) S.info[p.id] = { ...p, team: side === 'home' ? 0 : 1 };
  S.slots = [...D.meta.home.players, ...D.meta.away.players].map(p => p.id);
  S.subs = D.events.filter(e => e.type === 'substitution' || e.type === 'outfield_keeper');
  S.goals = D.events.filter(e => e.type === 'goal' || e.type === 'own_goal');
  S.clockEv = D.events.filter(e => e.clock);
  $('h-code').textContent = D.meta.home.code; $('h-name').textContent = D.meta.home.name;
  $('a-code').textContent = D.meta.away.code; $('a-name').textContent = D.meta.away.name;
  $('seek').max = D.meta.duration;
  $('club').innerHTML = '<option value="">Neutral</option>' + ['home', 'away'].map(k => `<option${S.profile.club === D.meta[k].name ? ' selected' : ''}>${esc(D.meta[k].name)}</option>`).join('');
  if (S.profile.playerId && !S.info[S.profile.playerId]) Object.assign(S.profile, { player: '', playerId: '' });
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
const feed = () => S.D.newsroom[VOICE[S.persona]] || [];
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
  if (boardOn()) drawBoard(t);
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
    }
    g.fillStyle = c; g.beginPath(); g.arc(px(e.x), py(e.y), e.type === 'shot' || e.type === 'goal' ? 5 : 3, 0, 7); g.fill();
  }
  g.globalAlpha = 1;
}

let hudKey = '';
function hud(t) {
  const [h, a] = scoreAt(t), clock = clockAt(t), per = periodAt(t), key = `${h}${a}${clock}${per}`;
  if (key !== hudKey) {
    hudKey = key;
    $('h-score').textContent = h; $('a-score').textContent = a; $('clock').textContent = per === 'Full time' ? 'FT' : clock; $('period').textContent = per;
  }
  if (document.activeElement !== $('seek')) $('seek').value = t;
  const item = feed().filter(it => it.approved && t >= it.overlay.t_in && t < it.overlay.t_out).pop();
  $('overlay').hidden = !item;
  if (item && $('lt-line').dataset.id !== String(item.moment.id)) {
    $('lt-line').dataset.id = item.moment.id; $('lt-line').textContent = tr(item.line);
    $('lt-kind').textContent = `${item.moment.type.replace(/_/g, ' ')} · ${item.moment.clock}`;
  }
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
  if (S.playing && S.D) {
    S.t = Math.min(S.D.meta.duration, S.t + (now - S.last) / 1000 * +$('speed').value);
    if (S.clip && S.t >= S.clip.end) S.t = S.clip.start;  // a selected clip loops
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
  const key = [S.persona, S.tab, S.tab === 'Clips' || S.tab === 'Ask' ? '' : minute, (S.D.recaps || []).filter(r => r.t <= S.t + .5).length, shown, S.player, S.group, S.clip && S.clip.id, S.custom.length, S.askVer, S.profile.playerId, JSON.stringify(S.board), S.trVer, S.profile.language].join('|');
  if (!force && key === S.panelKey) return;
  S.panelKey = key;
  const body = $('panel-body'), keep = body.scrollTop, same = body.dataset.view === S.persona + S.tab + S.player;
  body.innerHTML = VIEWS[S.tab]();
  body.dataset.view = S.persona + S.tab + S.player;
  if (same) body.scrollTop = keep;
}

const VIEWS = {
  Live: () => feedView('fan'),
  Feed: () => feedView('analyst'),
  Stats: () => S.persona === 'fan' ? compareView() : tableView(),
  Players: () => S.player ? playerView() : squadView(),
  Clips: () => clipsView(),
  Ask: () => askView(),
  Recap: () => recapView(),
  Board: () => boardView(),
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
  return out;
}

function feedView(mode) {
  const notes = mode === 'fan' ? playerNotes().map(n => ({ note: true, moment: { t: n.t, clock: n.clock, type: 'your player' }, line: n.line, approved: true })) : [];
  const items = [...feed().filter(it => it.moment.t <= S.t), ...notes].sort((a, b) => a.moment.t - b.moment.t).reverse();
  if (!items.length) return `<div class="empty">The newsroom files its first story when something happens.</div>`;
  return `<ul class="feed ${mode}">` + items.map(it => {
    if (it.note) return `<li class="note"><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(S.profile.player)}</span>
      <button class="jump" data-seek="${it.moment.t - 6}">Watch</button></div><p>${esc(tr(it.line))}</p></li>`;
    if (!it.approved && mode === 'fan') return '';
    const badge = !it.approved ? `<span class="badge warn">Held by fact-checker</span>`
      : it.fallback ? `<span class="badge warn" title="Every agent draft was held, so a plain line built from the facts went out">Drafts held · plain line</span>`
      : it.rewrites ? `<span class="badge ok">Fact-checked · rewritten ${it.rewrites}×</span>` : `<span class="badge ok">Fact-checked</span>`;
    return `<li><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(it.moment.type.replace(/_/g, ' '))}</span>${mode === 'analyst' ? badge : ''}
      <button class="jump" data-seek="${it.moment.t - 8}">Watch</button></div>
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
function compareView() {
  const [h, a] = statsAt(S.t), M = S.D.meta;
  const rows = STAT_ROWS.filter(r => FAN_ROWS.includes(r[0])).map(r => {
    const hv = h[r[0]] ?? 0, av = a[r[0]] ?? 0, tot = hv + av || 1, lead = r[3] ? (hv < av ? 'h' : av < hv ? 'a' : '') : (hv > av ? 'h' : av > hv ? 'a' : '');
    return `<div class="cmp-row"><div class="cmp-head"><span>${fmtStat(r, hv)}</span><span>${r[1]}</span><span>${fmtStat(r, av)}</span></div>
      <div class="cmp-bar"><i class="${lead === 'h' ? 'lead h' : ''}" style="flex:${hv / tot || .001}"></i><i class="${lead === 'a' ? 'lead a' : ''}" style="flex:${av / tot || .001}"></i></div></div>`;
  }).join('');
  return `<div class="label">${esc(M.home.code)} vs ${esc(M.away.code)} · after ${esc(clockAt(S.t))}</div><div class="compare">${rows}</div>`;
}

function tableView() {
  const [h, a] = statsAt(S.t), M = S.D.meta;
  const rows = STAT_ROWS.map(r => {
    const hv = h[r[0]], av = a[r[0]], better = (x, y) => x != null && y != null && (r[3] ? x < y : x > y);
    return `<tr><td>${r[1]}</td><td class="${better(hv, av) ? 'lead' : ''}">${fmtStat(r, hv)}</td><td class="${better(av, hv) ? 'lead' : ''}">${fmtStat(r, av)}</td></tr>`;
  }).join('');
  return `<div class="label">Team statistics · after ${esc(clockAt(S.t))}</div>
    <table class="stats"><thead><tr><th>Metric</th><th>${esc(M.home.code)}</th><th>${esc(M.away.code)}</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="label">Source</div><div class="mono">Computed from the event stream by analytics/stats.py; the newsroom may only quote these numbers.</div>${truthPanel()}`;
}

// what the reader saw against what the engine actually did: the data is synthetic, so the truth is known
function truthPanel() {
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
    const team = side === 'home' ? 0 : 1, starters = M[side].players.map(p => p.id), used = M[side].bench.filter(p => (P[p.id] || {}).minutes > 0).map(p => p.id);
    return [...starters, ...used].map(id => {
      const p = S.info[id], s = P[id] || {};
      return `<li><button data-player="${esc(id)}"><span class="num">${p.number}</span><span>${esc(p.name)}${p.star ? ' <span class="badge">Star</span>' : ''}${starters.includes(id) ? '' : ' <span class="sub">(sub)</span>'}</span>
        <span class="role">${esc(p.label || p.role)}</span><span class="rt">${s.rating ?? '–'}</span></button></li>`;
    }).join('');
  };
  return `<div class="label">${esc(M.home.name)} · ${esc(M.home.formation)}</div><ul class="squad">${list('home')}</ul>
    <div class="label">${esc(M.away.name)} · ${esc(M.away.formation)}</div><ul class="squad">${list('away')}</ul>
    <div class="mono">Ratings are for the full match (analytics/stats.py rating, 1–10). Select a player for every action.</div>`;
}

const ROLE_KV = {
  GK: [['saves', 'Saves'], ['keeper_claims', 'Claims'], ['passes_completed', 'Passes completed'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%'], ['long_passes', 'Long passes'], ['errors', 'Errors']],
  CB: [['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['clearances', 'Clearances'], ['aerial_won', 'Aerials won'], ['progressive_passes', 'Prog. passes'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  FB: [['progressive_passes', 'Prog. passes'], ['crosses', 'Crosses'], ['tackles_won', 'Tackles won'], ['interceptions', 'Interceptions'], ['carry_metres', 'Carry metres', v => Math.round(v)], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  MID: [['passes_completed', 'Passes completed'], ['progressive_passes', 'Prog. passes'], ['key_passes', 'Key passes'], ['tackles_won', 'Tackles won'], ['pressures', 'Pressures'], ['pass_accuracy', 'Pass acc.', v => Math.round(v * 100) + '%']],
  ATT: [['goals', 'Goals'], ['xg', 'xG', v => v.toFixed(2)], ['shots', 'Shots'], ['key_passes', 'Key passes'], ['take_ons_won', 'Take-ons won'], ['progressive_carries', 'Prog. carries']],
};
const kvFor = role => ROLE_KV[role] || ROLE_KV[{ DM: 'MID', CM: 'MID', AM: 'ATT', WG: 'ATT', ST: 'ATT' }[role] || 'MID'];

function playerView() {
  const p = S.info[S.player], s = S.D.players[S.player] || {}, side = p.team ? 'away' : 'home';
  const kv = kvFor(p.role).map(([k, l, f]) => `<div><b>${s[k] == null ? '–' : f ? f(s[k]) : s[k]}</b><span>${l}</span></div>`).join('');
  const types = GROUPS[S.group];
  const acts = S.D.events.filter(e => e.player === S.player && e.type !== 'goal' && (types ? types.includes(e.type) : e.type !== 'pressure'));
  const rows = acts.map(e => {
    const v = verdict(e), out = e.outcome ? `<span class="${v > 0 ? 'ok' : v < 0 ? 'no' : ''}">${esc(e.outcome.replace(/_/g, ' '))}</span>` : '<span></span>';
    return `<li class="${e.t > S.t ? 'future' : ''}"><span class="clk">${esc(e.clock)}</span><span>${label(e.type)}${e.xg ? ` · xG ${e.xg.toFixed(2)}` : ''}</span>${out}
      <span><button class="link" data-seek="${e.t - 3}">Watch</button><button class="link" data-clip-event="${e.id}">Clip</button></span></li>`;
  }).join('');
  const chips = Object.keys(GROUPS).map(gname => `<button class="chip" data-group="${gname}" aria-pressed="${gname === S.group}">${gname}</button>`).join('');
  const following = S.profile.playerId === S.player;
  return `<div class="row-between"><button class="back" data-player="">← All players</button>
    <button class="btn${following ? ' primary' : ''}" data-follow="${esc(S.player)}">${following ? 'Following' : 'Follow'}</button></div>
    <div class="player-head"><span class="big-num">${p.number}</span><div><h2>${esc(p.name)}</h2>
      <div class="sub">${esc(S.D.meta[side].name)} · ${esc(p.label || p.role)}${(p.traits || []).length ? ' · ' + esc(p.traits.map(x => x.replace(/_/g, ' ')).join(', ')) : ''}</div>
      <div class="sub">${s.minutes ?? 0} minutes played</div></div><span class="rating" title="Full-match rating">${s.rating ?? '–'}</span></div>
    <div class="label">Full match</div><div class="kv">${kv}</div>
    <div class="label">Actions (${acts.length})</div><div class="chips">${chips}</div>
    <ul class="actions" style="margin-top:10px">${rows || '<li><span></span><span class="empty">No actions in this group.</span></li>'}</ul>
    ${S.group === 'All' ? '<div class="mono" style="margin-top:8px">Pressures are under Defending.</div>' : ''}`;
}

// ---------------------------------------------------------------- analyst board (the toolkit's numbers, last completed window only)
const boardOn = () => S.persona === 'analyst' && S.tab === 'Board' && S.D && S.D.board && S.board.mode !== 'Momentum';
const toWorld = (team, x, y) => (team ? [L - x, W - y] : [x, y]);
const lastWin = (rows, w) => { const i = Math.floor(S.t / w) - 1; return i >= 0 ? rows[Math.min(i, rows.length - 1)] : null; };
const teamCol = tm => (tm ? COL.away : COL.home);
const num = id => (S.info[id] ? S.info[id].number : '');

function drawBoard(t) {
  const B = S.D.board, tm = S.board.team;
  g.save();
  if (S.board.mode === 'Shape') {
    const row = lastWin(B.shape[tm], B.shape_window), sh = row && row[S.board.phase];
    if (sh) {
      const [lx] = toWorld(tm, sh.line, 0);
      g.setLineDash([6, 5]); g.strokeStyle = teamCol(tm); g.lineWidth = 1.5; g.beginPath(); g.moveTo(px(lx), py(0)); g.lineTo(px(lx), py(W)); g.stroke(); g.setLineDash([]);
      for (const [id, x, y] of sh.positions) {
        const [wx, wy] = toWorld(tm, x, y);
        g.beginPath(); g.arc(px(wx), py(wy), 12, 0, 7); g.fillStyle = 'rgba(12,13,14,.75)'; g.fill(); g.lineWidth = 2.5; g.strokeStyle = teamCol(tm); g.stroke();
        g.fillStyle = '#f1f2f4'; g.font = '600 11px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(num(id), px(wx), py(wy) + .5);
      }
    }
  } else if (S.board.mode === 'Network') {
    const row = lastWin(B.network[tm], B.net_window);
    if (row) {
      const max = Math.max(1, ...row.links.map(l => l[2]));
      for (const [a, b, n] of row.links) {
        if (!row.nodes[a] || !row.nodes[b]) continue;
        const [ax, ay] = toWorld(tm, ...row.nodes[a]), [bx, by] = toWorld(tm, ...row.nodes[b]);
        g.globalAlpha = .25 + .75 * n / max; g.strokeStyle = teamCol(tm); g.lineWidth = 1 + 7 * n / max;
        g.beginPath(); g.moveTo(px(ax), py(ay)); g.lineTo(px(bx), py(by)); g.stroke();
      }
      g.globalAlpha = 1;
      for (const [id, xy] of Object.entries(row.nodes)) {
        const [wx, wy] = toWorld(tm, ...xy);
        g.beginPath(); g.arc(px(wx), py(wy), 11, 0, 7); g.fillStyle = teamCol(tm); g.fill();
        g.fillStyle = '#0c0d0e'; g.font = '600 10px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(num(id), px(wx), py(wy) + .5);
      }
    }
  } else if (S.board.mode === 'Press') {
    for (const [rt, team, x, y] of B.regains) {
      if (rt > t || rt < t - B.net_window || team !== tm) continue;
      g.globalAlpha = .35 + .65 * (1 - (t - rt) / B.net_window);
      g.beginPath(); g.arc(px(x), py(y), 6, 0, 7); g.fillStyle = teamCol(tm); g.fill(); g.strokeStyle = '#0c0d0e'; g.lineWidth = 1; g.stroke();
    }
  }
  g.restore();
}

function momentumSvg() {
  const M = S.D.board.momentum, n = Math.max(1, Math.floor(S.t / M.window_s)), rows = M.series.slice(0, n), w = 368, h = 150, mid = 70;
  const mx = Math.max(.3, ...rows.flatMap(r => r.xg)), bw = w / Math.max(rows.length, 18);
  const bars = rows.map((r, i) => `<rect x="${i * bw + 1}" y="${mid - r.xg[0] / mx * 60}" width="${bw - 2}" height="${r.xg[0] / mx * 60}" fill="var(--home)"/>
    <rect x="${i * bw + 1}" y="${mid}" width="${bw - 2}" height="${r.xg[1] / mx * 60}" fill="var(--away)"/>`).join('');
  const tilt = rows.map((r, i) => `${i * bw + bw / 2},${mid - (r.field_tilt_home - .5) * 120}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="xG by five minutes for both teams, with field tilt">
    <line x1="0" x2="${w}" y1="${mid}" y2="${mid}" stroke="var(--line-strong)"/>${bars}
    <polyline points="${tilt}" fill="none" stroke="var(--accent)" stroke-width="2"/>
    <text x="0" y="12" fill="var(--text-3)" font-size="10">${esc(S.D.meta.home.code)} xG ↑</text><text x="0" y="${h - 4}" fill="var(--text-3)" font-size="10">${esc(S.D.meta.away.code)} xG ↓ · line: field tilt</text></svg>`;
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
       <div class="kv"><div><b>${esc(sh.formation)}</b><span>Shape</span></div><div><b>${sh.line} m</b><span>Defensive line</span></div><div><b>${sh.length} × ${sh.width} m</b><span>Block</span></div></div>
       <div class="mono" style="margin-top:8px">Average positions over the last completed five minutes; the dashed line is the defensive line. The two phases are read separately: a team can line up one way with the ball and another without it.</div>`;
  } else if (b.mode === 'Network') {
    const row = lastWin(B.network[b.team], B.net_window);
    body = !row ? '<div class="empty">The first fifteen minutes are not over yet.</div>' :
      `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · passing network ${clockAt(row.t0)}–${clockAt(row.t1)}</div>
       <table class="stats"><tbody>${row.links.slice(0, 6).map(([a, c, n]) => `<tr><td>${num(a)} ${esc(S.info[a].name)} → ${num(c)} ${esc(S.info[c].name)}</td><td>${n}</td></tr>`).join('')}</tbody></table>
       <div class="mono" style="margin-top:8px">Entries into the final third, left / centre / right: ${row.entries.join(' / ')}</div>`;
  } else if (b.mode === 'Press') {
    const rows = B.pressing[b.team], cur = Math.floor(S.t / B.net_window);
    body = `<div class="label">${esc(M[b.team ? 'away' : 'home'].name)} · pressing by 15 minutes</div>
      <table class="stats"><thead><tr><th>Window</th><th>PPDA</th><th>Height</th><th>Counter-press</th></tr></thead><tbody>${rows.slice(0, cur).map(r => `<tr><td>${esc(r.window.join('–'))}</td><td>${r.ppda ?? '–'}</td><td>${r.action_height_m ?? '–'} m</td><td>${r.counterpress_wins}</td></tr>`).join('') || '<tr><td colspan="4">First window not over yet.</td></tr>'}</tbody></table>
      <div class="mono" style="margin-top:8px">PPDA: opponent passes per defensive action in their build-up (lower = harder press). Dots on the pitch: where the ball was won in the last fifteen minutes.</div>`;
  } else {
    const sp = B.momentum.dominant_spells.filter(x => x.end_t <= S.t);
    body = `<div class="label">Momentum · xG by five minutes</div>${momentumSvg()}
      <div class="label">Spells of dominance</div>${sp.length ? `<table class="stats"><tbody>${sp.map(x => `<tr><td>${esc(x.team)} ${esc(x.start)}–${esc(x.end)}</td><td>xG ${x.xg} v ${x.opponent_xg}</td></tr>`).join('')}</tbody></table>` : '<div class="mono">None yet.</div>'}`;
  }
  return `<div class="chips">${['Shape', 'Network', 'Press', 'Momentum'].map(m => chip('bmode', m, m, b.mode === m)).join('')}</div>
    ${b.mode !== 'Momentum' ? `<div class="chips" style="margin-top:8px">${[0, 1].map(t => chip('bteam', t, M[t ? 'away' : 'home'].code, b.team === t)).join('')}
      ${b.mode === 'Shape' ? chip('bphase', 'in_possession', 'With the ball', b.phase === 'in_possession') + chip('bphase', 'out_of_possession', 'Without the ball', b.phase === 'out_of_possession') : ''}</div>` : ''}
    <div style="margin-top:12px">${body}</div>`;
}

// ---------------------------------------------------------------- recaps (written in advance by the newsroom, shown once their moment has come)
function recapView() {
  const want = S.persona === 'analyst' ? 'analyst' : 'fan';
  const rs = (S.D.recaps || []).filter(r => r.profile.persona === want && r.t <= S.t + .5).reverse();
  if (!rs.length) return '<div class="empty">The half-time report arrives at the break.</div>';
  return '<ul class="feed asks">' + rs.map(r => {
    const title = r.kind === 'half_time' ? 'Half-time report' : 'Full-time report';
    const watch = (r.watch || []).map(w => `<button class="link" data-seek="${w.t - 6}">Watch ${esc(w.clock)} ${esc(label(w.type).toLowerCase())}</button>`).join(' ');
    const steps = r.steps.map(st => `<li><b>${esc(st.agent.replace('_', ' '))}</b> → ${esc(st.tool.replace(/_/g, ' '))}</li>`).join('');
    return `<li><div class="q">${title}</div><div class="meta"><span class="badge ok">Fact-checked${r.rewrites ? ` · rewritten ${r.rewrites}×` : ''}</span></div>
      <p${r.approved ? '' : ' class="rejected"'}>${esc(tr(r.answer || r.held))}</p>${watch ? `<div class="watch">${watch}</div>` : ''}
      <details class="trail"><summary>How the newsroom worked it out (${r.steps.length} steps)</summary><div class="trail-body"><ol class="steps">${steps}</ol></div></details></li>`;
  }).join('') + '</ul>';
}

// ---------------------------------------------------------------- ask the newsroom
function askView() {
  const P = S.profile;
  const sug = SUGGEST[S.persona].map(q => (q === 'How is my player doing?' && P.player ? `How is ${P.player} doing?` : q)).filter(q => q !== 'How is my player doing?');
  const opt = (v, cur, txt) => `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(txt || v)}</option>`;
  const hist = S.asks.slice().reverse().map(a => {
    if (a.pending) return `<li><div class="q">${esc(a.q)}</div><div class="thinking">The newsroom is working on it</div></li>`;
    if (a.error) return `<li><div class="q">${esc(a.q)}</div><div class="empty">${esc(a.error)}</div></li>`;
    const r = a.r;
    const badge = r.approved ? `<span class="badge ok">Fact-checked${r.rewrites ? ` · rewritten ${r.rewrites}×` : ''}</span>` : '<span class="badge warn">Held by fact-checker</span>';
    const steps = r.steps.map(st => `<li><b>${esc(st.agent.replace('_', ' '))}</b> → ${esc(st.tool.replace(/_/g, ' '))}${Object.values(st.args || {}).some(Boolean) ? ` <span class="mono">${esc(Object.values(st.args).filter(Boolean).join(', '))}</span>` : ''}</li>`).join('');
    const watch = (r.watch || []).map(w => `<button class="link" data-seek="${w.t - 6}">Watch ${esc(w.clock)} ${esc(label(w.type).toLowerCase())}</button>`).join(' ');
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
    const res = await fetch('api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match: S.id, question: q, t: Math.max(60, S.t), profile: { persona: S.persona, depth: P.depth, language: P.language, club: P.club, player: P.player } }) });
    if (!res.ok) throw new Error(res.status === 404 || res.status === 405 || res.status === 501 ? NO_SERVER : `The newsroom could not answer (${res.status}).`);
    a.r = await res.json();
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
  return editor + (S.custom.length ? `<div class="label">Your clips</div><ul class="clips">${S.custom.map(li).join('')}</ul>` : '')
    + `<div class="label">Suggested by the newsroom (${S.D.clips.length})</div><ul class="clips">${S.D.clips.map(li).join('')}</ul>
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
  const recs = (S.D.recaps || []).filter(r => r.profile.persona === (S.persona === 'analyst' ? 'analyst' : 'fan'));
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

let toastTimer;
function toast(msg) { const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 2600); }
function setTabs() { for (const b of $('tabs').children) b.setAttribute('aria-selected', b.dataset.tab === S.tab); }

// ---------------------------------------------------------------- events
$('persona').onclick = e => { const b = e.target.closest('button'); if (b) setPersona(b.dataset.p); };
$('tabs').onclick = e => { const b = e.target.closest('button'); if (b) { S.tab = b.dataset.tab; setTabs(); render(true); } };
$('match').onchange = e => load(e.target.value);
$('play').onclick = () => setPlaying(!S.playing);
$('next').onclick = () => { const e = S.D.events.find(e => e.t > S.t + 1 && KEY(e)); if (e) seek(e.t - 6); };
$('seek').oninput = e => seek(+e.target.value);
document.addEventListener('keydown', e => {
  if (e.target.matches('input[type=text], select')) return;
  if (e.key === ' ') { e.preventDefault(); setPlaying(!S.playing); }
  if (e.key === 'n') $('next').click();
  if (e.key === 'ArrowRight' && e.target.type !== 'range') seek(S.t + 5);
  if (e.key === 'ArrowLeft' && e.target.type !== 'range') seek(S.t - 5);
});
$('panel-body').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  const d = b.dataset;
  if (d.seek) { seek(+d.seek); setPlaying(true); }
  else if (d.player !== undefined) { S.player = d.player || null; S.group = 'All'; render(true); }
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
  else if (d.act === 'ask') askNewsroom($('ask-q').value);
  else if (d.ask) askNewsroom(d.ask);
  else if (d.follow !== undefined) {
    const on = S.profile.playerId !== d.follow;
    Object.assign(S.profile, on ? { playerId: d.follow, player: S.info[d.follow].name } : { playerId: '', player: '' });
    saveProfile(); render(true);
    toast(on ? `Following ${S.info[d.follow].name}: highlighted on the pitch and at the centre of your answers.` : 'Not following anyone.');
  }
});
$('panel-body').addEventListener('change', e => {
  if (e.target.id === 'p-depth') { S.profile.depth = e.target.value; saveProfile(); }
});
$('panel-body').addEventListener('keydown', e => { if (e.target.id === 'ask-q' && e.key === 'Enter') askNewsroom(e.target.value); });
$('club').onchange = e => { S.profile.club = e.target.value; saveProfile(); };
$('lang').innerHTML = LANGS.map(l => `<option${l === S.profile.language ? ' selected' : ''}>${l}</option>`).join('');
$('lang').onchange = e => { S.profile.language = e.target.value; saveProfile(); $('lt-line').dataset.id = ''; render(true); };
$('panel-body').addEventListener('input', e => {
  if (e.target.id === 'clip-start' || e.target.id === 'clip-end') { setPlaying(false); updateClip(e.target.id.slice(5), +e.target.value); }
  if (e.target.id === 'ask-q') S.askDraft = e.target.value;
  if (e.target.id === 'clip-title') { S.clip.title = e.target.value; const s = S.custom.find(x => x.id === S.clip.id); if (s) { s.title = e.target.value; saveCustom(); } }
});
cv.addEventListener('click', e => {  // scout: click a player on the pitch to open his actions
  if (!TABS[S.persona].includes('Players')) return;
  const r = cv.getBoundingClientRect(), mx = (e.clientX - r.left) * cv.width / r.width, my = (e.clientY - r.top) * cv.height / r.height, f = frame(S.t);
  let best = -1, bd = 16;
  for (let k = 0; k < 22; k++) { const d = Math.hypot(px(f[5 + 2 * k]) - mx, py(f[6 + 2 * k]) - my); if (d < bd) { bd = d; best = k; } }
  if (best >= 0) { S.player = who(best, S.t).id; S.group = 'All'; S.tab = 'Players'; setTabs(); render(true); }
});

boot().catch(err => { $('loading').textContent = 'Could not load the match data. Run scripts/build_site.py first. (' + err.message + ')'; });
requestAnimationFrame(tick);
