import test from "node:test";
import assert from "node:assert/strict";
import {
  decodePosture,
  isPostureActiveForTwin,
  newPostureState,
} from "../src/components/live/codecs/postureCodec.ts";
import {
  buildLiveHrZones,
  decodeTelemetry,
  dirName,
  formatHrZoneSource,
  hrScaleMax,
  zoneOf,
} from "../src/components/live/codecs/telemetryCodec.ts";
import { parseCoachIa } from "../src/components/live/codecs/coachIaParser.ts";
import {
  flattenWorkoutSteps,
  formatDurationHuman,
  formatStepTarget,
  formatTimerMMSS,
  totalWorkoutDurationS,
} from "../src/components/live/codecs/workoutUtils.ts";
import {
  accumulateTelemetrySample,
  buildSessionEndedSnapshot,
  isSystemParticipant,
  type TelemetryAgg,
} from "../src/components/live/codecs/sessionSummary.ts";
import {
  extractGpsFromTelemetry,
  findClosestGpsPoint,
  findNearestTrackPointByLatLng,
  isValidGpsCoord,
} from "../src/components/analysis/gpsUtils.ts";

function buildV6Packet(opts?: { code?: number; seq?: number; visibility?: number }) {
  const code = opts?.code ?? 0;
  const seq = opts?.seq ?? 42;
  const vis = opts?.visibility ?? 15;
  const buf = new Uint8Array(11 + 33 * 5);
  const view = new DataView(buf.buffer);
  buf[0] = 6;
  buf[1] = code;
  buf[2] = 204;
  view.setUint16(3, seq, false);
  view.setUint16(5, 1234, false);
  view.setUint16(7, 480, false);
  view.setUint16(9, 640, false);

  for (let i = 0; i < 33; i++) {
    const off = 11 + i * 5;
    const xRaw = 2048; // (2048/4095)*2 - 0.5 ≈ 0.5
    const yRaw = 2048;
    const zRaw = 2048;
    buf[off] = (xRaw >> 4) & 0xff;
    buf[off + 1] = ((xRaw & 0x0f) << 4) | ((yRaw >> 8) & 0x0f);
    buf[off + 2] = yRaw & 0xff;
    buf[off + 3] = (zRaw >> 4) & 0xff;
    buf[off + 4] = ((zRaw & 0x0f) << 4) | (vis & 0x0f);
  }
  return buf;
}

function buildV7PacketWithGravity() {
  const buf = new Uint8Array(11 + 33 * 8 + 6);
  const view = new DataView(buf.buffer);
  buf[0] = 7;
  buf[1] = 1; // assis
  buf[2] = 255;
  view.setUint16(3, 99, false);
  view.setUint16(5, 500, false);
  view.setUint16(7, 720, false);
  view.setUint16(9, 1280, false);

  for (let i = 0; i < 33; i++) {
    const off = 11 + i * 8;
    const raw = 2048;
    buf[off] = (raw >> 4) & 0xff;
    buf[off + 1] = ((raw & 0x0f) << 4) | ((raw >> 8) & 0x0f);
    buf[off + 2] = raw & 0xff;
    buf[off + 3] = (raw >> 4) & 0xff;
    buf[off + 4] = ((raw & 0x0f) << 4) | ((raw >> 8) & 0x0f);
    buf[off + 5] = raw & 0xff;
    buf[off + 6] = (raw >> 4) & 0xff;
    buf[off + 7] = ((raw & 0x0f) << 4) | 15;
  }
  const gOff = 11 + 33 * 8;
  view.setInt16(gOff, 0, false);
  view.setInt16(gOff + 2, -30000, false);
  view.setInt16(gOff + 4, 0, false);
  return buf;
}

test("postureCodec decodes v6, v7 and POSTURE_LOST", () => {
  const v6 = decodePosture(buildV6Packet());
  assert.ok(v6 && !v6.lost);
  assert.equal(v6.version, 6);
  assert.equal(v6.label, "debout");
  assert.equal(v6.seq, 42);
  assert.equal(v6.count, 33);
  assert.equal(v6.frameWidth, 480);
  assert.equal(v6.frameHeight, 640);

  const lost = decodePosture(buildV6Packet({ code: 254, seq: 43 }));
  assert.deepEqual(lost, { lost: true, seq: 43 });

  const v7 = decodePosture(buildV7PacketWithGravity());
  assert.ok(v7 && !v7.lost);
  assert.equal(v7.version, 7);
  assert.equal(v7.label, "assis");
  assert.ok(v7.pts.world instanceof Float32Array);
  assert.ok(v7.gravity && v7.gravity.length === 3);
});

test("isPostureActiveForTwin enforces video + posture rule with grace period", () => {
  const st = newPostureState();
  assert.equal(isPostureActiveForTwin(undefined, 1000), false);
  assert.equal(isPostureActiveForTwin(st, 1000), false);

  // Perte sans jamais avoir reçu de pose valide -> faux
  st.lastAt = 1000;
  st.lost = true;
  assert.equal(isPostureActiveForTwin(st, 1100), false);

  // Pose valide reçue à t=2000
  st.lost = false;
  st.lastAt = 2000;
  st.lastValidAt = 2000;
  assert.equal(isPostureActiveForTwin(st, 2500), true);
  assert.equal(isPostureActiveForTwin(st, 4500), false); // > 2000 ms silence

  // Pose perdue temporaire avec flux continu (< 3000 ms depuis lastValidAt)
  st.lost = true;
  st.lastAt = 4000;
  assert.equal(isPostureActiveForTwin(st, 4500), true);
  assert.equal(isPostureActiveForTwin(st, 5500), false); // > 3000 ms grace
});

test("telemetryCodec decodes binary v3 (25 bytes with power_w & bearing)", () => {
  const buf = new Uint8Array(25);
  const view = new DataView(buf.buffer);
  buf[0] = 3;
  buf[1] = 1; // trainee
  view.setUint16(2, 3250, false); // 32.5 km/h
  buf[4] = 148; // HR
  buf[5] = 92; // cadence
  view.setInt32(6, 488566000, false); // 48.8566
  view.setInt32(10, 23522000, false); // 2.3522
  view.setInt16(14, 12500, false); // 125 m
  view.setUint16(16, 180, false); // bearing 180°
  view.setUint16(18, 285, false); // 285 W
  view.setUint32(20, 1700000000, false);

  const decoded = decodeTelemetry(buf);
  assert.ok(decoded);
  assert.equal(decoded.role, "trainee");
  assert.equal(decoded.speed, 32.5);
  assert.equal(decoded.heartRate, 148);
  assert.equal(decoded.cadence, 92);
  assert.ok(Math.abs((decoded.lat ?? 0) - 48.8566) < 1e-6);
  assert.equal(decoded.altitude, 125);
  assert.equal(decoded.bearing, 180);
  assert.equal(decoded.powerW, 285);
  assert.equal(zoneOf(148).name, "Aérobie");
  assert.equal(dirName(90), "E");

  // Zones FC personnalisées (LTHR prioritaire, puis FCmax)
  const lthrZones = buildLiveHrZones({ lthr_bpm: 170, hr_max: 192 });
  assert.equal(zoneOf(130, lthrZones).name, "Z1 Récup.");
  assert.equal(zoneOf(145, lthrZones).name, "Z2 Aérobie");
  assert.equal(zoneOf(155, lthrZones).name, "Z3 Tempo");
  assert.equal(zoneOf(165, lthrZones).name, "Z4 Seuil");
  assert.equal(zoneOf(175, lthrZones).name, "Z5 VO2max");
  assert.equal(formatHrZoneSource({ lthr_bpm: 170, hr_max: 192 }), "LTHR 170 bpm");

  const maxZones = buildLiveHrZones({ hr_max: 200 });
  assert.equal(zoneOf(115, maxZones).name, "Z1 Récup.");
  assert.equal(zoneOf(170, maxZones).name, "Z4 Seuil");
  assert.equal(zoneOf(185, maxZones).name, "Z5 Maximal");
  assert.equal(hrScaleMax({ hr_max: 205 }), 210);
  assert.equal(formatHrZoneSource({ hr_max: 200 }), "FCmax 200 bpm");
});

test("coachIaParser validates v1 state/alert/rep messages", () => {
  const stateMsg = parseCoachIa(
    JSON.stringify({
      v: 1,
      kind: "state",
      trainee: "ath-1",
      reps: 5,
      knee: 92,
      hip: 105,
      lean: 12,
      view: "profil",
      phase: "exercise",
    })
  );
  assert.ok(stateMsg && stateMsg.kind === "state");
  assert.equal(stateMsg.reps, 5);
  assert.equal(stateMsg.view, "profil");

  assert.equal(parseCoachIa(JSON.stringify({ v: 2, kind: "state", trainee: "a" })), null);
  assert.equal(parseCoachIa("not json"), null);
});

test("workoutUtils flattens repeat blocks, reps and formats FTP targets", () => {
  const flat = flattenWorkoutSteps([
    { name: "Échauffement", type: "warmup", duration_s: 300 },
    {
      repeat_count: 2,
      steps: [
        {
          name: "Seuil",
          type: "effort",
          duration_s: 120,
          target_type: "power_pct_ftp",
          target_min: 95,
          target_max: 105,
        },
        { name: "Récup", type: "recovery", duration_s: 60 },
      ],
    },
    {
      kind: "active",
      duration_s: 45,
      reps: 12,
      target_type: "ftp_pct",
      target_min: 80,
      target_max: 90,
      note: "Squat goblet",
    },
  ]);
  assert.equal(flat.length, 6);
  assert.equal(
    formatStepTarget(flat[1], { ftp_w: 300 }),
    "285–315 W (95–105% FTP)"
  );
  assert.equal(flat[5].name, "Squat goblet");
  assert.equal(flat[5].reps, 12);
  assert.equal(
    formatStepTarget(flat[5], { ftp_w: 200 }),
    "12 reps · 160–180 W (80–90% FTP)"
  );
  assert.equal(formatTimerMMSS(125), "02:05");
  assert.equal(totalWorkoutDurationS(flat), 300 + 2 * (120 + 60) + 45);
  assert.equal(formatDurationHuman(95), "1m 35s");
  assert.equal(formatDurationHuman(3720), "1h 02m");
});

test("sessionSummary accumulates telemetry and builds end-of-session snapshot", () => {
  assert.equal(isSystemParticipant("telemetry-agent"), true);
  assert.equal(isSystemParticipant("EG_t4vwpmucGNdL"), true);
  assert.equal(isSystemParticipant("OmjfmKXjcbQolGDX6HBaWvKbdOO2"), false);

  const map = new Map<string, TelemetryAgg>();
  accumulateTelemetrySample(map, "ath-1", { heart_rate: 140, speed: 30, power_w: 220 });
  accumulateTelemetrySample(map, "ath-1", { heart_rate: 160, speed: 36.44, power_w: 260 });
  accumulateTelemetrySample(map, "telemetry-agent", { heart_rate: 99 });

  const snap = buildSessionEndedSnapshot({
    roomName: "session-demo",
    lobbyInfo: {
      title: "Fractionné Seuil",
      org_id: "org-1",
      org_name: "VC Lyon",
      sport: "cycling",
      accessible: true,
    },
    durationS: 1245,
    participants: [
      { identity: "coach-1:web", name: "Ahmed Coach", role: "coach" },
      { identity: "ath-1", name: "Lucas Martin", role: "trainee" },
      { identity: "telemetry-agent", name: "Telemetry Agent (session-demo)", role: "agent" },
      { identity: "EG_t4vwpmucGNdL", name: "EG_t4vwpmucGNdL", role: "egress" },
    ],
    telemetryMap: map,
    nameMap: { "ath-1": "Lucas Martin" },
    roleMap: { "ath-1": "trainee" },
    workout: {
      title: "Seuil 2x8'",
      started: true,
      completed: false,
      currentStepIdx: 2,
      totalSteps: 5,
      totalDurationS: 1800,
    },
    hadRecording: true,
    markersCount: 3,
  });

  assert.equal(snap.title, "Fractionné Seuil");
  assert.equal(snap.durationS, 1245);
  assert.equal(snap.participants.length, 2);
  assert.equal(snap.hadRecording, true);
  assert.equal(snap.markersCount, 3);
  assert.equal(snap.telemetryRows.length, 1);
  assert.equal(snap.telemetryRows[0].hrAvg, 150);
  assert.equal(snap.telemetryRows[0].hrMax, 160);
  assert.equal(snap.telemetryRows[0].speedMax, 36.4);
  assert.equal(snap.telemetryRows[0].powerAvg, 240);
});

test("gpsUtils filters invalid coords, finds closest by pts_ms and nearest by lat/lng", () => {
  assert.equal(isValidGpsCoord(0, 0), false);
  assert.equal(isValidGpsCoord(95, 2), false);
  assert.equal(isValidGpsCoord(45.764, 4.8357), true);

  const track = extractGpsFromTelemetry([
    { pts_ms: 2000, lat: 45.765, lng: 4.836, speed: 30 },
    { pts_ms: 500, lat: 0, lng: 0 }, // ignoré
    { pts_ms: 1000, lat: 45.764, lng: 4.835, altitude: 170 },
    { pts_ms: 3000, lat: null, lng: null },
  ]);

  assert.equal(track.length, 2);
  assert.equal(track[0].pts_ms, 1000);
  assert.equal(track[1].pts_ms, 2000);

  const closest = findClosestGpsPoint(track, 1700);
  assert.equal(closest?.pts_ms, 2000);

  const nearest = findNearestTrackPointByLatLng(track, 45.7641, 4.8351);
  assert.equal(nearest?.pts_ms, 1000);
});
