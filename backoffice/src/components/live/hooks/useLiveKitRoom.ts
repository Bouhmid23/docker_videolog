import { useCallback, useEffect, useRef, useState } from "react";
import {
  ConnectionQuality,
  LocalTrack,
  Participant,
  RemoteParticipant,
  RemoteVideoTrack,
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { auth } from "../../../lib/firebase";
import { parseCoachIa } from "../codecs/coachIaParser";
import { attachConnLog, connLog, connSnap, initConnLogHooks } from "../codecs/connLog";
import { isSystemParticipant } from "../codecs/sessionSummary";
import {
  asBytes,
  decodePosture,
  isNewerSeq,
  isPostureActiveForTwin,
  MIN_VISIBILITY,
  newPostureState,
  POSE_CONNECTIONS,
  poseLocalTime,
  samplePose,
  SENT,
  smoothPose,
  STALE_MS,
} from "../codecs/postureCodec";
import { binaryTelemetryToData, decodeTelemetry } from "../codecs/telemetryCodec";
import { getWebPoseConfig } from "../codecs/webPoseLoader";
import type {
  AvatarModelInfo,
  CoachIaMessage,
  MicInfo,
  MicStateKind,
  NetSample,
  PostureDisplay,
  PosturePts,
  PostureState,
  RoomParticipantSummary,
  TelemetryData,
  VideoCardEntry,
  WebPoseResult,
} from "../types";

const WS_URL_FALLBACK = "wss://livekit.videolog.app";
const VIDEO_STALL_MS = 4000;
const VIDEO_FIRST_FRAME_MS = 15000;
const DATA_LINES_MAX = 300;

export interface VideoSlotRef {
  video: HTMLVideoElement | null;
  canvas: HTMLCanvasElement | null;
  track: RemoteVideoTrack;
  vstats?: { jbd: number; jbe: number; dec: number; fr: number };
  vDelay: number;
  delay: number;
  netPrev?: {
    t: number;
    bytes: number;
    recv: number;
    lost: number;
    frames: number;
  };
  elOk?: boolean;
  elFrames?: number;
  lastFrameAt: number;
  gotFrame?: boolean;
  frameReady: boolean;
  iframeWindow: Window | null;
  avatarActive: boolean | null;
  statusAt: number;
  lastStatusText: string;
  webPoseError?: string;
  webPoseHz?: number;
  webPoseN?: number;
  webPoseAt?: number;
  webDetectMs?: number;
  stopWebPose?: () => void;
}

export interface DataLogState {
  lines: string[];
  totalCount: number;
}

const withTimeout = <T,>(promise: Promise<T>, ms = 1500): Promise<T> =>
  Promise.race([
    promise,
    new Promise<T>((_, rej) =>
      setTimeout(() => rej(new Error("délai dépassé")), ms)
    ),
  ]);

function pairRtt(report: RTCStatsReport | null | undefined): number | null {
  let ms: number | null = null;
  report?.forEach((r: Record<string, unknown>) => {
    if (
      r.type !== "candidate-pair" ||
      !(r.nominated || r.selected || r.state === "succeeded")
    ) {
      return;
    }
    const curRtt = typeof r.currentRoundTripTime === "number" ? r.currentRoundTripTime : null;
    const totalRtt = typeof r.totalRoundTripTime === "number" ? r.totalRoundTripTime : 0;
    const resp = typeof r.responsesReceived === "number" ? r.responsesReceived : 0;
    const v = curRtt ?? (resp > 0 ? totalRtt / resp : null);
    if (v && v > 0) ms = v * 1000;
  });
  return ms;
}

function micStateOf(p: Participant, local: Participant): MicStateKind {
  const mic = p.getTrackPublication(Track.Source.Microphone);
  if (!mic) return "none";
  if (mic.isMuted) return "muted";
  if (p !== local && !mic.isSubscribed) return "pending";
  return "live";
}

function parseParticipantMetaRole(p?: Participant | null): string | null {
  const raw = p?.metadata;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { role?: string };
    if (parsed && typeof parsed.role === "string" && parsed.role.trim()) {
      return parsed.role.trim();
    }
  } catch {
    // ignore
  }
  return null;
}

export function formatParticipantDisplayName(
  identity: string,
  nameMap?: Record<string, string>
): string {
  const baseId = identity.split(":")[0];
  const mapped = nameMap?.[identity] || nameMap?.[baseId];
  if (mapped && mapped.trim()) return mapped.trim();
  return baseId || identity || "inconnu";
}

function fitCanvas(canvas: HTMLCanvasElement, cw: number, ch: number) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cw * dpr);
  const h = Math.round(ch * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  return ctx;
}

function drawSkeletonOnCanvas(
  slot: VideoSlotRef,
  s: PostureState | undefined,
  pts: PosturePts | null
) {
  const { video, canvas } = slot;
  if (!video || !canvas) return;
  const cw = canvas.clientWidth || 1;
  const ch = canvas.clientHeight || 1;
  const ctx = fitCanvas(canvas, cw, ch);
  if (!ctx || !pts || !s) return;

  const sw = video.videoWidth || s.frameW || cw;
  const sh = video.videoHeight || s.frameH || ch;
  const scale = Math.min(cw / sw, ch / sh);
  const ox = (cw - sw * scale) / 2;
  const oy = (ch - sh * scale) / 2;
  const depth = pts.depth;

  const project = (i: number) => {
    const x = pts[i * 3];
    const y = pts[i * 3 + 1];
    const z = depth ? depth[i] : 0;
    const perspective = 1 - 0.12 * z;
    const px = 0.5 + (x - 0.5) * perspective;
    const py = 0.5 + (y - 0.5) * perspective;
    return { x: ox + px * sw * scale, y: oy + py * sh * scale, z };
  };

  const ok = (i: number) => pts[i * 3 + 2] >= MIN_VISIBILITY;

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  for (const [a, b] of POSE_CONNECTIONS) {
    if (!ok(a) || !ok(b)) continue;
    const p1 = project(a);
    const p2 = project(b);
    const z = (p1.z + p2.z) / 2;
    ctx.strokeStyle = z < -0.15 ? "#18abf8" : "#3a8fd6";
    ctx.lineWidth = Math.max(1, 2.5 * (1 - 0.12 * z));
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
  }

  for (const i of SENT) {
    if (!ok(i)) continue;
    const p = project(i);
    const radius = (i <= 10 ? 2.5 : 4) * (1 - 0.12 * p.z);
    ctx.fillStyle =
      p.z < -0.15 ? "#18abf8" : p.z > 0.15 ? "#98a3b0" : "#eef2f6";
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}

export interface UseLiveKitRoomOptions {
  avatarPage: string;
  lowLatency: boolean;
  onWorkoutStep?: (payload: Uint8Array) => void;
  onDisconnectedCallback?: () => void;
}

export function useLiveKitRoom(options: UseLiveKitRoomOptions) {
  const { avatarPage, lowLatency, onWorkoutStep, onDisconnectedCallback } = options;

  const query = new URLSearchParams(window.location.search);
  const debugMode = query.has("debug");
  const gravityAlign = query.get("gravity") !== "off";
  const webPoseEnabled = getWebPoseConfig().enabled;
  const webPoseModelName = getWebPoseConfig().model;

  const roomRef = useRef<Room | null>(null);
  const audioHostRef = useRef<HTMLDivElement | null>(null);
  const lowLatencyRef = useRef(lowLatency);
  lowLatencyRef.current = lowLatency;

  const onWorkoutStepRef = useRef(onWorkoutStep);
  onWorkoutStepRef.current = onWorkoutStep;
  const onDisconnectedRef = useRef(onDisconnectedCallback);
  onDisconnectedRef.current = onDisconnectedCallback;

  // Buffers temps réel (sans déclencher de re-render à 30/60 Hz)
  const videoSlotsRef = useRef<Map<string, VideoSlotRef>>(new Map());
  const stalledVideoRef = useRef<
    Map<string, { track: RemoteVideoTrack; participant: RemoteParticipant }>
  >(new Map());
  const postureStatesRef = useRef<Map<string, PostureState>>(new Map());
  const lastPosturesRef = useRef<Map<string, PostureDisplay>>(new Map());
  const lastTelemetryRef = useRef<Map<string, TelemetryData>>(new Map());
  const lastTelemetryAtRef = useRef<Map<string, number>>(new Map());
  const rttStateRef = useRef<{ last: number | null; fails: number }>({
    last: null,
    fails: 0,
  });

  // Abonnements directs vers les widgets locaux (sparklines, CoachIA, télémétrie, statut texte)
  const netListenersRef = useRef<Map<string, Set<(s: NetSample) => void>>>(
    new Map()
  );
  const iaListenersRef = useRef<Map<string, Set<(m: CoachIaMessage) => void>>>(
    new Map()
  );
  const statusListenersRef = useRef<Map<string, Set<(txt: string) => void>>>(
    new Map()
  );
  const telemetryListenersRef = useRef<
    Set<(id: string, data: TelemetryData | null) => void>
  >(new Set());

  // Journal DataTracks
  const dataLinesRef = useRef<string[]>([]);
  const dataCountRef = useRef(0);
  const lastSampledLogRef = useRef(0);
  const logTimerRef = useRef(0);
  const replayInterceptorRef = useRef<
    ((payload: Uint8Array | ArrayBuffer, participant?: Participant) => boolean) | null
  >(null);

  // États React discrets
  const [connected, setConnected] = useState(false);
  const [joining, setJoining] = useState(false);
  const [activeRoomName, setActiveRoomName] = useState("");
  const [status, setStatusState] = useState<{ text: string; kind: "" | "ok" | "err" }>({
    text: "",
    kind: "",
  });
  const [videoCards, setVideoCards] = useState<VideoCardEntry[]>([]);
  const [soloEmitters, setSoloEmitters] = useState<string[]>([]);
  const [participants, setParticipants] = useState<RoomParticipantSummary[]>([]);
  const [localCameraTrack, setLocalCameraTrack] = useState<LocalTrack | null>(null);
  const [camEnabled, setCamEnabled] = useState(false);
  const [micEnabled, setMicEnabled] = useState(false);
  const [canPlaybackAudio, setCanPlaybackAudio] = useState(true);
  const [postureMap, setPostureMap] = useState<Record<string, PostureDisplay>>({});
  const [roleMap, setRoleMap] = useState<Record<string, string>>({});
  const [nameMap, setNameMap] = useState<Record<string, string>>({});
  const [localRole, setLocalRole] = useState<string>("");
  const [qualityMap, setQualityMap] = useState<Record<string, ConnectionQuality>>({});
  const [micInfoMap, setMicInfoMap] = useState<Record<string, MicInfo>>({});
  const [avatarActiveMap, setAvatarActiveMap] = useState<Record<string, boolean>>({});
  const [globalModels, setGlobalModels] = useState<AvatarModelInfo[]>([]);
  const [globalModelCurrent, setGlobalModelCurrent] = useState("");
  const [dataLog, setDataLog] = useState<DataLogState>({ lines: [], totalCount: 0 });
  const [isRecording, setIsRecording] = useState(false);
  const [recordingBusy, setRecordingBusy] = useState(false);

  const setStatus = useCallback((text: string, kind: "" | "ok" | "err" = "") => {
    setStatusState({ text, kind });
  }, []);

  const flushDataLog = useCallback(() => {
    logTimerRef.current = 0;
    setDataLog({
      lines: [...dataLinesRef.current],
      totalCount: dataCountRef.current,
    });
  }, []);

  const logData = useCallback(
    (
      payload: ArrayBuffer | Uint8Array,
      who: string,
      topic: string | undefined,
      text: string,
      sampled = false
    ) => {
      dataCountRef.current += 1;
      const now = performance.now();
      if (!(sampled && now - lastSampledLogRef.current < 500)) {
        if (sampled) lastSampledLogRef.current = now;
        const t = new Date().toLocaleTimeString();
        const byteLen = payload instanceof ArrayBuffer ? payload.byteLength : payload.byteLength;
        dataLinesRef.current.push(
          `${t} [${topic || "(sans topic)"}] ${who} (${byteLen} o)\n${text}`
        );
        if (dataLinesRef.current.length > DATA_LINES_MAX) {
          dataLinesRef.current.shift();
        }
      }
      if (!logTimerRef.current) {
        logTimerRef.current = window.setTimeout(flushDataLog, 250);
      }
    },
    [flushDataLog]
  );

  const clearDataLog = useCallback(() => {
    dataLinesRef.current = [];
    dataCountRef.current = 0;
    lastSampledLogRef.current = 0;
    window.clearTimeout(logTimerRef.current);
    flushDataLog();
  }, [flushDataLog]);

  const participantOf = useCallback((id: string): Participant | null => {
    const r = roomRef.current;
    if (!r) return null;
    if (r.localParticipant?.identity === id) return r.localParticipant;
    return r.getParticipantByIdentity?.(id) ?? r.remoteParticipants.get(id) ?? null;
  }, []);

  const syncSoloEmitters = useCallback(() => {
    const r = roomRef.current;
    if (!r) {
      setSoloEmitters((prev) => (prev.length ? [] : prev));
      return;
    }
    const now = performance.now();
    const emitters: string[] = [];
    const seen = new Set<string>();

    for (const id of lastPosturesRef.current.keys()) {
      const st = postureStatesRef.current.get(id);
      if (st && now - st.lastAt < 6000) seen.add(id);
    }
    for (const id of lastTelemetryRef.current.keys()) {
      if (now - (lastTelemetryAtRef.current.get(id) ?? -Infinity) < 6000) {
        seen.add(id);
      }
    }
    for (const id of seen) {
      if (!isSystemParticipant(id) && !videoSlotsRef.current.has(id) && participantOf(id)) {
        emitters.push(id);
      }
    }
    emitters.sort();
    setSoloEmitters((prev) =>
      prev.length === emitters.length && prev.every((v, i) => v === emitters[i])
        ? prev
        : emitters
    );
  }, [participantOf]);

  const syncAvatarFlag = useCallback(
    (id: string, now = performance.now()) => {
      const slot = videoSlotsRef.current.get(id);
      if (!slot) return;
      const active =
        webPoseEnabled ||
        isPostureActiveForTwin(postureStatesRef.current.get(id), now);
      if (slot.avatarActive === active) return;
      slot.avatarActive = active;
      setAvatarActiveMap((prev) =>
        prev[id] === active ? prev : { ...prev, [id]: active }
      );
    },
    [webPoseEnabled]
  );

  const renderParticipantsList = useCallback(() => {
    const r = roomRef.current;
    if (!r) {
      setParticipants([]);
      return;
    }
    const all: Participant[] = [
      r.localParticipant,
      ...Array.from(r.remoteParticipants.values()),
    ].filter((p) => !isSystemParticipant(p.identity || p.name));
    const nextNames: Record<string, string> = {};
    const nextRoles: Record<string, string> = {};

    const myRole = parseParticipantMetaRole(r.localParticipant);
    if (myRole) setLocalRole(myRole);

    const next: RoomParticipantSummary[] = all.map((p) => {
      const id = p.identity || p.name || "inconnu";
      const baseId = id.split(":")[0];
      const displayName =
        (p.name && p.name.trim()) || baseId || id || "inconnu";
      if (p.name && p.name.trim()) {
        nextNames[id] = p.name.trim();
        if (baseId) nextNames[baseId] = p.name.trim();
      }
      const metaRole = parseParticipantMetaRole(p);
      if (metaRole) {
        nextRoles[id] = metaRole;
      }

      const mic = p.getTrackPublication(Track.Source.Microphone);
      const micLabel = !mic
        ? "🎙 aucun micro publié"
        : mic.isMuted
          ? "🔇 micro coupé"
          : p === r.localParticipant
            ? "🎙 micro actif"
            : mic.isSubscribed
              ? "🔊 reçu"
              : "⏳ non reçu";
      return {
        identity: id,
        name: displayName,
        isLocal: p === r.localParticipant,
        isSpeaking: Boolean(p.isSpeaking),
        micLabel,
        hasVideoCard: videoSlotsRef.current.has(p.identity),
      };
    });
    if (Object.keys(nextNames).length > 0) {
      setNameMap((prev) => ({ ...prev, ...nextNames }));
    }
    if (Object.keys(nextRoles).length > 0) {
      setRoleMap((prev) => ({ ...prev, ...nextRoles }));
    }
    setParticipants(next);
  }, []);

  const syncButtonsState = useCallback(() => {
    const r = roomRef.current;
    setCamEnabled(Boolean(r?.localParticipant?.isCameraEnabled));
    setMicEnabled(Boolean(r?.localParticipant?.isMicrophoneEnabled));
  }, []);

  const sendPoseToAvatar = useCallback(
    (id: string, pkt: Extract<ReturnType<typeof decodePosture>, { lost: false }>, tLocal: number, now: number) => {
      if (webPoseEnabled) return;
      const slot = videoSlotsRef.current.get(id);
      if (!slot || !slot.frameReady || !slot.iframeWindow) return;

      const lm: Array<[number, number, number, number]> = new Array(33);
      for (let i = 0; i < 33; i++) {
        lm[i] = [
          pkt.pts[i * 3],
          pkt.pts[i * 3 + 1],
          pkt.pts.depth ? pkt.pts.depth[i] : 0,
          pkt.pts[i * 3 + 2],
        ];
      }
      const s = postureStatesRef.current.get(id);
      const worldCapable = avatarPage.startsWith("avatar-v2");
      if (pkt.pts.world && !worldCapable) {
        const P = pkt.pts;
        const W = pkt.pts.world;
        const dyImg = Math.abs(
          (P[11 * 3 + 1] + P[12 * 3 + 1]) / 2 - (P[23 * 3 + 1] + P[24 * 3 + 1]) / 2
        );
        const dyWorld = Math.abs(
          (W[11 * 3 + 1] + W[12 * 3 + 1]) / 2 - (W[23 * 3 + 1] + W[24 * 3 + 1]) / 2
        );
        const k = dyWorld > 0.05 ? dyImg / dyWorld : 0;
        const aspect = s && s.frameW && s.frameH ? s.frameW / s.frameH : 0.75;
        for (let i = 0; i < 33; i++) lm[i][2] = (W[i * 3 + 2] * k) / aspect;
      }
      if (pkt.pts.world && worldCapable) {
        const world: Array<[number, number, number]> = new Array(33);
        for (let i = 0; i < 33; i++) {
          world[i] = [
            pkt.pts.world[i * 3],
            pkt.pts.world[i * 3 + 1],
            pkt.pts.world[i * 3 + 2],
          ];
        }
        slot.iframeWindow.postMessage(
          {
            type: "poseWorld",
            parse: true,
            world,
            vis: Array.from({ length: 33 }, (_, i) => pkt.pts[i * 3 + 2]),
            label: pkt.label,
            lag: Math.max(0, now - tLocal),
            delay: slot.delay || 150,
          },
          "*"
        );
        return;
      }
      slot.iframeWindow.postMessage(
        {
          type: "pose",
          lm,
          label: pkt.label,
          w: (s && s.frameW) || 480,
          h: (s && s.frameH) || 640,
          lag: Math.max(0, now - tLocal),
          delay: slot.delay || 150,
        },
        "*"
      );
    },
    [avatarPage, webPoseEnabled]
  );

  const sendWorldToAvatar = useCallback((id: string, r: WebPoseResult) => {
    const slot = videoSlotsRef.current.get(id);
    if (!slot || !slot.frameReady || !slot.iframeWindow) return;
    slot.webPoseN = (slot.webPoseN || 0) + 1;
    const now = performance.now();
    if (!slot.webPoseAt || now - slot.webPoseAt > 1000) {
      slot.webPoseHz =
        slot.webPoseN / ((now - (slot.webPoseAt || now - 1000)) / 1000);
      slot.webPoseN = 0;
      slot.webPoseAt = now;
    }
    slot.webDetectMs = slot.webDetectMs
      ? slot.webDetectMs + (r.detectMs - slot.webDetectMs) * 0.1
      : r.detectMs;
    slot.iframeWindow.postMessage(
      {
        type: "poseWorld",
        parse: true,
        world: r.world,
        vis: r.vis,
        label: postureStatesRef.current.get(id)?.label || undefined,
        delay: 90,
      },
      "*"
    );
  }, []);

  const handlePosture = useCallback(
    (
      participant: { identity?: string } | undefined,
      pkt: NonNullable<ReturnType<typeof decodePosture>>,
      now: number
    ) => {
      const id = participant?.identity || "inconnu";
      let s = postureStatesRef.current.get(id);
      if (!s) {
        s = newPostureState();
        postureStatesRef.current.set(id, s);
      }

      if (
        s.lastSeq !== null &&
        now - s.lastAt < 1000 &&
        !isNewerSeq(pkt.seq, s.lastSeq)
      ) {
        return;
      }

      if (s.lossT == null || now - s.lastAt >= 1000) {
        s.lossT = now;
        s.rx = 0;
        s.exp = 0;
      } else if (s.lastSeq !== null) {
        s.exp = (s.exp ?? 0) + ((pkt.seq - s.lastSeq) & 0xffff);
      }
      s.rx = (s.rx ?? 0) + 1;
      if (now - s.lossT >= 1000) {
        const sec = (now - s.lossT) / 1000;
        s.rxRate = s.rx / sec;
        s.txRate = ((s.exp ?? 0) + 1) / sec;
        s.lossT = now;
        s.rx = 0;
        s.exp = 0;
      }
      s.lastSeq = pkt.seq;

      if (pkt.lost) {
        s.lastAt = now;
        s.lost = true;
        s.buf.length = 0;
        s.count = 0;
        s.label = null;
        s.shownLabel = null;
        const disp = { label: "pose perdue", confidence: null, count: 0 };
        lastPosturesRef.current.set(id, disp);
        setPostureMap((prev) => ({ ...prev, [id]: disp }));
        syncSoloEmitters();
        syncAvatarFlag(id, now);
        return;
      }

      const prev = s.buf[s.buf.length - 1];
      if (!s.lost && prev) {
        const dt = now - prev.a;
        if (dt < 500) s.interval += (dt - s.interval) * 0.1;
      }
      s.lastAt = now;
      s.lastValidAt = now;
      s.lost = false;
      s.label = pkt.label;
      s.confidence = pkt.confidence;
      s.count = pkt.count;
      if (pkt.frameWidth > 0 && pkt.frameHeight > 0) {
        s.frameW = pkt.frameWidth;
        s.frameH = pkt.frameHeight;
      }

      const age = ((Date.now() & 0xffff) - pkt.timestamp) & 0xffff;
      if (age < 5000) s.age = s.age == null ? age : s.age + (age - s.age) * 0.1;

      s.relAcc =
        s.relPrev == null
          ? pkt.timestamp
          : (s.relAcc ?? 0) + ((pkt.timestamp - s.relPrev) & 0xffff);
      s.relPrev = pkt.timestamp;
      const rawLag = now - s.relAcc;
      s.rawMin = s.rawMin == null ? rawLag : Math.min(rawLag, s.rawMin + 0.2);
      s.ageRel =
        s.ageRel == null
          ? rawLag - s.rawMin
          : s.ageRel + (rawLag - s.rawMin - s.ageRel) * 0.1;

      const tLocal = poseLocalTime(s, pkt.timestamp, now);
      s.buf.push({ t: tLocal, a: now, pts: pkt.pts });
      while (s.buf.length > 12) s.buf.shift();

      syncAvatarFlag(id, now);
      sendPoseToAvatar(id, pkt, tLocal, now);

      if (s.shownLabel !== pkt.label || now - s.uiAt > 250) {
        s.uiAt = now;
        s.shownLabel = pkt.label;
        const disp = {
          label: pkt.label,
          confidence: pkt.confidence,
          count: pkt.count,
        };
        lastPosturesRef.current.set(id, disp);
        setPostureMap((prev) => ({ ...prev, [id]: disp }));
        syncSoloEmitters();
      }
    },
    [sendPoseToAvatar, syncAvatarFlag, syncSoloEmitters]
  );

  const handleDataReceived = useCallback(
    (
      payload: Uint8Array | ArrayBuffer,
      participant?: { identity?: string },
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic === "workout_step") {
        const bytes = asBytes(payload);
        if (bytes) onWorkoutStepRef.current?.(bytes);
        return;
      }

      const bytes = asBytes(payload);
      const who = participant?.identity || "serveur";

      if (topic === "posture") {
        if (
          replayInterceptorRef.current &&
          replayInterceptorRef.current(payload, participant as Participant | undefined)
        ) {
          return;
        }
        const pkt = decodePosture(payload, gravityAlign);
        if (!pkt) {
          logData(
            payload,
            who,
            topic,
            "(paquet posture invalide ou version inconnue)",
            true
          );
          return;
        }
        logData(
          payload,
          who,
          topic,
          pkt.lost
            ? `v6 · seq ${pkt.seq} · pose perdue`
            : `v${pkt.version} · seq ${pkt.seq} · ${pkt.label} · ${pkt.count} pts`,
          true
        );
        handlePosture(participant, pkt, performance.now());
        return;
      }

      if (topic === "coach_ia") {
        const msg = parseCoachIa(bytes || payload);
        if (!msg) {
          logData(payload, who, topic, "(message coach_ia invalide)");
          return;
        }
        const decodedTxt = new TextDecoder().decode(bytes || payload);
        logData(payload, who, topic, decodedTxt, msg.kind === "state");
        iaListenersRef.current.get(msg.trainee)?.forEach((cb) => cb(msg));
        return;
      }

      let text = "";
      try {
        text = new TextDecoder().decode(bytes || payload);
      } catch {
        text = "(binaire)";
      }
      const isBinaryTel =
        topic === "telemetry" &&
        bytes &&
        (bytes[0] === 1 || bytes[0] === 2 || bytes[0] === 3);
      logData(
        payload,
        who,
        topic,
        isBinaryTel ? `Données binaires v${bytes[0]}` : text
      );

      if (topic !== "telemetry") return;
      const id = participant?.identity || "inconnu";

      const bin = decodeTelemetry(payload);
      if (bin) {
        const tel = binaryTelemetryToData(bin);
        lastTelemetryAtRef.current.set(id, performance.now());
        lastTelemetryRef.current.set(id, tel);
        telemetryListenersRef.current.forEach((cb) => cb(id, tel));
        if (tel.role) setRoleMap((prev) => (prev[id] === tel.role ? prev : { ...prev, [id]: tel.role! }));
        syncSoloEmitters();
        return;
      }

      try {
        const msg = JSON.parse(text) as { type?: string; payload?: TelemetryData };
        if (msg.type !== "telemetry") return;
        const tel = msg.payload || {};
        lastTelemetryAtRef.current.set(id, performance.now());
        lastTelemetryRef.current.set(id, tel);
        telemetryListenersRef.current.forEach((cb) => cb(id, tel));
        if (tel.role) setRoleMap((prev) => (prev[id] === tel.role ? prev : { ...prev, [id]: tel.role! }));
        syncSoloEmitters();
      } catch (e) {
        console.warn("Télémétrie illisible:", e);
      }
    },
    [gravityAlign, handlePosture, logData, syncSoloEmitters]
  );

  const openVideoCard = useCallback(
    (track: RemoteVideoTrack, participant: RemoteParticipant) => {
      const id = participant.identity;
      const existing = videoSlotsRef.current.get(id);
      if (existing) {
        existing.stopWebPose?.();
        videoSlotsRef.current.delete(id);
      }

      const slot: VideoSlotRef = {
        video: null,
        canvas: null,
        track,
        vDelay: 0,
        delay: 0,
        lastFrameAt: performance.now(),
        frameReady: false,
        iframeWindow: null,
        avatarActive: null,
        statusAt: 0,
        lastStatusText: "Posture en attente : aucun flux de posture reçu.",
      };
      videoSlotsRef.current.set(id, slot);
      syncAvatarFlag(id, performance.now());

      setVideoCards((prev) => [
        ...prev.filter((c) => c.identity !== id),
        {
          identity: id,
          track,
          trackSid: track.sid || "",
          connectionQuality: participant.connectionQuality,
        },
      ]);
      setQualityMap((prev) => ({
        ...prev,
        [id]: participant.connectionQuality,
      }));
      syncSoloEmitters();
    },
    [syncAvatarFlag, syncSoloEmitters]
  );

  const closeVideoCard = useCallback(
    (id: string) => {
      const slot = videoSlotsRef.current.get(id);
      if (!slot) return;
      slot.stopWebPose?.();
      try {
        if (slot.video) slot.track.detach(slot.video);
      } catch {
        // déjà détachée
      }
      videoSlotsRef.current.delete(id);
      setVideoCards((prev) => prev.filter((c) => c.identity !== id));
      setAvatarActiveMap((prev) => {
        if (!(id in prev)) return prev;
        const copy = { ...prev };
        delete copy[id];
        return copy;
      });
      syncSoloEmitters();
    },
    [syncSoloEmitters]
  );

  const cleanupRoomState = useCallback(() => {
    for (const slot of videoSlotsRef.current.values()) {
      slot.stopWebPose?.();
    }
    videoSlotsRef.current.clear();
    stalledVideoRef.current.clear();
    postureStatesRef.current.clear();
    lastPosturesRef.current.clear();
    lastTelemetryRef.current.clear();
    lastTelemetryAtRef.current.clear();
    telemetryListenersRef.current.forEach((cb) => cb("*", null));

    if (audioHostRef.current) audioHostRef.current.innerHTML = "";
    dataLinesRef.current = [];
    dataCountRef.current = 0;
    lastSampledLogRef.current = 0;
    window.clearTimeout(logTimerRef.current);
    logTimerRef.current = 0;

    setDataLog({ lines: [], totalCount: 0 });
    setVideoCards([]);
    setSoloEmitters([]);
    setParticipants([]);
    setPostureMap({});
    setRoleMap({});
    setNameMap({});
    setLocalRole("");
    setQualityMap({});
    setMicInfoMap({});
    setAvatarActiveMap({});
    setGlobalModels([]);
    setGlobalModelCurrent("");
    setLocalCameraTrack(null);
    setCamEnabled(false);
    setMicEnabled(false);
    setCanPlaybackAudio(true);
    setIsRecording(false);
    setConnected(false);
    setActiveRoomName("");
    roomRef.current = null;
  }, []);

  const joinRoom = useCallback(
    async (roomName: string, useCamera: boolean) => {
      const user = auth.currentUser;
      if (!user) {
        throw new Error(
          "Non connecté. Connectez-vous d'abord sur le back-office (/login)."
        );
      }

      setJoining(true);
      setStatus("Récupération du token…");
      try {
        const idToken = await user.getIdToken();
        const res = await fetch("/api/token", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${idToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ room: roomName, client: "web" }),
        });
        if (res.status === 401)
          throw new Error("Authentification refusée par l’API (ID token invalide ou expiré)");
        if (res.status === 403)
          throw new Error("Vous n'êtes pas autorisé à rejoindre cette session.");
        if (res.status === 404)
          throw new Error("Session introuvable ou déjà terminée.");
        if (res.status === 410)
          throw new Error(
            "L'horaire de cette session est dépassé et aucun participant n'est connecté."
          );
        if (!res.ok) throw new Error(`Token HTTP ${res.status}`);

        const data = (await res.json()) as { token?: string; url?: string };
        if (!data.token) throw new Error("Réponse sans token");

        let lkUrl = data.url || WS_URL_FALLBACK;
        if (
          lkUrl.includes("10.0.2.2") &&
          (window.location.hostname === "localhost" ||
            window.location.hostname === "127.0.0.1")
        ) {
          lkUrl = lkUrl.replace("10.0.2.2", window.location.hostname);
        }

        const room = new Room({ adaptiveStream: false });
        roomRef.current = room;
        attachConnLog(room, () => videoSlotsRef.current.size);
        connLog("join : début", { salle: roomName });

        room
          .on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
            if (track.kind === Track.Kind.Audio && audioHostRef.current) {
              audioHostRef.current.append(track.attach());
            }
            if (
              track.kind === Track.Kind.Video &&
              track instanceof RemoteVideoTrack
            ) {
              stalledVideoRef.current.delete(participant.identity);
              openVideoCard(track, participant);
            }
            renderParticipantsList();
          })
          .on(RoomEvent.TrackUnsubscribed, (track, _pub, participant) => {
            track.detach().forEach((el) => el.remove());
            if (track.kind === Track.Kind.Video && participant) {
              stalledVideoRef.current.delete(participant.identity);
              const slot = videoSlotsRef.current.get(participant.identity);
              if (slot && slot.track.sid === track.sid) {
                closeVideoCard(participant.identity);
              }
            }
            renderParticipantsList();
          })
          .on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
            handleDataReceived(payload, participant, kind, topic);
          })
          .on(RoomEvent.TrackPublished, renderParticipantsList)
          .on(RoomEvent.TrackMuted, (pub, participant) => {
            if (
              participant === room.localParticipant &&
              pub.source === Track.Source.Camera
            ) {
              setLocalCameraTrack(null);
            }
            syncButtonsState();
            renderParticipantsList();
          })
          .on(RoomEvent.TrackUnmuted, (pub, participant) => {
            if (
              participant === room.localParticipant &&
              pub.source === Track.Source.Camera &&
              pub.track
            ) {
              setLocalCameraTrack(pub.track as LocalTrack);
            }
            const stalled = stalledVideoRef.current.get(participant.identity);
            if (
              stalled &&
              pub.track &&
              pub.kind === Track.Kind.Video &&
              pub.track instanceof RemoteVideoTrack
            ) {
              stalledVideoRef.current.delete(participant.identity);
              openVideoCard(pub.track, participant as RemoteParticipant);
            }
            syncButtonsState();
            renderParticipantsList();
          })
          .on(RoomEvent.LocalTrackPublished, (pub) => {
            if (pub.track && pub.source === Track.Source.Camera) {
              setLocalCameraTrack(pub.track as LocalTrack);
            }
            syncButtonsState();
            renderParticipantsList();
          })
          .on(RoomEvent.LocalTrackUnpublished, (pub) => {
            if (pub.source === Track.Source.Camera) {
              setLocalCameraTrack(null);
            }
            syncButtonsState();
            renderParticipantsList();
          })
          .on(RoomEvent.AudioPlaybackStatusChanged, () => {
            setCanPlaybackAudio(room.canPlaybackAudio);
            if (!room.canPlaybackAudio) {
              setStatus(
                "Audio bloqué par le navigateur : clique sur « Activer le son ».",
                "err"
              );
            }
          })
          .on(RoomEvent.ParticipantConnected, renderParticipantsList)
          .on(RoomEvent.ParticipantMetadataChanged, renderParticipantsList)
          .on(RoomEvent.ParticipantNameChanged, renderParticipantsList)
          .on(RoomEvent.ParticipantDisconnected, (participant) => {
            const pid = participant.identity;
            stalledVideoRef.current.delete(pid);
            lastPosturesRef.current.delete(pid);
            lastTelemetryRef.current.delete(pid);
            lastTelemetryAtRef.current.delete(pid);
            postureStatesRef.current.delete(pid);
            telemetryListenersRef.current.forEach((cb) => cb(pid, null));
            closeVideoCard(pid);
            setPostureMap((prev) => {
              const copy = { ...prev };
              delete copy[pid];
              return copy;
            });
            syncSoloEmitters();
            renderParticipantsList();
          })
          .on(RoomEvent.ActiveSpeakersChanged, renderParticipantsList)
          .on(RoomEvent.ConnectionQualityChanged, (quality, p) => {
            if (p?.identity) {
              setQualityMap((prev) => ({ ...prev, [p.identity]: quality }));
            }
          })
          .on(RoomEvent.Disconnected, () => {
            cleanupRoomState();
            setStatus("Session déconnectée.");
            onDisconnectedRef.current?.();
          });

        setStatus("Connexion à LiveKit…");
        await room.connect(lkUrl, data.token);
        connLog("join : connecté", {
          ...connSnap(room, videoSlotsRef.current.size),
          identite: room.localParticipant.identity,
        });
        await room.startAudio().catch(() => {});
        setCanPlaybackAudio(room.canPlaybackAudio);

        let statusErr = false;
        if (useCamera) {
          try {
            await room.localParticipant.setCameraEnabled(true, {
              resolution: { width: 640, height: 480, frameRate: 24 },
            });
          } catch (e) {
            statusErr = true;
            setStatus(
              "Connecté (webcam indisponible : " +
                (e instanceof Error ? e.message : String(e)) +
                ")",
              "err"
            );
          }
        }
        try {
          await room.localParticipant.setMicrophoneEnabled(true);
        } catch (e) {
          console.warn("Micro indisponible:", e);
        }

        syncButtonsState();
        setActiveRoomName(roomName);
        setConnected(true);
        renderParticipantsList();
        if (!statusErr) setStatus("Connecté.", "ok");

        // État initial de l'enregistrement Egress
        try {
          const recRes = await fetch(
            `/api/mobile/rooms/${encodeURIComponent(roomName)}/recording`,
            { headers: { Authorization: `Bearer ${idToken}` } }
          );
          if (recRes.ok) {
            const recData = (await recRes.json()) as { status?: string };
            setIsRecording(
              ["recording", "starting", "EGRESS_STARTING", "EGRESS_ACTIVE"].includes(
                recData.status || ""
              )
            );
          }
        } catch {
          // ignore
        }
      } catch (err) {
        if (roomRef.current) {
          try {
            await roomRef.current.disconnect();
          } catch {
            // ignore
          }
        }
        cleanupRoomState();
        throw err;
      } finally {
        setJoining(false);
      }
    },
    [
      cleanupRoomState,
      closeVideoCard,
      handleDataReceived,
      openVideoCard,
      renderParticipantsList,
      setStatus,
      syncButtonsState,
      syncSoloEmitters,
    ]
  );

  const leaveRoom = useCallback(async () => {
    const r = roomRef.current;
    if (r) {
      await r.disconnect();
    } else {
      cleanupRoomState();
    }
  }, [cleanupRoomState]);

  const toggleMic = useCallback(async () => {
    const r = roomRef.current;
    if (!r) return;
    try {
      await r.localParticipant.setMicrophoneEnabled(
        !r.localParticipant.isMicrophoneEnabled
      );
    } catch (e) {
      setStatus(
        "Micro indisponible : " + (e instanceof Error ? e.message : String(e)),
        "err"
      );
    }
    syncButtonsState();
  }, [setStatus, syncButtonsState]);

  const toggleCam = useCallback(async () => {
    const r = roomRef.current;
    if (!r) return;
    try {
      const next = !r.localParticipant.isCameraEnabled;
      await r.localParticipant.setCameraEnabled(
        next,
        next
          ? { resolution: { width: 640, height: 480, frameRate: 24 } }
          : undefined
      );
      if (!next) setLocalCameraTrack(null);
    } catch (e) {
      setStatus(
        "Webcam indisponible : " + (e instanceof Error ? e.message : String(e)),
        "err"
      );
    }
    syncButtonsState();
  }, [setStatus, syncButtonsState]);

  const startSound = useCallback(async () => {
    const r = roomRef.current;
    if (!r) return;
    await r.startAudio();
    audioHostRef.current?.querySelectorAll("audio").forEach((a) => {
      a.play().catch(() => {});
    });
    setCanPlaybackAudio(true);
    setStatus("Son activé.", "ok");
  }, [setStatus]);

  const toggleRecording = useCallback(async () => {
    if (!activeRoomName || recordingBusy) return;
    const user = auth.currentUser;
    if (!user) return;
    setRecordingBusy(true);
    try {
      const idToken = await user.getIdToken();
      const action = isRecording ? "stop" : "start?layout=grid";
      const res = await fetch(
        `/api/mobile/rooms/${encodeURIComponent(activeRoomName)}/recording/${action}`,
        { method: "POST", headers: { Authorization: `Bearer ${idToken}` } }
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const next = !isRecording;
      setIsRecording(next);
      setStatus(
        next ? "Enregistrement démarré." : "Enregistrement arrêté.",
        "ok"
      );
    } catch (e) {
      setStatus(
        "Enregistrement : " + (e instanceof Error ? e.message : String(e)),
        "err"
      );
    } finally {
      setRecordingBusy(false);
    }
  }, [activeRoomName, isRecording, recordingBusy, setStatus]);

  const selectGlobalModel = useCallback((modelKey: string) => {
    setGlobalModelCurrent(modelKey);
    try {
      localStorage.setItem("glbModel", modelKey);
    } catch {
      // ignore
    }
    for (const [id, slot] of videoSlotsRef.current) {
      let own = "";
      try {
        own = localStorage.getItem("glbModel:" + id) || "";
      } catch {
        // ignore
      }
      if (!own && slot.iframeWindow) {
        slot.iframeWindow.postMessage({ type: "setModel", key: modelKey }, "*");
      }
    }
  }, []);

  // Hooks globaux connlog + nettoyage à la sortie
  useEffect(() => {
    const stopHooks = initConnLogHooks();
    return () => {
      stopHooks();
      window.clearTimeout(logTimerRef.current);
      if (roomRef.current) {
        roomRef.current.disconnect().catch(() => {});
      }
    };
  }, []);

  // Boucle 60 fps : interpolation squelette, statut synchro, transition Twin 3D
  useEffect(() => {
    if (!connected) return;
    let raf = 0;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      for (const [id, slot] of videoSlotsRef.current) {
        syncAvatarFlag(id, now);
        const s = postureStatesRef.current.get(id);
        let pts: PosturePts | null = null;
        if (s && !s.lost && s.buf.length && now - s.lastAt <= STALE_MS) {
          const margin = lowLatencyRef.current
            ? Math.min(Math.max(0.3 * s.interval + 1.0 * (s.jit || 0), 30), 120)
            : Math.min(Math.max(s.interval + 2 * (s.jit || 0), 30), 250);
          const want = Math.max(slot.vDelay || 0, margin);
          slot.delay = slot.delay ? slot.delay + (want - slot.delay) * 0.05 : want;
          s.delayShown = slot.delay;
          pts = smoothPose(s, samplePose(s, now - slot.delay), now);
        }
        drawSkeletonOnCanvas(slot, s, pts);

        if (now - slot.statusAt >= 500) {
          slot.statusAt = now;
          let text: string;
          if (!s) {
            text = "Posture en attente : aucun flux de posture reçu.";
          } else if (s.lost) {
            text = "Pose perdue : aucune personne détectée côté mobile.";
          } else if (now - s.lastAt > STALE_MS) {
            text = `Flux de posture interrompu (${((now - s.lastAt) / 1000).toFixed(1)} s).`;
          } else {
            text = `Posture : ${s.label} · ${s.count} points · ${Math.round(1000 / s.interval)} paquets/s`;
            const vid = slot.vDelay ? `${Math.round(slot.vDelay)} ms` : "n/a";
            text += ` | sync : squelette retardé de ${Math.round(s.delayShown || 0)} ms · lecture vidéo ${vid} · jitter ${Math.round(s.jit || 0)} ms${s.tsOk ? "" : " · horodatage ignoré"}`;
            if (s.rxRate != null && s.txRate) {
              text += ` | reçus ${s.rxRate.toFixed(1)}/s sur ${s.txRate.toFixed(1)}/s émis (perte ${Math.max(0, Math.round(100 * (1 - s.rxRate / s.txRate)))} %)`;
            }
            if (s.age != null) text += ` | âge pose ≈ ${Math.round(s.age)} ms`;
            if (debugMode && s.buf.length) {
              const p = s.buf[s.buf.length - 1].pts;
              const f = (t: number) =>
                `${p[t * 3].toFixed(2)},${p[t * 3 + 1].toFixed(2)}@${p[t * 3 + 2].toFixed(2)}`;
              text += ` | frame ${s.frameW}x${s.frameH} | épaule G ${f(11)} | hanche G ${f(23)}`;
            }
          }
          if (webPoseEnabled) {
            text += slot.webPoseError
              ? ` | pose navigateur en erreur : ${slot.webPoseError}`
              : slot.webPoseHz
                ? ` | pose navigateur (${webPoseModelName}) : ${slot.webPoseHz.toFixed(1)} poses/s · détection ${Math.round(slot.webDetectMs || 0)} ms`
                : " | pose navigateur : démarrage (chargement du modèle)…";
          }
          if (slot.lastStatusText !== text) {
            slot.lastStatusText = text;
            statusListenersRef.current.get(id)?.forEach((cb) => cb(text));
          }
        }
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [connected, debugMode, syncAvatarFlag, webPoseEnabled, webPoseModelName]);

  // Boucle 1 Hz (stats WebRTC, délai vidéo, réseau, stall check) + 5 Hz niveaux micro
  useEffect(() => {
    if (!connected) return;

    const updateVideoDelay = async (slot: VideoSlotRef) => {
      try {
        const rep = await slot.track.receiver?.getStats();
        rep?.forEach((st: Record<string, unknown>) => {
          if (st.type !== "inbound-rtp" || st.kind !== "video") return;
          const p = slot.vstats;
          const cur = {
            jbd: Number(st.jitterBufferDelay || 0),
            jbe: Number(st.jitterBufferEmittedCount || 0),
            dec: Number(st.totalDecodeTime || 0),
            fr: Number(st.framesDecoded || 0),
          };
          slot.vstats = cur;
          if (!p) return;
          const de = cur.jbe - p.jbe;
          const df = cur.fr - p.fr;
          if (de <= 0 || df <= 0) return;
          const ms = ((cur.jbd - p.jbd) / de + (cur.dec - p.dec) / df) * 1000;
          if (Number.isFinite(ms)) slot.vDelay = Math.max(0, Math.min(ms, 800));
        });
      } catch {
        // ignore
      }
    };

    const poseSample = (id: string): NetSample => {
      const sample: NetSample = {};
      const s = postureStatesRef.current.get(id);
      const now = performance.now();
      if (s) {
        const live = !s.lost && now - s.lastAt <= 2000;
        sample.poseHz = live ? (s.rxRate ?? (s.interval ? 1000 / s.interval : null)) : 0;
        if (live && s.age != null) sample.poseAge = s.age;
        else if (live && s.ageRel != null) {
          sample.poseAge = s.ageRel;
          sample.poseAgeSub = "relatif (horloges non synchronisées)";
        }
        if (live && s.rxRate != null && s.txRate) {
          sample.poseSub = `émis ${s.txRate.toFixed(0)}/s · perte ${Math.max(0, Math.round(100 * (1 - s.rxRate / s.txRate)))} %`;
        }
      }
      return sample;
    };

    const measureRtt = async (): Promise<number | null> => {
      const r = roomRef.current as
        | (Room & {
            engine?: {
              pcManager?: {
                subscriber?: { getStats?: () => Promise<RTCStatsReport> };
                publisher?: { getStats?: () => Promise<RTCStatsReport> };
              };
              client?: { rtt?: number };
            };
          })
        | null;
      const pm = r?.engine?.pcManager;
      const sources = [
        pm?.subscriber,
        pm?.publisher,
        ...Array.from(videoSlotsRef.current.values()).map((v) => v.track.receiver),
      ];
      for (const src of sources) {
        if (!src?.getStats) continue;
        try {
          const statsPromise = src.getStats();
          if (!statsPromise) continue;
          const ms = pairRtt(await withTimeout(statsPromise));
          if (ms) return ms;
        } catch {
          // source indisponible
        }
      }
      const sig = r?.engine?.client?.rtt;
      return sig && sig > 0 ? sig : null;
    };

    const secTimer = window.setInterval(async () => {
      syncSoloEmitters();

      for (const slot of videoSlotsRef.current.values()) {
        void updateVideoDelay(slot);
      }

      const rtt = await measureRtt();
      if (rtt) {
        rttStateRef.current.last = rtt;
        rttStateRef.current.fails = 0;
      } else if (++rttStateRef.current.fails > 5) {
        rttStateRef.current.last = null;
      }
      const rttVal = rttStateRef.current.last;

      // Émetteurs sans vidéo
      for (const id of netListenersRef.current.keys()) {
        if (videoSlotsRef.current.has(id)) continue;
        const sample = poseSample(id);
        if (rttVal) sample.rtt = rttVal;
        netListenersRef.current.get(id)?.forEach((cb) => cb(sample));
      }

      // Cartes vidéo
      for (const [id, slot] of videoSlotsRef.current) {
        const sample = poseSample(id);
        try {
          const report = slot.track.receiver
            ? await withTimeout(slot.track.receiver.getStats())
            : null;
          let inb: Record<string, unknown> | null = null;
          report?.forEach((r: Record<string, unknown>) => {
            if (r.type === "inbound-rtp" && (r.kind || r.mediaType) === "video") {
              inb = r;
            }
          });
          if (inb) {
            const rec = inb as Record<string, unknown>;
            const ts = Number(rec.timestamp || 0);
            const bytes = Number(rec.bytesReceived || 0);
            const recv = Number(rec.packetsReceived || 0);
            const lost = Number(rec.packetsLost || 0);
            const frames = Number(rec.framesDecoded || 0);
            const prev = slot.netPrev;
            const dt = prev ? (ts - prev.t) / 1000 : 0;
            if (dt > 0 && prev) {
              sample.kbps = ((bytes - prev.bytes) * 8) / 1000 / dt;
              const dRecv = recv - prev.recv;
              const dLost = Math.max(0, lost - prev.lost);
              sample.loss = dRecv + dLost > 0 ? (100 * dLost) / (dRecv + dLost) : 0;
              sample.fps =
                typeof rec.framesPerSecond === "number"
                  ? rec.framesPerSecond
                  : (frames - prev.frames) / dt;
            }
            if (typeof rec.jitter === "number") sample.jitter = rec.jitter * 1000;
            const grew = prev && (frames > prev.frames || bytes > prev.bytes);
            if (!prev || grew) slot.lastFrameAt = performance.now();
            if (frames > 0) slot.gotFrame = true;
            slot.netPrev = { t: ts, bytes, recv, lost, frames };
          }
          if (rttVal) sample.rtt = rttVal;
        } catch {
          // ignore
        }
        netListenersRef.current.get(id)?.forEach((cb) => cb(sample));
      }

      // Détection arrêt vidéo (checkVideoStall)
      const now = performance.now();
      for (const [id, slot] of [...videoSlotsRef.current]) {
        if (!slot.video) continue;
        const pq = slot.video.getVideoPlaybackQuality?.();
        if (pq) {
          slot.elOk = true;
          if (pq.totalVideoFrames > (slot.elFrames ?? -1)) {
            slot.elFrames = pq.totalVideoFrames;
            slot.lastFrameAt = now;
          }
          if (pq.totalVideoFrames > 0) slot.gotFrame = true;
        }
        const rect = slot.video.getBoundingClientRect();
        const onScreen =
          rect.width > 0 &&
          rect.height > 0 &&
          rect.bottom > 0 &&
          rect.top < window.innerHeight;
        if (document.hidden || !onScreen) slot.lastFrameAt = now;
        const stallMs = slot.gotFrame ? VIDEO_STALL_MS : VIDEO_FIRST_FRAME_MS;
        if ((slot.netPrev || slot.elOk) && now - slot.lastFrameAt > stallMs) {
          console.info(
            "[salon] plus d'images vidéo depuis",
            stallMs,
            "ms : carte fermée pour",
            id
          );
          connLog("carte fermée : plus d'images", {
            id,
            ...connSnap(roomRef.current, videoSlotsRef.current.size),
          });
          const p = participantOf(id);
          if (p && p instanceof RemoteParticipant) {
            stalledVideoRef.current.set(id, {
              track: slot.track,
              participant: p,
            });
          }
          closeVideoCard(id);
        }
      }
    }, 1000);

    // Niveau micro à 5 Hz
    const micTimer = window.setInterval(() => {
      const r = roomRef.current;
      if (!r) return;
      const next: Record<string, MicInfo> = {};
      const ids = new Set<string>(videoSlotsRef.current.keys());
      for (const id of netListenersRef.current.keys()) ids.add(id);
      for (const id of ids) {
        const p = participantOf(id);
        if (!p) continue;
        next[id] = {
          state: micStateOf(p, r.localParticipant),
          level: p.audioLevel || 0,
          speaking: Boolean(p.isSpeaking),
        };
      }
      setMicInfoMap(next);
    }, 200);

    return () => {
      window.clearInterval(secTimer);
      window.clearInterval(micTimer);
    };
  }, [closeVideoCard, connected, participantOf, syncSoloEmitters]);

  // Réception des messages depuis les iframes avatar-v2
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const d = e.data as
        | {
            type?: string;
            models?: AvatarModelInfo[];
            current?: string;
            picked?: boolean;
            credit?: string;
          }
        | undefined;
      if (!d || typeof d !== "object") return;

      if (d.type === "avatar-ready") {
        for (const slot of videoSlotsRef.current.values()) {
          if (slot.iframeWindow && slot.iframeWindow === e.source) {
            slot.frameReady = true;
          }
        }
      } else if (d.type === "avatar-models" && Array.isArray(d.models)) {
        const own = [...videoSlotsRef.current].find(
          ([, v]) => v.iframeWindow && v.iframeWindow === e.source
        );
        if (own) {
          const [id] = own;
          if (d.picked && d.current) {
            try {
              localStorage.setItem("glbModel:" + id, d.current);
            } catch {
              // ignore
            }
          }
        }
        setGlobalModels(d.models);
        let saved = "";
        try {
          saved = localStorage.getItem("glbModel") || "";
        } catch {
          // ignore
        }
        const globalOk = d.models.some((m) => m.key === saved && m.rigged);
        let ownSaved = "";
        if (own) {
          try {
            ownSaved = localStorage.getItem("glbModel:" + own[0]) || "";
          } catch {
            // ignore
          }
        }
        const want = ownSaved || (globalOk ? saved : "");
        const usable = want && d.models.some((m) => m.key === want && m.rigged);
        const cur = globalOk
          ? saved
          : ownSaved
            ? d.models.find((m) => m.rigged)?.key ?? ""
            : d.current || "";
        setGlobalModelCurrent(cur);
        if (usable && want !== d.current && e.source && "postMessage" in e.source) {
          (e.source as Window).postMessage({ type: "setModel", key: want }, "*");
        }
      }
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const hasActiveAvatar = Object.values(avatarActiveMap).some(Boolean);

  return {
    roomRef,
    audioHostRef,
    videoSlotsRef,
    netListenersRef,
    iaListenersRef,
    statusListenersRef,
    telemetryListenersRef,
    lastTelemetryRef,
    replayInterceptorRef,
    connected,
    joining,
    activeRoomName,
    status,
    setStatus,
    videoCards,
    soloEmitters,
    participants,
    localCameraTrack,
    camEnabled,
    micEnabled,
    canPlaybackAudio,
    postureMap,
    roleMap,
    nameMap,
    localRole,
    qualityMap,
    micInfoMap,
    avatarActiveMap,
    hasActiveAvatar,
    globalModels,
    globalModelCurrent,
    dataLog,
    isRecording,
    recordingBusy,
    joinRoom,
    leaveRoom,
    toggleMic,
    toggleCam,
    startSound,
    toggleRecording,
    selectGlobalModel,
    clearDataLog,
    logData,
    handleDataReceived,
    sendWorldToAvatar,
  };
}
