import { useEffect, useRef, useState } from "react";
import type { ConnectionQuality, RemoteVideoTrack, Room } from "livekit-client";
import { getWebPoseConfig, startWebPoseDetection } from "../codecs/webPoseLoader";
import type { VideoSlotRef } from "../hooks/useLiveKitRoom";
import type {
  CoachIaMessage,
  MicInfo,
  NetSample,
  PostureDisplay,
  WebPoseResult,
} from "../types";
import { AvatarBabylonPanel } from "./AvatarBabylonPanel";
import { CoachIaPanel } from "./CoachIaPanel";
import { CoachPaletteOverlay } from "./CoachPaletteOverlay";
import { TraineeHeaderBar } from "./TraineeHeaderBar";

interface ParticipantVideoCardProps {
  identity: string;
  displayName?: string;
  canAnnotate?: boolean;
  track: RemoteVideoTrack;
  trackSid: string;
  role?: string;
  mic?: MicInfo;
  quality?: ConnectionQuality;
  posture?: PostureDisplay;
  avatarActive: boolean;
  avatarPage: string;
  avatarExp: string;
  mirrorVideo: boolean;
  roomRef: React.MutableRefObject<Room | null>;
  videoSlotsRef: React.MutableRefObject<Map<string, VideoSlotRef>>;
  netListenersRef: React.MutableRefObject<
    Map<string, Set<(s: NetSample) => void>>
  >;
  iaListenersRef: React.MutableRefObject<
    Map<string, Set<(m: CoachIaMessage) => void>>
  >;
  statusListenersRef: React.MutableRefObject<
    Map<string, Set<(txt: string) => void>>
  >;
  onSentMarker: (bytes: Uint8Array, text: string, topic?: string) => void;
  onWebPoseWorld: (id: string, r: WebPoseResult) => void;
}

export function ParticipantVideoCard({
  identity,
  displayName,
  canAnnotate = true,
  track,
  trackSid,
  role,
  mic,
  quality,
  posture,
  avatarActive,
  avatarPage,
  avatarExp,
  mirrorVideo,
  roomRef,
  videoSlotsRef,
  netListenersRef,
  iaListenersRef,
  statusListenersRef,
  onSentMarker,
  onWebPoseWorld,
}: ParticipantVideoCardProps) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const [avatarOpen, setAvatarOpen] = useState(() => {
    try {
      return localStorage.getItem("avatarCollapsed") !== "1";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    const vid = videoRef.current;
    const cv = canvasRef.current;
    if (!vid || !cv) return;

    const slots = videoSlotsRef.current;
    track.attach(vid);
    vid.dataset.sid = trackSid;

    const slot = slots.get(identity);
    if (slot) {
      slot.video = vid;
      slot.canvas = cv;
    }

    let stopWebPose: (() => void) | null = null;
    let cancelled = false;

    if (getWebPoseConfig().enabled) {
      startWebPoseDetection(
        vid,
        (res) => onWebPoseWorld(identity, res),
        (err) => {
          const s = slots.get(identity);
          if (s) s.webPoseError = err.message;
        }
      )
        .then((stop) => {
          if (cancelled) stop();
          else {
            stopWebPose = stop;
            const s = slots.get(identity);
            if (s) s.stopWebPose = stop;
          }
        })
        .catch((err) => {
          const s = slots.get(identity);
          if (s) {
            s.webPoseError = err instanceof Error ? err.message : String(err);
          }
        });
    }

    return () => {
      cancelled = true;
      stopWebPose?.();
      try {
        track.detach(vid);
      } catch {
        // ignore
      }
      const s = slots.get(identity);
      if (s && s.video === vid) {
        s.video = null;
        s.canvas = null;
      }
    };
  }, [identity, onWebPoseWorld, track, trackSid, videoSlotsRef]);

  const showPanel = avatarActive && avatarOpen;

  return (
    <div className={`video-card${showPanel ? "" : " avatar-off"}`}>
      <TraineeHeaderBar
        identity={identity}
        displayName={displayName}
        role={role}
        mic={mic}
        quality={quality}
        posture={posture}
        defaultSyncText="Posture en attente : aucun flux de posture reçu."
        showAvatarButton={avatarActive}
        avatarOpen={avatarOpen}
        onToggleAvatar={() => {
          const next = !avatarOpen;
          setAvatarOpen(next);
          try {
            localStorage.setItem("avatarCollapsed", next ? "0" : "1");
          } catch {
            // ignore
          }
        }}
        netListenersRef={netListenersRef}
        statusListenersRef={statusListenersRef}
      />

      <div className="vwrap">
        <div ref={stageRef} className="video-stage">
          <video ref={videoRef} autoPlay playsInline />
          <canvas ref={canvasRef} className="overlay" />
          {canAnnotate && (
            <CoachPaletteOverlay
              identity={identity}
              stageRef={stageRef}
              videoRef={videoRef}
              roomRef={roomRef}
              mirrorVideo={mirrorVideo}
              onSent={onSentMarker}
            />
          )}
        </div>

        {avatarActive && (
          <AvatarBabylonPanel
            identity={identity}
            avatarPage={avatarPage}
            avatarExp={avatarExp}
            slotRef={videoSlotsRef}
          />
        )}

        <CoachIaPanel identity={identity} iaListenersRef={iaListenersRef} />
      </div>
    </div>
  );
}
