// 3D match view: the same tracking frames the 2D pitch draws (5 Hz, interpolated by app.js), rendered with three.js. Everything is built
// from primitives here: the pitch is drawn to a canvas texture with the real markings, the players are simple figures in their team's
// colours turned to their logged facing, legs and arms swinging with their speed, and the ball flies at the height the engine logged.
// Players are the CC0 Quaternius mannequin (web/assets/player.glb, trimmed to five clips by scripts/trim_glb.py), one cloned rig each,
// idle / walk / jog / sprint blended by the player's real speed; the primitive figures stay as the fallback if it cannot load.
// Exposed as window.View3D for the classic-script app.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

const L = 105, W = 68;
const P = (x, y, z = 0) => new THREE.Vector3(x - L / 2, z, y - W / 2);  // pitch metres -> scene (x along the length, z across, y up)

let renderer, scene, camera, controls, wrap, players = [], ball, ballShadow, mode = 'tv', last = null, lastT = 0, follow = -1, model = null, teamsNow = null;
// locomotion clips and the speed (m/s) each is played at its own rate; weights are blended linearly between neighbours
const GAIT = [['Idle_Loop', 0], ['Walk_Loop', 1.5], ['Jog_Fwd_Loop', 3.8], ['Sprint_Loop', 7.2]];
const FACE = 0;  // the model's own forward is +z; heading 0 points along +x
// the kick, posed on the skeleton over the running clip (the pack has no kick): u runs 0 -> 1 over KICK_S seconds, the foot meets the ball
// at KICK_HIT; thigh swing and knee bend in degrees at each key (backswing, contact, follow-through)
const KICK_S = .55, KICK_HIT = .55, KICK_KEYS = [[0, 0, 0], [.4, -38, 85], [.55, 35, 25], [.75, 60, 5], [1, 0, 0]];
const KICK_SIGN = { thigh: -1, calf: 1 };
const _q = new THREE.Quaternion(), _x = new THREE.Vector3(1, 0, 0);
const camPos = new THREE.Vector3(0, 30, 60), camLook = new THREE.Vector3();

function pitchTexture() {
  const k = 16, M = 6, c = document.createElement('canvas');  // 16 px a metre, 6 m of run-off round the lines
  c.width = (L + 2 * M) * k; c.height = (W + 2 * M) * k;
  const g = c.getContext('2d'), X = x => (x + M) * k, Y = y => (y + M) * k;
  g.fillStyle = '#2f6b33'; g.fillRect(0, 0, c.width, c.height);
  for (let i = 0; i < 18; i++) { g.fillStyle = i % 2 ? '#2f6b33' : '#357439'; g.fillRect(X(i * L / 18), Y(-M), L / 18 * k, (W + 2 * M) * k); }  // mowing stripes
  g.strokeStyle = 'rgba(255,255,255,.92)'; g.lineWidth = .12 * k; g.fillStyle = 'rgba(255,255,255,.92)';
  const rect = (x, y, w, h) => g.strokeRect(X(x), Y(y), w * k, h * k);
  rect(0, 0, L, W);
  g.beginPath(); g.moveTo(X(L / 2), Y(0)); g.lineTo(X(L / 2), Y(W)); g.stroke();
  g.beginPath(); g.arc(X(L / 2), Y(W / 2), 9.15 * k, 0, 7); g.stroke();
  g.beginPath(); g.arc(X(L / 2), Y(W / 2), .25 * k, 0, 7); g.fill();
  for (const s of [0, 1]) {
    rect(s ? L - 16.5 : 0, W / 2 - 20.16, 16.5, 40.32);
    rect(s ? L - 5.5 : 0, W / 2 - 9.16, 5.5, 18.32);
    g.beginPath(); g.arc(X(s ? L - 11 : 11), Y(W / 2), .25 * k, 0, 7); g.fill();
    g.beginPath(); g.arc(X(s ? L - 11 : 11), Y(W / 2), 9.15 * k, s ? Math.PI - .93 : -.93, s ? Math.PI + .93 : .93); g.stroke();
    for (const cy of [0, W]) { g.beginPath(); g.arc(X(s ? L : 0), Y(cy), 1 * k, 0, 7); g.stroke(); }  // corner arcs (the canvas clips the outer part)
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  return { tex: t, size: [L + 2 * M, W + 2 * M] };
}

function goal(xLine, dir) {
  const grp = new THREE.Group(), white = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .4 });
  const post = h => new THREE.Mesh(new THREE.CylinderGeometry(.06, .06, h, 10), white);
  for (const s of [-1, 1]) { const p = post(2.44); p.position.copy(P(xLine, W / 2 + s * 3.66, 1.22)); grp.add(p); }
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(.06, .06, 7.32, 10), white);
  bar.rotation.x = Math.PI / 2; bar.position.copy(P(xLine, W / 2, 2.44)); grp.add(bar);
  const pts = [], d = 2.0, net = (a, b) => pts.push(a, b);  // the net: a wire grid, back and sides and roof
  for (let i = 0; i <= 14; i++) { const zz = -3.66 + i * 7.32 / 14; net(P(xLine + dir * d, W / 2 + zz, 0), P(xLine + dir * d, W / 2 + zz, 2.2)); net(P(xLine, W / 2 + zz, 2.44), P(xLine + dir * d, W / 2 + zz, 2.2)); }
  for (let j = 0; j <= 6; j++) { const hh = j * 2.2 / 6; net(P(xLine + dir * d, W / 2 - 3.66, hh), P(xLine + dir * d, W / 2 + 3.66, hh)); }
  for (const s of [-1, 1]) for (let j = 0; j <= 6; j++) { const hh = j * 2.2 / 6; net(P(xLine, W / 2 + s * 3.66, hh), P(xLine + dir * d, W / 2 + s * 3.66, hh)); }
  grp.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xdddddd, transparent: true, opacity: .55 })));
  return grp;
}

function stands() {  // four stepped stands with a darker crowd band: scale and a sense of place, no detail to distract
  const grp = new THREE.Group(), seats = new THREE.MeshStandardMaterial({ color: 0x2a2e36, roughness: .9 }), crowd = new THREE.MeshStandardMaterial({ color: 0x3b3f4a, roughness: 1 });
  const side = (len, x, z, rotY) => {
    for (let r = 0; r < 4; r++) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(len, 1.6, 4), r % 2 ? crowd : seats);
      const g2 = new THREE.Group(); g2.add(m); m.position.set(0, .8 + r * 1.6, -(r * 4));
      g2.position.set(x, 0, z); g2.rotation.y = rotY; grp.add(g2);
    }
  };
  side(L + 30, 0, -(W / 2 + 10), 0); side(L + 30, 0, W / 2 + 10, Math.PI);
  side(W + 20, -(L / 2 + 12), 0, Math.PI / 2); side(W + 20, L / 2 + 12, 0, -Math.PI / 2);
  return grp;
}

function label(text, color) {
  const c = document.createElement('canvas'); c.width = 128; c.height = 64;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(12,13,14,.75)'; g.beginPath(); g.roundRect(14, 6, 100, 52, 12); g.fill();
  g.fillStyle = color; g.font = '700 38px "Barlow Condensed", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, 64, 34);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
  s.scale.set(1.3, .65, 1); s.renderOrder = 10;
  return s;
}

const blob = (r, o) => {
  const m = new THREE.Mesh(new THREE.CircleGeometry(r, 20), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: o, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; return m;
};

function figure(shirt, shorts) {  // a player built from primitives, about 1.8 m tall, facing +x; hips and shoulders are pivots so the limbs can swing
  const g = new THREE.Group(), mat = c => new THREE.MeshStandardMaterial({ color: c, roughness: .7 });
  const skin = mat(0xc8a07a), sock = mat(shorts), boot = mat(0x111111);
  const torso = new THREE.Mesh(new THREE.BoxGeometry(.26, .62, .46), mat(shirt)); torso.position.y = 1.22; g.add(torso);
  const pants = new THREE.Mesh(new THREE.BoxGeometry(.27, .22, .46), mat(shorts)); pants.position.y = .86; g.add(pants);
  const head = new THREE.Mesh(new THREE.SphereGeometry(.13, 16, 12), skin); head.position.y = 1.68; g.add(head);
  const limb = (len, r, m) => { const piv = new THREE.Group(), l = new THREE.Mesh(new THREE.CylinderGeometry(r, r * .85, len, 8), m); l.position.y = -len / 2; piv.add(l); return piv; };
  const legs = [-1, 1].map(s => { const p = limb(.82, .075, sock); p.position.set(0, .8, s * .12); const b = new THREE.Mesh(new THREE.BoxGeometry(.24, .07, .1), boot); b.position.set(.05, -.84, 0); p.add(b); g.add(p); return p; });
  const arms = [-1, 1].map(s => { const p = limb(.62, .05, skin); p.position.set(0, 1.5, s * .28); g.add(p); return p; });
  g.add(blob(.38, .28));
  return { g, legs, arms, torso, phase: Math.random() * 6 };
}

async function loadModel() {
  try {
    const g = await new GLTFLoader().loadAsync('assets/player.glb');
    // height from the skeleton (a skinned mesh's own box is the unskinned geometry in its armature's units): feet to head bone, the head's
    // top a little above it
    g.scene.updateMatrixWorld(true);
    const ys = []; g.scene.traverse(o => { if (o.isBone) ys.push(new THREE.Vector3().setFromMatrixPosition(o.matrixWorld).y); });
    const h = (Math.max(...ys) - Math.min(...ys)) * 1.07;
    model = { scene: g.scene, clips: g.animations, scale: 1.8 / Math.max(h, .01) };  // its forward is +z (toe ahead of the foot bone)
    if (teamsNow) setTeams(teamsNow);
  } catch (e) { console.warn('3D players: using simple figures', e); }
}

function figureModel(shirt, shorts) {  // the CC0 mannequin, recoloured: its main material for the shirt, the joints for the shorts and socks
  const g = new THREE.Group(), root = SkeletonUtils.clone(model.scene);
  root.scale.setScalar(model.scale); root.rotation.y = Math.PI / 2 + FACE;  // its forward (+z) turned to the group's +x
  let main = null;
  root.traverse(o => {
    if (!o.isMesh) return;
    o.frustumCulled = false;
    o.material = o.material.clone();
    if (/main/i.test(o.material.name)) { o.material.color.copy(shirt); main = o.material; } else o.material.color.copy(shorts);
    o.material.roughness = .75;
  });
  g.add(root, blob(.38, .28));
  const mixer = new THREE.AnimationMixer(root);
  const acts = GAIT.map(([name]) => { const c = model.clips.find(c => c.name === name); const a = mixer.clipAction(c); a.play(); a.setEffectiveWeight(0); return a; });
  acts[0].setEffectiveWeight(1);
  mixer.update(Math.random() * 2);  // not all in step
  return { g, mixer, acts, thigh: root.getObjectByName('thigh_r'), calf: root.getObjectByName('calf_r'), torso: { material: main || new THREE.MeshStandardMaterial(), rotation: {} } };
}

function kick(p, u) {  // swing the right leg through the keys, on top of whatever the mixer posed this frame
  if (!p.thigh || !p.calf) return;
  let a = KICK_KEYS[0], b = KICK_KEYS[KICK_KEYS.length - 1];
  for (let i = 0; i < KICK_KEYS.length - 1; i++) if (u >= KICK_KEYS[i][0] && u <= KICK_KEYS[i + 1][0]) { a = KICK_KEYS[i]; b = KICK_KEYS[i + 1]; break; }
  const w = (u - a[0]) / Math.max(b[0] - a[0], 1e-6), s = .5 - .5 * Math.cos(Math.PI * w);  // eased between keys
  const th = THREE.MathUtils.degToRad(a[1] + (b[1] - a[1]) * s), kn = THREE.MathUtils.degToRad(a[2] + (b[2] - a[2]) * s);
  p.thigh.quaternion.multiply(_q.setFromAxisAngle(_x, th * KICK_SIGN.thigh));
  p.calf.quaternion.multiply(_q.setFromAxisAngle(_x, kn * KICK_SIGN.calf));
}

function gait(p, sp, dt) {  // blend the clips for this speed, each played at the rate that makes its stride match it
  const v = Math.min(sp, 9);
  let i = GAIT.length - 2;
  while (i > 0 && v < GAIT[i][1]) i--;
  const [a, b] = [GAIT[i], GAIT[i + 1]], w = THREE.MathUtils.clamp((v - a[1]) / (b[1] - a[1]), 0, 1);
  p.acts.forEach((act, k) => {
    act.setEffectiveWeight(k === i ? 1 - w : k === i + 1 ? w : 0);
    if (GAIT[k][1] > 0) act.setEffectiveTimeScale(THREE.MathUtils.clamp(v / GAIT[k][1], .6, 1.5));
  });
  p.mixer.update(dt);
}

function init(container) {
  wrap = container;
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.id = 'pitch3d';
  container.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0f1114);
  scene.fog = new THREE.Fog(0x0f1114, 140, 260);
  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x23331f, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(-30, 60, 25); scene.add(sun);
  const { tex, size } = pitchTexture();
  const grass = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[1]), new THREE.MeshStandardMaterial({ map: tex, roughness: 1 }));
  grass.rotation.x = -Math.PI / 2; scene.add(grass);
  const apron = new THREE.Mesh(new THREE.PlaneGeometry(L + 60, W + 50), new THREE.MeshStandardMaterial({ color: 0x1d3a20, roughness: 1 }));
  apron.rotation.x = -Math.PI / 2; apron.position.y = -.02; scene.add(apron);
  scene.add(goal(0, -1), goal(L, 1), stands());
  ball = new THREE.Mesh(new THREE.SphereGeometry(.2, 20, 14), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: .35 }));
  ballShadow = blob(.3, .35); scene.add(ball, ballShadow);
  camera = new THREE.PerspectiveCamera(30, 16 / 9, .5, 600);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enabled = false; controls.maxPolarAngle = Math.PI / 2.1;
  new ResizeObserver(resize).observe(container);
  resize();
  loadModel();
}

function resize() {
  if (!renderer) return;
  const w = wrap.clientWidth, h = Math.min(wrap.clientHeight, w * 720 / 1100);
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + 'px'; renderer.domElement.style.height = h + 'px';
  camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix();
}

// teams: [{shirt, shorts, keeper}, {...}] as CSS colours
function setTeams(teams) {
  teamsNow = teams;
  for (const p of players) scene.remove(p.g);
  players = [];
  for (let k = 0; k < 22; k++) {
    const t = teams[k < 11 ? 0 : 1];
    const f = (model ? figureModel : figure)(new THREE.Color(t.shirt), new THREE.Color(t.shorts));
    f.label = null; f.labelText = ''; f.team = k < 11 ? 0 : 1; f.colors = t;
    scene.add(f.g); players.push(f);
  }
  last = null;
}

// f: the interpolated frame from app.js (1, 2 ball xy; 3 carrier slot; 5 + 2k player k xy; 50 + k facing), z: ball height (m), who(k) -> {number, role}
function update(f, t, z, who, opts = {}) {
  if (!renderer || !players.length) return;
  const dt = last ? Math.max(1e-3, Math.min(.5, t - lastT)) : .2;
  for (let k = 0; k < 22; k++) {
    const p = players[k], x = f[5 + 2 * k], y = f[6 + 2 * k];
    p.g.visible = y > -5;  // sent off
    if (!p.g.visible) continue;
    const info = who(k) || {};
    const keeper = info.role === 'GK';
    if (p.keeper !== keeper) { p.keeper = keeper; p.torso.material.color.set(keeper ? p.colors.keeper : p.colors.shirt); }
    const txt = String(info.number ?? '');
    if (txt !== p.labelText) { if (p.label) p.g.remove(p.label); p.label = label(txt, opts.labelColor || '#f1f2f4'); p.label.position.y = 2.3; p.g.add(p.label); p.labelText = txt; }
    p.g.position.copy(P(x, y));
    p.g.rotation.y = -f[50 + k];
    const sp = last ? Math.hypot(x - last[5 + 2 * k], y - last[6 + 2 * k]) / dt : 0;
    if (p.mixer) {
      gait(p, sp, last ? dt : 0);
      const ku = opts.kicks && opts.kicks.get(k);
      if (ku != null) kick(p, ku);
    }
    else {
      const amp = Math.min(sp / 7, 1);
      p.phase += dt * (2.2 + sp * 1.1);
      const sw = Math.sin(p.phase) * (.15 + .75 * amp);
      p.legs[0].rotation.z = sw; p.legs[1].rotation.z = -sw;
      p.arms[0].rotation.z = -sw * .8; p.arms[1].rotation.z = sw * .8;
      p.torso.rotation.z = -.18 * amp;  // leans into the run
    }
    p.label.material.color.set(k === opts.highlight ? (opts.accent || '#5aa9ff') : '#ffffff');
  }
  ball.position.copy(P(f[1], f[2], .2 + Math.max(0, z || 0)));
  ballShadow.position.copy(P(f[1], f[2], .01));
  ballShadow.scale.setScalar(1 / (1 + (z || 0) * .25));
  last = Float32Array.from(f); lastT = t;
  follow = opts.follow ?? -1;
  aim(f);
  renderer.render(scene, camera);
}

function aim(f) {
  if (mode === 'free') { controls.update(); return; }
  const b = P(f[1], f[2]);
  let pos, look;
  if (mode === 'tv') {  // the main broadcast camera: high on the halfway side, panning with the ball, a little zoom towards the goals
    const x = THREE.MathUtils.clamp(b.x * .85, -L / 2 + 6, L / 2 - 6);
    pos = new THREE.Vector3(x * .92, 15, W / 2 + 26); look = new THREE.Vector3(x, 0, b.z * .5);
  } else if (mode === 'tactical') {  // high behind the middle: both lines and the spaces between them
    pos = new THREE.Vector3(b.x * .5, 78, W / 2 + 26); look = new THREE.Vector3(b.x * .5, 0, 0);
  } else if (mode === 'follow' && follow >= 0 && players[follow] && players[follow].g.visible) {  // behind and above the followed player
    const p = players[follow].g.position, h = -f[50 + follow];
    const back = new THREE.Vector3(-Math.cos(h), 0, Math.sin(h));
    pos = p.clone().addScaledVector(back, 14).add(new THREE.Vector3(0, 7, 0)); look = p.clone().lerp(b, .35);
  } else {  // behind the goal the ball is nearer to
    const s = b.x >= 0 ? 1 : -1;
    pos = new THREE.Vector3(s * (L / 2 + 22), 14, b.z * .3); look = new THREE.Vector3(s * (L / 2 - 20), 0, b.z * .5);
  }
  camPos.lerp(pos, .08); camLook.lerp(look, .12);
  camera.position.copy(camPos); camera.lookAt(camLook);
}

function setMode(m) {
  mode = m;
  controls.enabled = m === 'free';
  if (m === 'free') { controls.target.copy(camLook); camera.position.copy(camPos); }
}

window.View3D = { init, setTeams, update, setMode, get mode() { return mode; }, resize, KICK_S, KICK_HIT };
window.dispatchEvent(new Event('view3d-ready'));
