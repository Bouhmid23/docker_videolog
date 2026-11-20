import type { TelemetryPoint } from "../../types";

export function formatPts(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

export function formatPtsWithTenths(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  const tenths = Math.floor((Math.max(0, ms) % 1000) / 100);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}.${tenths}`;
}

export function findClosestTelemetry(
  points: TelemetryPoint[],
  targetPtsMs: number
): TelemetryPoint | null {
  if (!points || points.length === 0) return null;
  let lo = 0;
  let hi = points.length - 1;

  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].pts_ms === targetPtsMs) return points[mid];
    if (points[mid].pts_ms < targetPtsMs) lo = mid + 1;
    else hi = mid - 1;
  }

  if (lo <= 0) return points[0];
  if (lo >= points.length) return points[points.length - 1];

  const prev = points[lo - 1];
  const next = points[lo];
  return Math.abs(prev.pts_ms - targetPtsMs) <= Math.abs(next.pts_ms - targetPtsMs)
    ? prev
    : next;
}
