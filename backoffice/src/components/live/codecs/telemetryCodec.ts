import type { TelemetryData } from "../types";
import { asBytes } from "./postureCodec";

export interface HrZoneDef {
  max: number;
  name: string;
  color: string;
}

export interface HrThresholds {
  lthr_bpm?: number | null;
  hr_max?: number | null;
  hr_rest?: number | null;
}

export const HR_ZONES: readonly HrZoneDef[] = [
  { max: 100, name: "Repos", color: "#98a3b0" },
  { max: 125, name: "Léger", color: "#3a8fd6" },
  { max: 150, name: "Aérobie", color: "#18abf8" },
  { max: 170, name: "Seuil", color: "#f5a524" },
  { max: Infinity, name: "Maximum", color: "#e5373a" },
] as const;

/**
 * Construit 5 zones FC personnalisées alignées sur le modèle Friel/FCmax du backend
 * (`mongo-writer/services/zones.py`) : priorité à `lthr_bpm`, repli sur `hr_max`,
 * puis sur `HR_ZONES` par défaut.
 */
export function buildLiveHrZones(
  thresholds?: HrThresholds | null
): readonly HrZoneDef[] {
  const lthr = thresholds?.lthr_bpm;
  if (typeof lthr === "number" && Number.isFinite(lthr) && lthr >= 80) {
    return [
      { max: Math.round(lthr * 0.81), name: "Z1 Récup.", color: "#98a3b0" },
      { max: Math.round(lthr * 0.89), name: "Z2 Aérobie", color: "#3a8fd6" },
      { max: Math.round(lthr * 0.93), name: "Z3 Tempo", color: "#18abf8" },
      { max: Math.round(lthr * 1.0), name: "Z4 Seuil", color: "#f5a524" },
      { max: Infinity, name: "Z5 VO2max", color: "#e5373a" },
    ];
  }
  const hrMax = thresholds?.hr_max;
  if (typeof hrMax === "number" && Number.isFinite(hrMax) && hrMax >= 100) {
    return [
      { max: Math.round(hrMax * 0.6), name: "Z1 Récup.", color: "#98a3b0" },
      { max: Math.round(hrMax * 0.7), name: "Z2 Endurance", color: "#3a8fd6" },
      { max: Math.round(hrMax * 0.8), name: "Z3 Tempo", color: "#18abf8" },
      { max: Math.round(hrMax * 0.9), name: "Z4 Seuil", color: "#f5a524" },
      { max: Infinity, name: "Z5 Maximal", color: "#e5373a" },
    ];
  }
  return HR_ZONES;
}

export function hrScaleMax(thresholds?: HrThresholds | null): number {
  const hrMax = thresholds?.hr_max;
  const lthr = thresholds?.lthr_bpm;
  const fromMax =
    typeof hrMax === "number" && Number.isFinite(hrMax) ? hrMax + 5 : 0;
  const fromLthr =
    typeof lthr === "number" && Number.isFinite(lthr)
      ? Math.round(lthr * 1.12)
      : 0;
  return Math.max(200, fromMax, fromLthr);
}

export function formatHrZoneSource(thresholds?: HrThresholds | null): string | null {
  if (thresholds?.lthr_bpm && thresholds.lthr_bpm >= 80) {
    return `LTHR ${Math.round(thresholds.lthr_bpm)} bpm`;
  }
  if (thresholds?.hr_max && thresholds.hr_max >= 100) {
    return `FCmax ${Math.round(thresholds.hr_max)} bpm`;
  }
  return null;
}

const DIRS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];

export const zoneOf = (
  bpm: number,
  zones: readonly HrZoneDef[] = HR_ZONES
): HrZoneDef => zones.find((z) => bpm < z.max) ?? zones[zones.length - 1];

export const dirName = (deg: number) =>
  DIRS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

export const fmtCoord = (v: number, pos: string, neg: string) =>
  `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;

export const toFiniteNum = (v: unknown): number | null =>
  v === null || v === undefined || v === "" || !Number.isFinite(Number(v))
    ? null
    : Number(v);

export interface DecodedBinaryTelemetry {
  role: string;
  speed: number | null;
  heartRate: number | null;
  cadence: number | null;
  lat: number | null;
  lng: number | null;
  altitude: number | null;
  bearing: number | null;
  powerW: number | null;
  timestamp: number;
  receivedAt: string;
}

export function decodeTelemetry(
  buffer: ArrayBuffer | Uint8Array
): DecodedBinaryTelemetry | null {
  const data = asBytes(buffer);
  if (!data) return null;
  const version = data[0];
  if (version === 1 && data.length < 18) return null;
  if (version === 2 && data.length < 21) return null;
  if (version === 3 && data.length < 25) return null;
  if (version !== 1 && version !== 2 && version !== 3) return null;

  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const roles = ["coach", "trainee", "observer"];
  const speedRaw = view.getUint16(2, false);
  const heartRateRaw = data[4];
  const cadenceRaw = data[5];
  const latRaw = view.getInt32(6, false);
  const lngRaw = view.getInt32(10, false);
  const altitudeRaw = version >= 2 ? view.getInt16(14, false) : 0x7fff;

  let bearing: number | null = null;
  let powerW: number | null = null;
  let timestampOffset = 14;

  if (version === 2) {
    bearing = data[16] === 255 ? null : data[16];
    timestampOffset = 17;
  } else if (version === 3) {
    const bearingRaw = view.getUint16(16, false);
    const powerRaw = view.getUint16(18, false);
    bearing = bearingRaw === 65535 ? null : bearingRaw;
    powerW = powerRaw === 65535 ? null : powerRaw;
    timestampOffset = 20;
  }

  return {
    role: roles[data[1]] || "unknown",
    speed: speedRaw === 65535 ? null : speedRaw / 100,
    heartRate: heartRateRaw === 255 ? null : heartRateRaw,
    cadence: cadenceRaw === 255 ? null : cadenceRaw,
    lat: latRaw === 0x7fffffff ? null : latRaw / 10000000,
    lng: lngRaw === 0x7fffffff ? null : lngRaw / 10000000,
    altitude: altitudeRaw === 0x7fff ? null : altitudeRaw / 100,
    bearing,
    powerW,
    timestamp: view.getUint32(timestampOffset, false),
    receivedAt: new Date().toISOString(),
  };
}

export function binaryTelemetryToData(t: DecodedBinaryTelemetry): TelemetryData {
  return {
    role: t.role,
    speed: t.speed,
    heart_rate: t.heartRate,
    cadence: t.cadence,
    power_w: t.powerW,
    gps: {
      lat: t.lat,
      lng: t.lng,
      altitude: t.altitude,
      bearing: t.bearing,
    },
  };
}
