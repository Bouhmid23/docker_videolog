import type { ConnectionQuality, RemoteVideoTrack } from "livekit-client";

export interface PosturePts extends Float32Array {
  depth?: Float32Array;
  world?: Float32Array;
}

export type DecodedPosture =
  | {
      lost: true;
      seq: number;
    }
  | {
      lost: false;
      seq: number;
      version: number;
      label: string;
      confidence: number | null;
      timestamp: number;
      frameWidth: number;
      frameHeight: number;
      pts: PosturePts;
      gravity: number[] | null;
      count: number;
    };

export interface PostureDisplay {
  label: string;
  confidence: number | null;
  count: number;
}

export interface PostureSmoothFilter {
  out: PosturePts;
  x: Float32Array;
  dx: Float32Array;
  init: boolean;
}

export interface PostureState {
  buf: Array<{ t: number; a: number; pts: PosturePts }>;
  out: PosturePts;
  lastSeq: number | null;
  lastAt: number;
  lastValidAt: number;
  interval: number;
  lost: boolean;
  label: string | null;
  confidence: number | null;
  count: number;
  frameW: number;
  frameH: number;
  uiAt: number;
  shownLabel: string | null;
  tsPrev?: number;
  arrPrev?: number;
  tsU?: number;
  offset?: number;
  dTs?: number[];
  dArr?: number[];
  jit?: number;
  tsOk?: boolean;
  tsRatio?: number;
  sm?: PostureSmoothFilter;
  smAt?: number;
  lossT?: number;
  rx?: number;
  exp?: number;
  rxRate?: number;
  txRate?: number;
  age?: number;
  relAcc?: number;
  relPrev?: number;
  rawMin?: number;
  ageRel?: number;
  delayShown?: number;
}

export interface TelemetryData {
  role?: string;
  speed?: number | null;
  heart_rate?: number | null;
  heartRate?: number | null;
  cadence?: number | null;
  power_w?: number | null;
  altitude?: number | null;
  bearing?: number | null;
  gps?: {
    lat?: number | null;
    lng?: number | null;
    altitude?: number | null;
    bearing?: number | null;
  };
}

export interface NetSample {
  kbps?: number | null;
  fps?: number | null;
  loss?: number | null;
  jitter?: number | null;
  rtt?: number | null;
  poseHz?: number | null;
  poseAge?: number | null;
  poseSub?: string;
  poseAgeSub?: string;
}

export type MicStateKind = "live" | "muted" | "none" | "pending";

export interface MicInfo {
  state: MicStateKind;
  level: number;
  speaking: boolean;
}

export interface AvatarModelInfo {
  key: string;
  label: string;
  rigged: boolean;
}

export type CoachIaMessage =
  | {
      kind: "state";
      trainee: string;
      reps: number | null;
      knee: number | null;
      hip: number | null;
      lean: number | null;
      view: "face" | "profil" | "3/4" | null;
      phase: string | null;
      countdown: number | null;
      calib: { done: number | null; total: number | null } | null;
      ref: { lean: number | null; spread: number | null; limit: number | null } | null;
    }
  | {
      kind: "alert";
      trainee: string;
      code: string;
      text: string;
    }
  | {
      kind: "phase";
      trainee: string;
      phase: string | null;
      text: string;
      countdown: number | null;
      target: "calibrating" | "exercise" | null;
      calib: { done: number | null; total: number | null } | null;
      ref: { lean: number | null; spread: number | null; limit: number | null } | null;
      checks: { visible: boolean; view: "face" | "profil" | "3/4" | null; missing: string[] } | null;
      summary: {
        reps: number | null;
        partial: number | null;
        errors: number | null;
        refLean: number | null;
        limit: number | null;
      } | null;
    }
  | {
      kind: "rep";
      trainee: string;
      n: number | null;
      complete: boolean;
      say: string | null;
      errors: string[];
      kneeMin: number | null;
      hip: number | null;
      leanMax: number | null;
      downS: number | null;
      upS: number | null;
    };

export interface FlattenedWorkoutStep {
  name: string;
  type: string;
  duration_s: number;
  reps?: number | null;
  target_type: string | null;
  target_min: number | null;
  target_max: number | null;
}

export interface WorkoutThresholds {
  ftp_w?: number | null;
  lthr_bpm?: number | null;
  hr_max?: number | null;
  hr_rest?: number | null;
}

export interface LobbyWorkout {
  title?: string;
  steps?: unknown[];
  thresholds?: WorkoutThresholds;
}

export interface LobbyParticipant {
  uid?: string;
  name: string;
  connected?: boolean;
  is_owner?: boolean;
}

export interface LobbyInfo {
  org_id?: string | null;
  org_name?: string;
  sport?: string | null;
  title?: string;
  finished?: boolean;
  accessible?: boolean;
  reason?: string;
  active_count?: number;
  duration_s?: number;
  scheduled_at?: string | null;
  invited_participants?: LobbyParticipant[];
  participant_thresholds?: Record<string, WorkoutThresholds>;
  planned_workout?: LobbyWorkout | null;
}

export interface ActiveRoomSummary {
  room_name: string;
  title?: string;
  count?: number;
}

export interface RoomParticipantSummary {
  identity: string;
  name: string;
  isLocal: boolean;
  isSpeaking: boolean;
  micLabel: string;
  hasVideoCard: boolean;
}

export interface VideoCardEntry {
  identity: string;
  track: RemoteVideoTrack;
  trackSid: string;
  connectionQuality: ConnectionQuality;
}

export interface WebPoseResult {
  world: number[][];
  image: number[][];
  vis: number[];
  detectMs: number;
}
