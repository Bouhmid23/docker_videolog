import type { FlattenedWorkoutStep, WorkoutThresholds } from "../types";

interface RawStep {
  name?: string;
  type?: string;
  kind?: string;
  note?: string | null;
  duration_s?: number;
  reps?: number | null;
  repeat_count?: number;
  target_type?: string | null;
  target_min?: number | null;
  target_max?: number | null;
  steps?: unknown[];
}

const STEP_KIND_LABELS: Record<string, string> = {
  warmup: "Échauffement",
  active: "Effort",
  recovery: "Récupération",
  cooldown: "Retour au calme",
  rest: "Repos",
};

export function flattenWorkoutSteps(steps: unknown): FlattenedWorkoutStep[] {
  const out: FlattenedWorkoutStep[] = [];
  if (!Array.isArray(steps)) return out;
  for (const raw of steps) {
    if (!raw || typeof raw !== "object") continue;
    const s = raw as RawStep;
    const repeatCount = Math.max(1, Number(s.repeat_count || 1));
    if (Array.isArray(s.steps) && s.steps.length) {
      for (let i = 0; i < repeatCount; i++) {
        out.push(...flattenWorkoutSteps(s.steps));
      }
    } else {
      const kindKey = s.kind || s.type || "active";
      const baseName =
        s.name ||
        (s.note && s.note.trim()) ||
        STEP_KIND_LABELS[kindKey] ||
        s.type ||
        "Étape";
      const repsVal =
        typeof s.reps === "number" && s.reps > 0 ? Math.round(s.reps) : null;
      const defaultDur = repsVal ? Math.max(15, repsVal * 4) : 60;
      for (let i = 0; i < repeatCount; i++) {
        out.push({
          name: baseName,
          type: kindKey,
          duration_s: Math.max(5, Number(s.duration_s || defaultDur)),
          reps: repsVal,
          target_type: s.target_type || null,
          target_min: s.target_min ?? null,
          target_max: s.target_max ?? null,
        });
      }
    }
  }
  return out;
}

export function formatStepTarget(
  step: FlattenedWorkoutStep | undefined,
  thresholds?: WorkoutThresholds
): string {
  if (!step) return "";
  const repsPrefix =
    step.reps && step.reps > 0 ? `${step.reps} reps` : "";
  if (step.target_min == null && step.target_max == null) {
    return repsPrefix;
  }
  const tMin = step.target_min ?? 0;
  const tMax = step.target_max ?? tMin;

  let targetStr = "";
  if (step.target_type === "power_pct_ftp" || step.target_type === "ftp_pct") {
    const ftp = thresholds?.ftp_w;
    if (ftp) {
      const w1 = Math.round((tMin / 100) * ftp);
      const w2 = Math.round((tMax / 100) * ftp);
      targetStr =
        w1 === w2
          ? `${w1} W (${tMin}% FTP)`
          : `${w1}–${w2} W (${tMin}–${tMax}% FTP)`;
    } else {
      targetStr = tMin === tMax ? `${tMin}% FTP` : `${tMin}–${tMax}% FTP`;
    }
  } else if (
    step.target_type === "hr_pct_lthr" ||
    step.target_type === "lthr_pct"
  ) {
    const lthr = thresholds?.lthr_bpm;
    if (lthr) {
      const b1 = Math.round((tMin / 100) * lthr);
      const b2 = Math.round((tMax / 100) * lthr);
      targetStr = b1 === b2 ? `${b1} bpm` : `${b1}–${b2} bpm`;
    } else {
      targetStr =
        tMin === tMax ? `${tMin}% FC seuil` : `${tMin}–${tMax}% FC seuil`;
    }
  } else if (step.target_type === "rpe") {
    targetStr = `Effort ${tMin}/10`;
  } else {
    targetStr = tMin === tMax ? `${tMin}%` : `${tMin}–${tMax}%`;
  }

  return repsPrefix && targetStr
    ? `${repsPrefix} · ${targetStr}`
    : repsPrefix || targetStr;
}

export function formatTimerMMSS(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function totalWorkoutDurationS(steps: FlattenedWorkoutStep[]): number {
  return steps.reduce((acc, s) => acc + Math.max(0, Number(s.duration_s || 0)), 0);
}

export function formatDurationHuman(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${r}s`;
}
