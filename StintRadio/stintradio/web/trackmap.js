// The track map engine shared by the race page and the overlay: a drawing fetched from /api/track, cars placed by
// lap distance and eased every frame. Needs on the page: $(id), STALE, and the elements mapbox, trackmap, mapNone, mapSrc, mapNext.
// ---- the track map: a drawing fetched when its id changes, cars placed by lap distance and moved every frame -----------
// colours for a dark page and for a light one; the page picks with setMapTheme()
const THEMES = {
  dark:  { casing:"rgba(255,255,255,.11)", line:"rgba(255,255,255,.3)", ref:"rgba(74,168,255,.8)", start:"#fff", me:"#fff", meGlow:"#fff",
           other:"rgba(220,228,240,.7)", halo:"rgba(7,9,13,.85)", trail:"124,108,255", good:"#34e089", warn:"#ffc83d", pit:"rgba(135,148,166,.45)" },
  light: { casing:"rgba(20,30,50,.10)", line:"rgba(20,30,50,.38)", ref:"rgba(25,105,215,.9)", start:"#0b0e14", me:"#0b0e14", meGlow:"rgba(124,108,255,.9)",
           other:"rgba(40,50,70,.7)", halo:"rgba(255,255,255,.95)", trail:"90,70,235", good:"#0f9d58", warn:"#c98a00", pit:"rgba(110,122,140,.5)" },
};
let THEME_NAME = "dark";
const TH = () => THEMES[THEME_NAME];
function setMapTheme(name) { if (THEMES[name] && name !== THEME_NAME) { THEME_NAME = name; MAP.base = null; } }

const MAP = { track: null, wanted: null, fetching: false, me: null, cars: new Map(), base: null, view: null, nextText: "" };
const LIT_M = 300, TRAIL_M = 160, EASE_S = 0.22, PREDICT_S = 1.6, MAX_MS = 110;
const CALM = matchMedia("(prefers-reduced-motion: reduce)").matches;
try { MAP.track = JSON.parse(localStorage.getItem("sr_track")); } catch (e) {}
if (MAP.track) setTrack(MAP.track);

function setTrack(t) {
  MAP.track = t;
  MAP.base = null;
  $("mapNone").style.display = "none";
  $("mapSrc").textContent = { reference: "reference line", lap: "your lap", learning: "learning the track" }[t.source] || "";
}

async function fetchTrack() {
  if (MAP.fetching) return;
  MAP.fetching = true;
  try {
    const t = await (await fetch("/api/track", { cache: "no-store" })).json();
    if (t && t.pts) {                                // nothing new: the drawing on screen stays
      setTrack(t);
      try { localStorage.setItem("sr_track", JSON.stringify(t)); } catch (e) {}
    }
  } catch (e) {}
  MAP.fetching = false;
}

// lap distance -> a place on the drawing, between the two points around it (null off the end of a part-lap drawing)
function spot(t, d) {
  const n = t.pts.length;
  let i;
  if (t.closed) i = (((d % t.to) + t.to) % t.to) / t.to * n;
  else { if (d < t.from || d > t.to) return null; i = (d - t.from) / Math.max(1, t.to - t.from) * (n - 1); }
  const a = Math.floor(i) % n, b = t.closed ? (a + 1) % n : Math.min(a + 1, n - 1), f = i - Math.floor(i);
  return [t.pts[a][0] + (t.pts[b][0] - t.pts[a][0]) * f, t.pts[a][1] + (t.pts[b][1] - t.pts[a][1]) * f];
}

// a new reading of a car: dead-reckon from it at the speed it has been going until the next one (they come at 1 Hz)
function reading(c, d, now, L) {
  if (c.target != null && d !== c.target) {
    let step = d - c.target;
    if (L && Math.abs(step) > L / 2) step -= Math.sign(step) * L;
    const dt = (now - c.at) / 1000;
    c.v = dt > 0.05 ? Math.max(0, Math.min(MAX_MS, step / dt)) : c.v;
    c.at = now;
  } else if (c.target == null) { c.at = now; c.shown = d; c.v = 0; }
  c.target = d;
}

function mapUpdate(m) {
  if (!m) return;
  if (m.id && m.id !== (MAP.track && MAP.track.id) && !STALE) fetchTrack();
  const now = performance.now(), L = MAP.track && MAP.track.closed ? MAP.track.to : 0;
  if (!MAP.me) MAP.me = {};
  reading(MAP.me, m.dist, now, L);
  const seen = new Set();
  for (const k of m.cars || []) {
    const key = k.p + "|" + k.n;
    seen.add(key);
    const c = MAP.cars.get(key) || {};
    reading(c, k.d, now, L);
    Object.assign(c, { n: k.n, side: k.side, pit: k.pit });
    MAP.cars.set(key, c);
  }
  for (const key of [...MAP.cars.keys()]) if (!seen.has(key)) MAP.cars.delete(key);
}

function moved(c, dtS, now, L) {
  let want = c.target;
  if (!STALE && c.v) want += c.v * Math.min(PREDICT_S, (now - c.at) / 1000);
  let off = want - c.shown;
  if (L) { off = ((off % L) + L) % L; if (off > L / 2) off -= L; }
  c.shown += CALM ? off : off * (1 - Math.exp(-dtS / EASE_S));
  if (L) c.shown = ((c.shown % L) + L) % L;
}

// fit the drawing to the card, turned a quarter if that makes it noticeably bigger
function fit(t, W, H) {
  const xs = t.pts.map(p => p[0]), ys = t.pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 16, w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
  const flat = Math.min((W - 2 * pad) / w, (H - 2 * pad) / h), turned = Math.min((W - 2 * pad) / h, (H - 2 * pad) / w);
  const turn = turned > flat * 1.15, k = turn ? turned : flat, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return turn ? p => [W / 2 + (p[1] - cy) * k, H / 2 + (p[0] - cx) * k] : p => [W / 2 + (p[0] - cx) * k, H / 2 - (p[1] - cy) * k];
}

function line(g, pts, closed) {
  g.beginPath();
  pts.forEach((p, i) => i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]));
  if (closed) g.closePath();
}

// what never moves is drawn once into its own canvas: the track, the reference line, the start line
function mapBase(t, W, H, dpr) {
  const cv = document.createElement("canvas");
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.lineJoin = g.lineCap = "round";
  const view = fit(t, W, H), pts = t.pts.map(view);
  line(g, pts, t.closed);
  g.strokeStyle = TH().casing; g.lineWidth = 11; g.stroke();
  line(g, pts, t.closed);
  g.strokeStyle = t.source === "reference" ? TH().ref : TH().line; g.lineWidth = 1.6; g.stroke();
  if (t.closed) {
    const [x, y] = pts[0], [x2, y2] = pts[1], len = Math.hypot(x2 - x, y2 - y) || 1, nx = -(y2 - y) / len, ny = (x2 - x) / len;
    g.beginPath(); g.moveTo(x - nx * 9, y - ny * 9); g.lineTo(x + nx * 9, y + ny * 9);
    g.strokeStyle = TH().start; g.lineWidth = 2.5; g.stroke();
  }
  MAP.base = cv; MAP.view = view; MAP.size = W + "x" + H + "@" + dpr;
}

function zoneNow(z, me, L) {            // [metres until it starts, inside it now]
  const inside = L ? ((me - z.from) % L + L) % L < ((z.to - z.from) % L + L) % L : me >= z.from && me <= z.to;
  return [L ? ((z.from - me) % L + L) % L : z.from - me, inside];
}

let mapLast = performance.now();
function mapFrame(now) {
  requestAnimationFrame(mapFrame);
  const t = MAP.track, cv = $("trackmap"), box = $("mapbox");
  const dtS = Math.min(0.25, (now - mapLast) / 1000);
  mapLast = now;
  const W = box.clientWidth, H = box.clientHeight;
  if (!t || !MAP.me || MAP.me.shown == null || W < 20 || H < 20) return;
  const dpr = window.devicePixelRatio || 1, L = t.closed ? t.to : 0;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  if (!MAP.base || MAP.size !== W + "x" + H + "@" + dpr) mapBase(t, W, H, dpr);
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  g.drawImage(MAP.base, 0, 0, W, H);
  g.lineJoin = g.lineCap = "round";
  const place = d => { const p = spot(t, d); return p && MAP.view(p); };

  moved(MAP.me, dtS, now, L);
  const me = MAP.me.shown;

  // braking zones: faint all round the lap, lighting up over the last LIT_M metres before you reach one
  let next = null;
  for (const z of t.zones || []) {
    const [ahead, inside] = zoneNow(z, me, L);
    if (inside || (ahead >= 0 && (!next || ahead < next.ahead))) { if (inside || !next || !next.inside) next = { z, ahead, inside }; }
    const lit = inside ? 1 : ahead >= 0 && ahead < LIT_M ? 1 - ahead / LIT_M : 0;
    const span = L ? ((z.to - z.from) % L + L) % L : z.to - z.from, pts = [];
    for (let k = 0; k <= 12; k++) { const p = place(z.from + span * k / 12); if (p) pts.push(p); }
    if (pts.length < 2) continue;
    line(g, pts, false);
    g.strokeStyle = `rgba(255,93,108,${0.3 + 0.7 * lit})`;
    g.lineWidth = 5 + 4 * lit;
    g.shadowColor = "#ff5d6c"; g.shadowBlur = 18 * lit;
    g.stroke();
    g.shadowBlur = 0;
  }

  // a fading trail behind your car
  const drawn = L ? me : Math.max(t.from, Math.min(t.to, me));     // a part-lap drawing lags the car: wait at its tip
  let prev = place(drawn);
  if (!CALM) for (let k = 1; k <= 16 && prev; k++) {
    const p = place(drawn - TRAIL_M * k / 16);
    if (!p) break;
    g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(p[0], p[1]);
    g.strokeStyle = `rgba(${TH().trail},${0.8 * (1 - k / 17)})`; g.lineWidth = 5; g.stroke();
    prev = p;
  }

  // the other cars, then names for the two around you and anyone close on the road, never on top of each other
  g.font = "600 12px 'Segoe UI Variable','Segoe UI',system-ui,sans-serif";
  const labels = [], taken = [];
  const roadTo = d => L ? Math.min(((d - me) % L + L) % L, ((me - d) % L + L) % L) : Math.abs(d - me);
  for (const c of MAP.cars.values()) {
    moved(c, dtS, now, L);
    const p = place(c.shown);
    if (!p) continue;
    const col = c.pit ? TH().pit : c.side < 0 ? TH().good : c.side > 0 ? TH().warn : TH().other;
    g.beginPath(); g.arc(p[0], p[1], c.side ? 5 : 3.5, 0, 7);
    g.fillStyle = col; g.fill();
    if (!c.pit && c.n && (c.side || roadTo(c.shown) < 200)) labels.push({ p, c, col, rank: c.side ? 0 : 1 });
  }
  const mine = place(drawn);
  if (mine) {
    g.beginPath(); g.arc(mine[0], mine[1], 7, 0, 7);
    g.fillStyle = TH().me; g.shadowColor = TH().meGlow; g.shadowBlur = 16; g.fill(); g.shadowBlur = 0;
    taken.push([mine[0] - 10, mine[1] - 10, mine[0] + 10, mine[1] + 10]);
  }
  labels.sort((a, b) => a.rank - b.rank);
  for (const { p, c, col } of labels.slice(0, 6)) {
    const name = c.n.split(" ").pop(), w = g.measureText(name).width, y = p[1] + 4;
    const fits = x => x > 2 && x + w < W - 2 && !taken.some(b => x - 2 < b[2] && x + w + 2 > b[0] && y - 11 < b[3] && y + 3 > b[1]);
    const x = [p[0] + 8, p[0] - 8 - w, p[0] - w / 2].find(fits);       // to the right, else the left, else on top of the dot
    if (x == null) continue;
    taken.push([x - 2, y - 11, x + w + 2, y + 3]);
    g.lineWidth = 3; g.strokeStyle = TH().halo; g.strokeText(name, x, y);
    g.fillStyle = col; g.fillText(name, x, y);
  }

  const text = !next ? " " : next.inside ? `Braking zone · ${next.z.name}` : `Next braking · ${next.z.name} · ${Math.round(next.ahead / 10) * 10} m`;
  if (text !== MAP.nextText) { MAP.nextText = text; $("mapNext").textContent = text; }
  $("mapNext").className = "sub" + (next && (next.inside || next.ahead < LIT_M) ? " bad" : "");
}
requestAnimationFrame(mapFrame);
