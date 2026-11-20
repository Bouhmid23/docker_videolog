import type { LobbyInfo, TelemetryData } from "../types";

export function isSystemParticipant(identity?: string | null): boolean {
  const raw = (identity || "").trim();
  if (!raw) return false;
  const base = raw.split(":")[0].toLowerCase();
  return (
    base === "telemetry-agent" ||
    base === "telemetry_agent" ||
    /^eg_[a-z0-9]+$/i.test(base)
  );
}

export interface WorkoutProgressSummary {
  title: string;
  started: boolean;
  completed: boolean;
  currentStepIdx: number;
  totalSteps: number;
  totalDurationS: number;
}

export interface SessionParticipantSummary {
  identity: string;
  name: string;
  role: string;
}

export interface TelemetryAgg {
  samples: number;
  hrSum: number;
  hrCount: number;
  hrMax: number | null;
  speedMax: number | null;
  powerSum: number;
  powerCount: number;
  powerMax: number | null;
}

export interface ParticipantTelemetrySummary {
  identity: string;
  name: string;
  role: string;
  samples: number;
  hrAvg: number | null;
  hrMax: number | null;
  speedMax: number | null;
  powerAvg: number | null;
  powerMax: number | null;
}

export interface SessionEndedSnapshot {
  roomName: string;
  title: string;
  orgId: string | null;
  orgName: string | null;
  sport: string | null;
  durationS: number;
  endedAtIso: string;
  participants: SessionParticipantSummary[];
  telemetryRows: ParticipantTelemetrySummary[];
  workout: WorkoutProgressSummary | null;
  hadRecording: boolean;
  markersCount: number;
}

export function accumulateTelemetrySample(
  map: Map<string, TelemetryAgg>,
  identity: string,
  data: TelemetryData
): void {
  const cur = map.get(identity) ?? {
    samples: 0,
    hrSum: 0,
    hrCount: 0,
    hrMax: null,
    speedMax: null,
    powerSum: 0,
    powerCount: 0,
    powerMax: null,
  };
  cur.samples += 1;

  const hrRaw = data.heart_rate ?? data.heartRate;
  if (typeof hrRaw === "number" && Number.isFinite(hrRaw) && hrRaw > 0) {
    cur.hrSum += hrRaw;
    cur.hrCount += 1;
    cur.hrMax = cur.hrMax === null ? hrRaw : Math.max(cur.hrMax, hrRaw);
  }

  if (
    typeof data.speed === "number" &&
    Number.isFinite(data.speed) &&
    data.speed >= 0
  ) {
    cur.speedMax =
      cur.speedMax === null ? data.speed : Math.max(cur.speedMax, data.speed);
  }

  if (
    typeof data.power_w === "number" &&
    Number.isFinite(data.power_w) &&
    data.power_w >= 0
  ) {
    cur.powerSum += data.power_w;
    cur.powerCount += 1;
    cur.powerMax =
      cur.powerMax === null ? data.power_w : Math.max(cur.powerMax, data.power_w);
  }

  map.set(identity, cur);
}

export function buildSessionEndedSnapshot(params: {
  roomName: string;
  lobbyInfo: LobbyInfo | null;
  orgIdFallback?: string | null;
  durationS: number;
  participants: SessionParticipantSummary[];
  telemetryMap: Map<string, TelemetryAgg>;
  nameMap: Record<string, string>;
  roleMap: Record<string, string>;
  workout: WorkoutProgressSummary | null;
  hadRecording: boolean;
  markersCount: number;
}): SessionEndedSnapshot {
  const participantById = new Map<string, SessionParticipantSummary>();
  for (const p of params.participants) {
    if (!isSystemParticipant(p.identity)) {
      participantById.set(p.identity, p);
    }
  }

  const telemetryRows: ParticipantTelemetrySummary[] = [];
  for (const [identity, agg] of params.telemetryMap.entries()) {
    if (isSystemParticipant(identity)) continue;
    const known = participantById.get(identity);
    telemetryRows.push({
      identity,
      name: known?.name || params.nameMap[identity] || identity,
      role: known?.role || params.roleMap[identity] || "trainee",
      samples: agg.samples,
      hrAvg: agg.hrCount > 0 ? Math.round(agg.hrSum / agg.hrCount) : null,
      hrMax: agg.hrMax !== null ? Math.round(agg.hrMax) : null,
      speedMax:
        agg.speedMax !== null ? Math.round(agg.speedMax * 10) / 10 : null,
      powerAvg:
        agg.powerCount > 0 ? Math.round(agg.powerSum / agg.powerCount) : null,
      powerMax: agg.powerMax !== null ? Math.round(agg.powerMax) : null,
    });
  }

  return {
    roomName: params.roomName,
    title: params.lobbyInfo?.title || params.roomName,
    orgId: params.lobbyInfo?.org_id || params.orgIdFallback || null,
    orgName: params.lobbyInfo?.org_name || null,
    sport: params.lobbyInfo?.sport || null,
    durationS: Math.max(0, Math.round(params.durationS)),
    endedAtIso: new Date().toISOString(),
    participants: [...participantById.values()],
    telemetryRows,
    workout: params.workout,
    hadRecording: params.hadRecording,
    markersCount: params.markersCount,
  };
}
