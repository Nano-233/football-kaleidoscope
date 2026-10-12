// Match centre: plays back a built match (scripts/build_site.py) with the newsroom feed, team stats, a scout view and clips.
// No framework: one state object, a canvas for the pitch, and the side panel re-rendered from state when something it shows changes.
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const L = 105, W = 68, cv = $('pitch'), g = cv.getContext('2d'), PAD = 26;
const SX = (cv.width - 2 * PAD) / L, SY = (cv.height - 2 * PAD) / W, px = x => PAD + x * SX, py = y => PAD + y * SY;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COL = { home: css('--home'), away: css('--away'), accent: css('--accent'), good: css('--good'), bad: css('--bad'), pitch: css('--pitch'), line: css('--pitch-line') };

const TABS = { fan: ['Live', 'Stats', 'Clips'], analyst: ['Feed', 'Stats', 'Clips'], scout: ['Players', 'Clips'] };
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
};

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
  const sel = S.persona === 'scout' && S.player ? slotOf(S.player, t) : -1;
  for (let k = 0; k < 22; k++) {
    const x = f[5 + 2 * k], y = f[6 + 2 * k];
    if (y < -5) continue;  // sent off
    const X = px(x), Y = py(y), home = k < 11, p = who(k, t), dim = sel >= 0 && k !== sel;
    g.globalAlpha = dim ? .45 : 1;
    g.beginPath(); g.arc(X, Y, 9, 0, 7); g.fillStyle = home ? COL.home : COL.away; g.fill();
    if (p && p.role === 'GK') { g.lineWidth = 2; g.strokeStyle = '#0c0d0e'; g.stroke(); }
    g.fillStyle = '#0c0d0e'; g.font = '600 10px "Segoe UI", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(p ? p.number : '', X, Y + .5);
    if (k === f[3]) { g.beginPath(); g.arc(X, Y, 13, 0, 7); g.strokeStyle = 'rgba(255,255,255,.8)'; g.lineWidth = 1.5; g.stroke(); }
    if (k === sel) { g.beginPath(); g.arc(X, Y, 15, 0, 7); g.strokeStyle = COL.accent; g.lineWidth = 3; g.stroke(); }
  }
  g.globalAlpha = 1;
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
    $('lt-line').dataset.id = item.moment.id; $('lt-line').textContent = item.line;
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
  const minute = Math.floor(S.t / 60), shown = feed().filter(it => it.moment.t <= S.t).length;
  const key = [S.persona, S.tab, S.tab === 'Clips' ? '' : minute, shown, S.player, S.group, S.clip && S.clip.id, S.custom.length].join('|');
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
};

function feedView(mode) {
  const items = feed().filter(it => it.moment.t <= S.t).reverse();
  if (!items.length) return `<div class="empty">The newsroom files its first story when something happens.</div>`;
  return `<ul class="feed ${mode}">` + items.map(it => {
    if (!it.approved && mode === 'fan') return '';
    const badge = !it.approved ? `<span class="badge warn">Held by fact-checker</span>`
      : it.fallback ? `<span class="badge warn" title="Every agent draft was held, so a plain line built from the facts went out">Drafts held · plain line</span>`
      : it.rewrites ? `<span class="badge ok">Fact-checked · rewritten ${it.rewrites}×</span>` : `<span class="badge ok">Fact-checked</span>`;
    return `<li><div class="meta"><span class="t">${esc(it.moment.clock)}</span><span class="kind">${esc(it.moment.type.replace(/_/g, ' '))}</span>${mode === 'analyst' ? badge : ''}
      <button class="jump" data-seek="${it.moment.t - 8}">Watch</button></div>
      <p${it.approved ? '' : ' class="rejected"'}>${esc(it.line)}</p>${mode === 'analyst' ? trail(it) : ''}</li>`;
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
    <div class="label">Source</div><div class="mono">Computed from the event stream by analytics/stats.py; the newsroom may only quote these numbers.</div>`;
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
  return `<button class="back" data-player="">← All players</button>
    <div class="player-head"><span class="big-num">${p.number}</span><div><h2>${esc(p.name)}</h2>
      <div class="sub">${esc(S.D.meta[side].name)} · ${esc(p.label || p.role)}${(p.traits || []).length ? ' · ' + esc(p.traits.map(x => x.replace(/_/g, ' ')).join(', ')) : ''}</div>
      <div class="sub">${s.minutes ?? 0} minutes played</div></div><span class="rating" title="Full-match rating">${s.rating ?? '–'}</span></div>
    <div class="label">Full match</div><div class="kv">${kv}</div>
    <div class="label">Actions (${acts.length})</div><div class="chips">${chips}</div>
    <ul class="actions" style="margin-top:10px">${rows || '<li><span></span><span class="empty">No actions in this group.</span></li>'}</ul>
    ${S.group === 'All' ? '<div class="mono" style="margin-top:8px">Pressures are under Defending.</div>' : ''}`;
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
    <div class="mono" style="margin-top:10px">Clips export as JSON: the events and tracking frames inside the range, ready for an overlay or edit tool.</div>`;
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
  else if (d.act === 'delete') { S.custom = S.custom.filter(c => c.id !== S.clip.id); saveCustom(); S.clip = null; render(true); }
  else if (d.act === 'close') { S.clip = null; render(true); }
});
$('panel-body').addEventListener('input', e => {
  if (e.target.id === 'clip-start' || e.target.id === 'clip-end') { setPlaying(false); updateClip(e.target.id.slice(5), +e.target.value); }
  if (e.target.id === 'clip-title') { S.clip.title = e.target.value; const s = S.custom.find(x => x.id === S.clip.id); if (s) { s.title = e.target.value; saveCustom(); } }
});
cv.addEventListener('click', e => {  // scout: click a player on the pitch to open his actions
  if (S.persona !== 'scout') return;
  const r = cv.getBoundingClientRect(), mx = (e.clientX - r.left) * cv.width / r.width, my = (e.clientY - r.top) * cv.height / r.height, f = frame(S.t);
  let best = -1, bd = 16;
  for (let k = 0; k < 22; k++) { const d = Math.hypot(px(f[5 + 2 * k]) - mx, py(f[6 + 2 * k]) - my); if (d < bd) { bd = d; best = k; } }
  if (best >= 0) { S.player = who(best, S.t).id; S.group = 'All'; S.tab = 'Players'; setTabs(); render(true); }
});

boot().catch(err => { $('loading').textContent = 'Could not load the match data. Run scripts/build_site.py first. (' + err.message + ')'; });
requestAnimationFrame(tick);
