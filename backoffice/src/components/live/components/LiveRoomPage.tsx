import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../../../auth/AuthProvider";
import {
  accumulateTelemetrySample,
  buildSessionEndedSnapshot,
  isSystemParticipant,
  type SessionEndedSnapshot,
  type SessionParticipantSummary,
  type TelemetryAgg,
  type WorkoutProgressSummary,
} from "../codecs/sessionSummary";
import { getWebPoseConfig } from "../codecs/webPoseLoader";
import {
  formatParticipantDisplayName,
  useLiveKitRoom,
} from "../hooks/useLiveKitRoom";
import type { LobbyInfo, TelemetryData } from "../types";
import { DataTracksDrawer } from "./DataTracksDrawer";
import { ParticipantVideoCard } from "./ParticipantVideoCard";
import { RoomEndedSummary } from "./RoomEndedSummary";
import { RoomLobby } from "./RoomLobby";
import { RoomTopbar } from "./RoomTopbar";
import { SelfViewPip } from "./SelfViewPip";
import { SoloEmitterBand } from "./SoloEmitterBand";
import { TelemetryDrawer } from "./TelemetryDrawer";
import { WorkoutBanner, type WorkoutSyncMsg } from "./WorkoutBanner";
import "../liveRoom.css";

export function LiveRoomPage() {
  const { roomName: paramRoom } = useParams<{ roomName?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { activeMembership, me } = useAuth();

  const roomName = (paramRoom || searchParams.get("room") || "").trim();
  const debugMode = searchParams.has("debug");
  const avatarPage =
    searchParams.get("avatar") === "ref"
      ? "avatar/avatar.html"
      : "avatar-v2/avatar.html";
  const webPose = getWebPoseConfig().enabled;
  const avatarExp = avatarPage.startsWith("avatar-v2")
    ? searchParams.get("exp") ?? (webPose ? "" : "zlp")
    : "";

  const [noAvatarAsset, setNoAvatarAsset] = useState(false);
  const [lobbyInfo, setLobbyInfo] = useState<LobbyInfo | null>(null);
  const [screenMode, setScreenMode] = useState<"lobby" | "in_room" | "ended">("lobby");
  const [endedSummary, setEndedSummary] = useState<SessionEndedSnapshot | null>(null);
  const [mirrorVideo, setMirrorVideo] = useState(() => {
    try {
      return (localStorage.getItem("mirrorVideo") ?? "1") === "1";
    } catch {
      return true;
    }
  });
  const [lowLatency, setLowLatency] = useState(false);

  const [dataDrawerOpen, setDataDrawerOpen] = useState(() => {
    try {
      return localStorage.getItem("sd-data") === "1";
    } catch {
      return false;
    }
  });
  const [teleDrawerOpen, setTeleDrawerOpen] = useState(() => {
    try {
      return localStorage.getItem("teleOpen") === "1";
    } catch {
      return false;
    }
  });

  const rootRef = useRef<HTMLDivElement | null>(null);
  const workoutSyncRef = useRef<((msg: WorkoutSyncMsg) => void) | null>(null);
  const sessionStartMsRef = useRef<number | null>(null);
  const participantsSeenRef = useRef<Map<string, SessionParticipantSummary>>(new Map());
  const telemetryAggRef = useRef<Map<string, TelemetryAgg>>(new Map());
  const workoutProgressRef = useRef<WorkoutProgressSummary | null>(null);
  const hadRecordingRef = useRef(false);
  const markersCountRef = useRef(0);

  const handleWorkoutStep = useCallback((payload: Uint8Array) => {
    try {
      const msg = JSON.parse(new TextDecoder().decode(payload)) as WorkoutSyncMsg;
      workoutSyncRef.current?.(msg);
    } catch {
      // ignore
    }
  }, []);

  const roomCtrl = useLiveKitRoom({
    avatarPage,
    lowLatency,
    onWorkoutStep: handleWorkoutStep,
  });

  // Vérifier la présence de l'asset avatar-v2/avatar.html
  useEffect(() => {
    fetch(`/live/${avatarPage}`, { method: "HEAD" })
      .then((r) => {
        if (!r.ok) setNoAvatarAsset(true);
      })
      .catch(() => setNoAvatarAsset(true));
  }, [avatarPage]);

  // Ajuster --topbar-h dynamiquement pour les deux tiroirs latéraux
  useEffect(() => {
    const root = rootRef.current;
    const bar = root?.querySelector(".topbar") as HTMLElement | null;
    if (!root || !bar || !window.ResizeObserver) return;
    const ro = new ResizeObserver(() => {
      root.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
    });
    ro.observe(bar);
    return () => ro.disconnect();
  }, [roomCtrl.connected]);

  const { joinRoom, leaveRoom, setStatus, handleDataReceived } = roomCtrl;

  // Basculer en mode "in_room" et réinitialiser les agrégats de session à la connexion
  useEffect(() => {
    if (roomCtrl.connected) {
      setScreenMode("in_room");
      setEndedSummary(null);
      if (sessionStartMsRef.current === null) {
        sessionStartMsRef.current = Date.now();
        participantsSeenRef.current = new Map();
        telemetryAggRef.current = new Map();
        hadRecordingRef.current = false;
        markersCountRef.current = 0;
      }
    }
  }, [roomCtrl.connected]);

  useEffect(() => {
    if (roomCtrl.isRecording) {
      hadRecordingRef.current = true;
    }
  }, [roomCtrl.isRecording]);

  useEffect(() => {
    const listeners = roomCtrl.telemetryListenersRef.current;
    const onTelemetry = (id: string, data: TelemetryData | null) => {
      if (!data || id === "*" || isSystemParticipant(id)) return;
      accumulateTelemetrySample(telemetryAggRef.current, id, data);
    };
    listeners.add(onTelemetry);
    return () => {
      listeners.delete(onTelemetry);
    };
  }, [roomCtrl.telemetryListenersRef]);

  const handleJoin = useCallback(
    async (targetRoom: string, useCamera: boolean) => {
      try {
        sessionStartMsRef.current = null;
        await joinRoom(targetRoom, useCamera);
      } catch (err) {
        setStatus(
          "Erreur : " + (err instanceof Error ? err.message : String(err)),
          "err"
        );
      }
    },
    [joinRoom, setStatus]
  );

  const handleLobbyError = useCallback(
    (msg: string) => {
      setStatus(msg, "err");
    },
    [setStatus]
  );

  const videoIdentities = useMemo(
    () => roomCtrl.videoCards.map((c) => c.identity),
    [roomCtrl.videoCards]
  );

  const getVideoIdentities = useCallback(() => videoIdentities, [videoIdentities]);

  const handleInjectPosture = useCallback(
    (buf: ArrayBuffer, targetIdentity: string) => {
      handleDataReceived(
        buf,
        { identity: targetIdentity },
        undefined,
        "posture"
      );
    },
    [handleDataReceived]
  );

  const unbandedPostures = useMemo(() => {
    const videoSet = new Set(roomCtrl.videoCards.map((c) => c.identity));
    const soloSet = new Set(roomCtrl.soloEmitters);
    return Object.entries(roomCtrl.postureMap).filter(
      ([id]) => !videoSet.has(id) && !soloSet.has(id)
    );
  }, [roomCtrl.postureMap, roomCtrl.soloEmitters, roomCtrl.videoCards]);

  const displayNameMap = useMemo(() => {
    const map: Record<string, string> = { ...roomCtrl.nameMap };
    for (const p of lobbyInfo?.invited_participants || []) {
      if (p.uid && p.name) {
        if (!map[p.uid]) map[p.uid] = p.name;
        if (!map[`${p.uid}:web`]) map[`${p.uid}:web`] = p.name;
      }
    }
    return map;
  }, [lobbyInfo?.invited_participants, roomCtrl.nameMap]);

  const displayParticipants = useMemo(
    () =>
      roomCtrl.participants.map((p) => ({
        ...p,
        name: formatParticipantDisplayName(p.identity, displayNameMap),
      })),
    [displayNameMap, roomCtrl.participants]
  );

  useEffect(() => {
    if (!roomCtrl.connected) return;
    for (const p of displayParticipants) {
      if (isSystemParticipant(p.identity)) continue;
      participantsSeenRef.current.set(p.identity, {
        identity: p.identity,
        name: p.name,
        role: roomCtrl.roleMap[p.identity] || "participant",
      });
    }
  }, [displayParticipants, roomCtrl.connected, roomCtrl.roleMap]);

  const handleLeaveRoom = useCallback(async () => {
    const durationS = sessionStartMsRef.current
      ? Math.max(1, Math.round((Date.now() - sessionStartMsRef.current) / 1000))
      : 0;
    const targetRoom = roomCtrl.activeRoomName || roomName;
    const snapshot = buildSessionEndedSnapshot({
      roomName: targetRoom,
      lobbyInfo,
      orgIdFallback: activeMembership?.org_id || null,
      durationS,
      participants: [...participantsSeenRef.current.values()],
      telemetryMap: telemetryAggRef.current,
      nameMap: displayNameMap,
      roleMap: roomCtrl.roleMap,
      workout: workoutProgressRef.current,
      hadRecording: hadRecordingRef.current,
      markersCount: markersCountRef.current,
    });
    setEndedSummary(snapshot);
    setScreenMode("ended");
    sessionStartMsRef.current = null;
    await leaveRoom();
  }, [
    activeMembership?.org_id,
    displayNameMap,
    leaveRoom,
    lobbyInfo,
    roomCtrl.activeRoomName,
    roomCtrl.roleMap,
    roomName,
  ]);

  const effectiveRole = (
    roomCtrl.localRole ||
    activeMembership?.role ||
    (me?.platform_admin ? "platform_admin" : "")
  ).toLowerCase();
  const isCoach =
    Boolean(me?.platform_admin) ||
    !effectiveRole ||
    [
      "owner",
      "admin",
      "coach",
      "org_owner",
      "org_admin",
      "platform_admin",
    ].includes(effectiveRole);

  const rootClasses = [
    "vl-room-root",
    roomCtrl.connected ? "in-room" : "",
    mirrorVideo ? "mirror-video" : "",
    noAvatarAsset ? "no-avatar" : "",
    roomCtrl.hasActiveAvatar && !noAvatarAsset ? "has-avatar" : "",
    dataDrawerOpen ? "sd-open-data sd-pad-left-data" : "",
    teleDrawerOpen ? "tele-open" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div ref={rootRef} className={rootClasses}>
      <RoomTopbar
        connected={roomCtrl.connected}
        endedMode={screenMode === "ended"}
        canRecord={isCoach}
        debugMode={debugMode}
        nameMap={displayNameMap}
        globalModels={roomCtrl.globalModels}
        globalModelCurrent={roomCtrl.globalModelCurrent}
        onSelectGlobalModel={roomCtrl.selectGlobalModel}
        mirrorVideo={mirrorVideo}
        onToggleMirror={(checked) => {
          setMirrorVideo(checked);
          try {
            localStorage.setItem("mirrorVideo", checked ? "1" : "0");
          } catch {
            // ignore
          }
        }}
        lowLatency={lowLatency}
        onToggleLowLatency={setLowLatency}
        unbandedPostures={unbandedPostures}
        micEnabled={roomCtrl.micEnabled}
        camEnabled={roomCtrl.camEnabled}
        isRecording={roomCtrl.isRecording}
        recordingBusy={roomCtrl.recordingBusy}
        onToggleMic={roomCtrl.toggleMic}
        onToggleCam={roomCtrl.toggleCam}
        onStartSound={roomCtrl.startSound}
        onToggleRecording={roomCtrl.toggleRecording}
        onLeave={() => void handleLeaveRoom()}
        replayInterceptorRef={roomCtrl.replayInterceptorRef}
        getVideoIdentities={getVideoIdentities}
        onInjectPosture={handleInjectPosture}
      />

      <main className="page">
        {!roomCtrl.connected && screenMode === "lobby" && (
          <RoomLobby
            roomName={roomName}
            joining={roomCtrl.joining}
            onJoin={(r, useCam) => {
              if (!paramRoom && r) {
                navigate(`/app/live/${encodeURIComponent(r)}`, { replace: true });
              }
              void handleJoin(r, useCam);
            }}
            onLobbyLoaded={setLobbyInfo}
            onLobbyError={handleLobbyError}
          />
        )}

        {!roomCtrl.connected && screenMode === "ended" && endedSummary && (
          <RoomEndedSummary summary={endedSummary} isCoach={isCoach} />
        )}

        <WorkoutBanner
          connected={roomCtrl.connected}
          canControl={isCoach}
          workout={lobbyInfo?.planned_workout || null}
          roomRef={roomCtrl.roomRef}
          externalSyncRef={workoutSyncRef}
          onProgressChange={(prog) => {
            workoutProgressRef.current = prog;
          }}
        />

        <SelfViewPip track={roomCtrl.localCameraTrack} />

        <div id="status" role="status" className={roomCtrl.status.kind}>
          {roomCtrl.status.text}
        </div>

        <div id="bands">
          {roomCtrl.connected &&
            roomCtrl.soloEmitters.map((id) => (
              <SoloEmitterBand
                key={id}
                identity={id}
                displayName={formatParticipantDisplayName(id, displayNameMap)}
                role={roomCtrl.roleMap[id]}
                mic={roomCtrl.micInfoMap[id]}
                quality={roomCtrl.qualityMap[id]}
                posture={roomCtrl.postureMap[id]}
                netListenersRef={roomCtrl.netListenersRef}
              />
            ))}
        </div>

        <div id="videos">
          {roomCtrl.connected &&
            roomCtrl.videoCards.map((card) => (
              <ParticipantVideoCard
                key={`${card.identity}:${card.trackSid}`}
                identity={card.identity}
                displayName={formatParticipantDisplayName(
                  card.identity,
                  displayNameMap
                )}
                canAnnotate={isCoach}
                track={card.track}
                trackSid={card.trackSid}
                role={roomCtrl.roleMap[card.identity]}
                mic={roomCtrl.micInfoMap[card.identity]}
                quality={roomCtrl.qualityMap[card.identity] ?? card.connectionQuality}
                posture={roomCtrl.postureMap[card.identity]}
                avatarActive={Boolean(roomCtrl.avatarActiveMap[card.identity])}
                avatarPage={avatarPage}
                avatarExp={avatarExp}
                mirrorVideo={mirrorVideo}
                roomRef={roomCtrl.roomRef}
                videoSlotsRef={roomCtrl.videoSlotsRef}
                netListenersRef={roomCtrl.netListenersRef}
                iaListenersRef={roomCtrl.iaListenersRef}
                statusListenersRef={roomCtrl.statusListenersRef}
                onSentMarker={(bytes, text, topic) => {
                  markersCountRef.current += 1;
                  roomCtrl.logData(
                    bytes,
                    `moi → ${formatParticipantDisplayName(
                      card.identity,
                      displayNameMap
                    )}`,
                    topic || "marker",
                    text
                  );
                }}
                onWebPoseWorld={roomCtrl.sendWorldToAvatar}
              />
            ))}
        </div>

        <div ref={roomCtrl.audioHostRef} id="audio" />
      </main>

      <DataTracksDrawer
        open={dataDrawerOpen}
        onToggleOpen={() => {
          const next = !dataDrawerOpen;
          setDataDrawerOpen(next);
          try {
            localStorage.setItem("sd-data", next ? "1" : "0");
          } catch {
            // ignore
          }
        }}
        roomName={roomCtrl.activeRoomName}
        participants={displayParticipants}
        dataLog={roomCtrl.dataLog}
        onClearLog={roomCtrl.clearDataLog}
      />

      <TelemetryDrawer
        open={teleDrawerOpen}
        onToggleOpen={() => {
          const next = !teleDrawerOpen;
          setTeleDrawerOpen(next);
          try {
            localStorage.setItem("teleOpen", next ? "1" : "0");
          } catch {
            // ignore
          }
        }}
        nameMap={displayNameMap}
        thresholdsMap={lobbyInfo?.participant_thresholds}
        latestRef={roomCtrl.lastTelemetryRef}
        telemetryListenersRef={roomCtrl.telemetryListenersRef}
      />
    </div>
  );
}
