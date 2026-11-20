import type { CoachIaMessage } from "../types";
import { asBytes } from "./postureCodec";

export const COACH_IA_VIEWS = {
  face: "de face",
  profil: "de profil",
  "3/4": "de trois quarts",
} as const;

export const COACH_IA_ERRORS: Record<string, string> = {
  torso_lean: "buste trop penché",
};

export const COACH_IA_PHASE_LABELS: Record<string, string> = {
  idle: "Au repos",
  positioning: "Placement",
  countdown: "Compte à rebours",
  calibrating: "Calibrage",
  ready: "Prêt",
  exercise: "Analyse en cours",
  paused: "Pause",
  done: "Terminé",
};

const PHASES = [
  "idle",
  "positioning",
  "countdown",
  "calibrating",
  "ready",
  "exercise",
  "paused",
  "done",
];
const TARGETS = ["calibrating", "exercise"];
const MAX_TEXT = 80;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const int = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 && v < 100000 ? v : null;
const txt = (v: unknown): string => String(v ?? "").slice(0, MAX_TEXT);
const phaseOf = (v: unknown): string | null =>
  typeof v === "string" && PHASES.includes(v) ? v : null;
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
const viewOf = (v: unknown): "face" | "profil" | "3/4" | null =>
  v === "face" || v === "profil" || v === "3/4" ? v : null;

export const degLabel = (v: number | null) =>
  v == null ? "—" : `${Math.round(v)}°`;

export function parseCoachIa(
  payload: ArrayBuffer | Uint8Array | string
): CoachIaMessage | null {
  let o: Record<string, unknown> | null = null;
  try {
    const raw =
      typeof payload === "string"
        ? payload
        : new TextDecoder().decode(asBytes(payload) ?? new Uint8Array());
    o = obj(JSON.parse(raw));
  } catch {
    return null;
  }
  if (!o || o.v !== 1 || typeof o.trainee !== "string" || !o.trainee) return null;

  if (o.kind === "state") {
    const calib = obj(o.calib);
    const ref = obj(o.ref);
    return {
      kind: "state",
      trainee: o.trainee,
      reps: int(o.reps),
      knee: num(o.knee),
      hip: num(o.hip),
      lean: num(o.lean),
      view: viewOf(o.view),
      phase: phaseOf(o.phase),
      countdown: int(o.countdown),
      calib: calib ? { done: int(calib.done), total: int(calib.total) } : null,
      ref: ref
        ? { lean: num(ref.lean), spread: num(ref.spread), limit: num(ref.limit) }
        : null,
    };
  }

  if (o.kind === "alert") {
    return o.text
      ? { kind: "alert", trainee: o.trainee, code: txt(o.code), text: txt(o.text) }
      : null;
  }

  if (o.kind === "phase") {
    const phase = phaseOf(o.phase);
    if (!phase) return null;
    const calib = obj(o.calib);
    const ref = obj(o.ref);
    const checks = obj(o.checks);
    const summary = obj(o.summary);
    return {
      kind: "phase",
      trainee: o.trainee,
      phase,
      text: txt(o.text),
      countdown: int(o.countdown),
      target:
        typeof o.target === "string" && TARGETS.includes(o.target)
          ? (o.target as "calibrating" | "exercise")
          : null,
      calib: calib ? { done: int(calib.done), total: int(calib.total) } : null,
      ref: ref
        ? { lean: num(ref.lean), spread: num(ref.spread), limit: num(ref.limit) }
        : null,
      checks: checks
        ? {
            visible: checks.visible === true,
            view: viewOf(checks.view),
            missing: Array.isArray(checks.missing)
              ? checks.missing.slice(0, 4).map(txt)
              : [],
          }
        : null,
      summary: summary
        ? {
            reps: int(summary.reps),
            partial: int(summary.partial),
            errors: int(summary.errors),
            refLean: num(summary.ref_lean),
            limit: num(summary.limit),
          }
        : null,
    };
  }

  if (o.kind === "rep") {
    if (typeof o.complete !== "boolean") return null;
    const n = int(o.n);
    if (n === null && o.complete) return null;
    return {
      kind: "rep",
      trainee: o.trainee,
      n,
      complete: o.complete,
      say: typeof o.text === "string" && o.text ? txt(o.text) : null,
      errors: Array.isArray(o.errors) ? o.errors.slice(0, 5).map(txt) : [],
      kneeMin: num(o.knee_min),
      hip: num(o.hip),
      leanMax: num(o.lean_max),
      downS: num(o.down_s),
      upS: num(o.up_s),
    };
  }

  return null;
}
