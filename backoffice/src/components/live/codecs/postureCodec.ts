import type { DecodedPosture, PosturePts, PostureState } from "../types";

export const SENT = Array.from({ length: 33 }, (_, i) => i);
export const MIN_VISIBILITY = 0.2;
export const STALE_MS = 500;
export const POSTURE_TWIN_STALE_MS = 2000;
export const POSTURE_LOST_GRACE_MS = 3000;

export const POSE_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8], [9, 10],
  [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  [11, 23], [12, 24], [23, 24], [23, 25], [24, 26], [25, 27], [26, 28],
  [27, 29], [28, 30], [29, 31], [30, 32], [27, 31], [28, 32],
];

const POSTURE_VERSION = 6;
const POSTURE_VERSION_WORLD = 7;
const POSTURE_POINT_SIZE_WORLD = 8;
const WORLD_RANGE = 1.5;
const POSTURE_HEADER_SIZE = 11;
const POSTURE_POINT_SIZE = 5;
const POSTURE_POINT_COUNT = 33;
const POSTURE_LOST = 254;
const POSTURE_LABELS = ["debout", "assis", "allongé"];

export function asBytes(data: unknown): Uint8Array | null {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  return null;
}

export function isNewerSeq(seq: number, last: number): boolean {
  return seq !== last && ((seq - last) & 0xffff) < 0x8000;
}

export function alignWorldToGravity(world: Float32Array, u: number[]): void {
  const cos = -u[1];
  const theta = Math.acos(Math.max(-1, Math.min(1, cos)));
  if (theta < 1e-4 || theta > (70 * Math.PI) / 180) return;
  const kx = u[2];
  const kz = -u[0];
  const kn = Math.hypot(kx, kz);
  if (!kn) return;
  const ax = kx / kn;
  const az = kz / kn;
  const c = Math.cos(theta);
  const s = Math.sin(theta);
  for (let i = 0; i < world.length; i += 3) {
    const x = world[i];
    const y = world[i + 1];
    const z = world[i + 2];
    const dot = ax * x + az * z;
    world[i] = x * c + -az * y * s + ax * dot * (1 - c);
    world[i + 1] = y * c + (az * x - ax * z) * s;
    world[i + 2] = z * c + ax * y * s + az * dot * (1 - c);
  }
}

export function decodePosture(
  buffer: ArrayBuffer | Uint8Array,
  gravityAlign = true
): DecodedPosture | null {
  const data = asBytes(buffer);
  if (!data || data.length < POSTURE_HEADER_SIZE) return null;

  const version = data[0];
  if (version !== POSTURE_VERSION && version !== POSTURE_VERSION_WORLD) return null;

  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const code = data[1];
  const seq = view.getUint16(3, false);

  if (code === POSTURE_LOST) {
    return { lost: true, seq };
  }

  const pointSize =
    version === POSTURE_VERSION_WORLD ? POSTURE_POINT_SIZE_WORLD : POSTURE_POINT_SIZE;
  if (data.length < POSTURE_HEADER_SIZE + POSTURE_POINT_COUNT * pointSize) return null;

  const frameWidth = view.getUint16(7, false);
  const frameHeight = view.getUint16(9, false);

  const pts = new Float32Array(POSTURE_POINT_COUNT * 3) as PosturePts;
  const depth = new Float32Array(POSTURE_POINT_COUNT);
  const world =
    version === POSTURE_VERSION_WORLD ? new Float32Array(POSTURE_POINT_COUNT * 3) : undefined;

  let offset = POSTURE_HEADER_SIZE;
  let count = 0;

  for (let i = 0; i < POSTURE_POINT_COUNT; i++) {
    const b0 = data[offset];
    const b1 = data[offset + 1];
    const b2 = data[offset + 2];
    const b3 = data[offset + 3];
    const b4 = data[offset + 4];

    const xRaw = (b0 << 4) | (b1 >> 4);
    const yRaw = ((b1 & 0x0f) << 8) | b2;
    let zRaw = (b3 << 4) | (b4 >> 4);
    let visibilityRaw = b4 & 0x0f;

    if (world) {
      const b5 = data[offset + 5];
      const b6 = data[offset + 6];
      const b7 = data[offset + 7];
      const q = (raw: number) => (raw / 4095) * 2 * WORLD_RANGE - WORLD_RANGE;
      world[i * 3] = q((b3 << 4) | (b4 >> 4));
      world[i * 3 + 1] = q(((b4 & 0x0f) << 8) | b5);
      world[i * 3 + 2] = q((b6 << 4) | (b7 >> 4));
      zRaw = 2047.5;
      visibilityRaw = b7 & 0x0f;
    }

    const x = (xRaw / 4095) * 2 - 0.5;
    const y = (yRaw / 4095) * 2 - 0.5;
    const z = (zRaw / 4095) * 2 - 1;
    const visibility = visibilityRaw / 15;

    pts[i * 3] = x;
    pts[i * 3 + 1] = y;
    pts[i * 3 + 2] = visibility;
    depth[i] = z;

    if (visibility >= MIN_VISIBILITY) count++;
    offset += pointSize;
  }

  pts.depth = depth;
  if (world) pts.world = world;

  let gravity: number[] | null = null;
  if (world && data.length >= offset + 6) {
    const g = [0, 1, 2].map((k) => view.getInt16(offset + k * 2, false) / 30000);
    const n = Math.hypot(g[0], g[1], g[2]);
    if (n > 0.5 && n < 1.5) {
      gravity = g.map((v) => v / n);
      if (gravityAlign) alignWorldToGravity(world, gravity);
    }
  }

  if (count === 0) {
    return { lost: true, seq };
  }

  return {
    lost: false,
    seq,
    version,
    label: POSTURE_LABELS[code] ?? "inconnu",
    confidence: data[2] === 0 ? null : data[2] / 255,
    timestamp: view.getUint16(5, false),
    frameWidth,
    frameHeight,
    pts,
    gravity,
    count,
  };
}

export function newPostureState(): PostureState {
  const out = new Float32Array(99) as PosturePts;
  out.depth = new Float32Array(33);
  return {
    buf: [],
    out,
    lastSeq: null,
    lastAt: 0,
    lastValidAt: 0,
    interval: 33,
    lost: false,
    label: null,
    confidence: null,
    count: 0,
    frameW: 0,
    frameH: 0,
    uiAt: 0,
    shownLabel: null,
  };
}

export function isPostureActiveForTwin(
  s: PostureState | undefined,
  now = performance.now()
): boolean {
  if (!s || !s.lastValidAt) return false;
  if (now - s.lastAt > POSTURE_TWIN_STALE_MS) return false;
  if (s.lost) return now - s.lastValidAt <= POSTURE_LOST_GRACE_MS;
  return true;
}

export function lerpPts(
  a: PosturePts,
  b: PosturePts,
  t: number,
  out: PosturePts
): PosturePts {
  const tv = Math.max(0, Math.min(1, t));
  const az = a.depth;
  const bz = b.depth;
  const oz = out.depth;

  for (let i = 0; i < 99; i += 3) {
    const va = a[i + 2];
    const vb = b[i + 2];
    const okA = va >= MIN_VISIBILITY;
    const okB = vb >= MIN_VISIBILITY;

    if (okA && okB) {
      out[i] = a[i] + (b[i] - a[i]) * t;
      out[i + 1] = a[i + 1] + (b[i + 1] - a[i + 1]) * t;
    } else if (okB) {
      out[i] = b[i];
      out[i + 1] = b[i + 1];
    } else {
      out[i] = a[i];
      out[i + 1] = a[i + 1];
    }

    out[i + 2] = va + (vb - va) * tv;

    const landmark = i / 3;
    if (az && bz && oz) {
      oz[landmark] = az[landmark] + (bz[landmark] - az[landmark]) * tv;
    }
  }
  return out;
}

export function poseLocalTime(s: PostureState, ts: number, now: number): number {
  if (s.tsPrev == null || s.arrPrev == null || now - s.arrPrev > 1000) {
    s.tsU = ts;
    s.offset = undefined;
    s.dTs = [];
    s.dArr = [];
    s.jit = 0;
  } else {
    s.tsU = (s.tsU ?? ts) + ((ts - s.tsPrev) & 0xffff);
    s.dTs = s.dTs ?? [];
    s.dArr = s.dArr ?? [];
    s.dTs.push((ts - s.tsPrev) & 0xffff);
    s.dArr.push(now - s.arrPrev);
    if (s.dTs.length > 40) {
      s.dTs.shift();
      s.dArr.shift();
    }
  }
  s.tsPrev = ts;
  s.arrPrev = now;

  if (s.dTs && s.dArr && s.dTs.length >= 15) {
    const sumTs = s.dTs.reduce((a, b) => a + b, 0);
    const sumArr = Math.max(
      s.dArr.reduce((a, b) => a + b, 0),
      1
    );
    const r = sumTs / sumArr;
    s.tsOk = r > 0.8 && r < 1.25;
    s.tsRatio = r;
  }
  if (!s.tsOk || s.tsU == null) return now;

  const raw = now - s.tsU;
  s.offset = s.offset === undefined ? raw : Math.min(s.offset + 0.1, raw);
  s.jit = (s.jit ?? 0) + (Math.abs(raw - s.offset) - (s.jit ?? 0)) * 0.1;
  return s.tsU + s.offset;
}

export function smoothPose(
  s: PostureState,
  pts: PosturePts | null,
  now: number
): PosturePts | null {
  if (!pts) return pts;
  if (!s.sm || !s.smAt || now - s.smAt > 300) {
    const out = new Float32Array(99) as PosturePts;
    out.depth = pts.depth;
    s.sm = {
      out,
      x: new Float32Array(99),
      dx: new Float32Array(99),
      init: false,
    };
  }
  const f = s.sm;
  const dt = Math.max((now - (s.smAt || now)) / 1000, 1e-3);
  s.smAt = now;
  const alpha = (cut: number) => {
    const r = 2 * Math.PI * cut * dt;
    return r / (r + 1);
  };
  for (let i = 0; i < 99; i++) {
    const v = pts[i];
    if (i % 3 === 2 || !f.init) {
      f.out[i] = v;
      f.x[i] = v;
      continue;
    }
    const d = (v - f.x[i]) / dt;
    f.dx[i] += alpha(1.0) * (d - f.dx[i]);
    f.x[i] += alpha(2.0 + 1.5 * Math.abs(f.dx[i])) * (v - f.x[i]);
    f.out[i] = f.x[i];
  }
  f.init = true;
  f.out.depth = pts.depth;
  return f.out;
}

export function samplePose(s: PostureState, rt: number): PosturePts | null {
  const buf = s.buf;
  const n = buf.length;
  if (!n) return null;
  if (n === 1 || rt <= buf[0].t) return buf[0].pts;

  const last = buf[n - 1];
  if (rt >= last.t) {
    const prev = buf[n - 2];
    return lerpPts(
      prev.pts,
      last.pts,
      Math.min((rt - prev.t) / Math.max(last.t - prev.t, 1), 1.5),
      s.out
    );
  }

  let i = n - 1;
  while (i > 1 && buf[i - 1].t > rt) i--;
  const a = buf[i - 1];
  const b = buf[i];
  return lerpPts(a.pts, b.pts, (rt - a.t) / Math.max(b.t - a.t, 1), s.out);
}
