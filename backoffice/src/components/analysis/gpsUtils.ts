import type { TelemetryPoint } from "../../types";

export interface GpsTrackPoint {
  pts_ms: number;
  lat: number;
  lng: number;
  altitude: number | null;
  speed: number | null;
  heart_rate: number | null;
  bearing: number | null;
}

export function isValidGpsCoord(
  lat: number | null | undefined,
  lng: number | null | undefined
): boolean {
  if (
    typeof lat !== "number" ||
    typeof lng !== "number" ||
    !Number.isFinite(lat) ||
    !Number.isFinite(lng)
  ) {
    return false;
  }
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return false;
  if (Math.abs(lat) < 1e-6 && Math.abs(lng) < 1e-6) return false;
  return true;
}

/** Calcule l'azimut en degrés [0, 360[ entre deux coordonnées GPS distinctes. */
export function computeBearingDeg(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number
): number | null {
  if (Math.abs(lat2 - lat1) < 1e-9 && Math.abs(lng2 - lng1) < 1e-9) {
    return null;
  }
  const toRad = Math.PI / 180;
  const phi1 = lat1 * toRad;
  const phi2 = lat2 * toRad;
  const dLambda = (lng2 - lng1) * toRad;
  const y = Math.sin(dLambda) * Math.cos(phi2);
  const x =
    Math.cos(phi1) * Math.sin(phi2) -
    Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
  const deg = (Math.atan2(y, x) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export function extractGpsFromTelemetry(
  points: TelemetryPoint[]
): GpsTrackPoint[] {
  const track: GpsTrackPoint[] = [];
  for (const p of points) {
    if (!isValidGpsCoord(p.lat, p.lng)) continue;
    track.push({
      pts_ms: p.pts_ms,
      lat: p.lat as number,
      lng: p.lng as number,
      altitude:
        typeof p.altitude === "number" && Number.isFinite(p.altitude)
          ? p.altitude
          : null,
      speed:
        typeof p.speed === "number" && Number.isFinite(p.speed)
          ? p.speed
          : null,
      heart_rate:
        typeof p.heart_rate === "number" && Number.isFinite(p.heart_rate)
          ? p.heart_rate
          : null,
      bearing:
        typeof p.bearing === "number" && Number.isFinite(p.bearing)
          ? p.bearing
          : null,
    });
  }
  track.sort((a, b) => a.pts_ms - b.pts_ms);

  // Fallback bearing entre points consécutifs distincts si absent en base
  let lastValidBearing: number | null = null;
  for (let i = 0; i < track.length; i++) {
    if (track[i].bearing !== null) {
      lastValidBearing = track[i].bearing;
      continue;
    }
    let computed: number | null = null;
    for (let j = i + 1; j < track.length; j++) {
      computed = computeBearingDeg(
        track[i].lat,
        track[i].lng,
        track[j].lat,
        track[j].lng
      );
      if (computed !== null) break;
    }
    if (computed !== null) {
      track[i].bearing = computed;
      lastValidBearing = computed;
    } else if (lastValidBearing !== null) {
      track[i].bearing = lastValidBearing;
    }
  }
  return track;
}

/** Sélectionne un sous-ensemble régulier de pinpoints (en gardant départ et arrivée). */
export function downsampleGpsPinpoints(
  track: GpsTrackPoint[],
  maxPinpoints = 100
): GpsTrackPoint[] {
  if (track.length <= maxPinpoints) return track;
  if (maxPinpoints <= 2) {
    return track.length > 1 ? [track[0], track[track.length - 1]] : [...track];
  }
  const result: GpsTrackPoint[] = [];
  const step = (track.length - 1) / (maxPinpoints - 1);
  let lastIdx = -1;
  for (let i = 0; i < maxPinpoints; i++) {
    const idx = Math.round(i * step);
    if (idx !== lastIdx) {
      result.push(track[idx]);
      lastIdx = idx;
    }
  }
  return result;
}

export function findClosestGpsPoint(
  track: GpsTrackPoint[],
  targetPtsMs: number
): GpsTrackPoint | null {
  if (!track.length) return null;
  let lo = 0;
  let hi = track.length - 1;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (track[mid].pts_ms === targetPtsMs) return track[mid];
    if (track[mid].pts_ms < targetPtsMs) lo = mid + 1;
    else hi = mid - 1;
  }
  if (lo <= 0) return track[0];
  if (lo >= track.length) return track[track.length - 1];
  const prev = track[lo - 1];
  const next = track[lo];
  return Math.abs(prev.pts_ms - targetPtsMs) <=
    Math.abs(next.pts_ms - targetPtsMs)
    ? prev
    : next;
}
