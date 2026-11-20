import { Link } from "react-router-dom";
import type { Participant } from "livekit-client";
import type { AvatarModelInfo, PostureDisplay } from "../types";
import { PostureReplayControls } from "./PostureReplayControls";

interface RoomTopbarProps {
  connected: boolean;
  endedMode?: boolean;
  canRecord?: boolean;
  debugMode?: boolean;
  nameMap?: Record<string, string>;
  globalModels: AvatarModelInfo[];
  globalModelCurrent: string;
  onSelectGlobalModel: (key: string) => void;
  mirrorVideo: boolean;
  onToggleMirror: (checked: boolean) => void;
  lowLatency: boolean;
  onToggleLowLatency: (checked: boolean) => void;
  unbandedPostures: Array<[string, PostureDisplay]>;
  micEnabled: boolean;
  camEnabled: boolean;
  isRecording: boolean;
  recordingBusy: boolean;
  onToggleMic: () => void;
  onToggleCam: () => void;
  onStartSound: () => void;
  onToggleRecording: () => void;
  onLeave: () => void;
  replayInterceptorRef: React.MutableRefObject<
    ((payload: Uint8Array | ArrayBuffer, participant?: Participant) => boolean) | null
  >;
  getVideoIdentities: () => string[];
  onInjectPosture: (buf: ArrayBuffer, targetIdentity: string) => void;
}

export function RoomTopbar({
  connected,
  endedMode = false,
  canRecord = true,
  debugMode = false,
  nameMap,
  globalModels,
  globalModelCurrent,
  onSelectGlobalModel,
  mirrorVideo,
  onToggleMirror,
  lowLatency,
  onToggleLowLatency,
  unbandedPostures,
  micEnabled,
  camEnabled,
  isRecording,
  recordingBusy,
  onToggleMic,
  onToggleCam,
  onStartSound,
  onToggleRecording,
  onLeave,
  replayInterceptorRef,
  getVideoIdentities,
  onInjectPosture,
}: RoomTopbarProps) {
  return (
    <header className="topbar">
      <Link className="brand" to="/app/sessions">
        <img src="/live/logo.png" alt="VideoLog OnAir" />
        <span>VIDEOLOG ONAIR</span>
        <em>Salon live</em>
      </Link>

      {!connected && (
        <Link
          className="room-topbar-back lobby-back"
          style={{ marginTop: 0 }}
          to={endedMode ? "/app" : "/app/sessions"}
        >
          {endedMode ? "← Retour à l'accueil" : "← Retour aux sessions"}
        </Link>
      )}

      <section id="room" hidden={!connected}>
        <span className="spacer" />

        <label className="glb-label">
          Avatar{" "}
          <select
            id="glbSelect"
            disabled={!globalModels.length}
            value={globalModelCurrent}
            onChange={(e) => onSelectGlobalModel(e.target.value)}
          >
            {!globalModels.length ? (
              <option>chargement…</option>
            ) : (
              globalModels.map((m) => (
                <option key={m.key} value={m.key} disabled={!m.rigged}>
                  {m.rigged ? m.label : `${m.label} (sans squelette)`}
                </option>
              ))
            )}
          </select>
        </label>

        <label title="Affiche la vidéo comme dans un miroir : le bras droit levé apparaît à droite">
          <input
            type="checkbox"
            id="mirrorVideo"
            checked={mirrorVideo}
            onChange={(e) => onToggleMirror(e.target.checked)}
          />{" "}
          Miroir
        </label>

        <label>
          <input
            type="checkbox"
            id="lowlat"
            checked={lowLatency}
            onChange={(e) => onToggleLowLatency(e.target.checked)}
          />{" "}
          Faible latence
        </label>

        <div id="postureBox">
          {unbandedPostures.map(([id, posture]) => {
            const conf =
              posture.confidence == null
                ? "N/A"
                : `${(posture.confidence * 100).toFixed(0)} %`;
            const display =
              nameMap?.[id] || nameMap?.[id.split(":")[0]] || id.split(":")[0];
            return (
              <div key={id} className="tele">
                <b>{display} — posture</b>
                <span>
                  {posture.label} · confiance {conf} · {posture.count} points
                </span>
              </div>
            );
          })}
        </div>

        {debugMode && (
          <PostureReplayControls
            replayInterceptorRef={replayInterceptorRef}
            getVideoIdentities={getVideoIdentities}
            onInjectPosture={onInjectPosture}
          />
        )}

        <button
          id="mute"
          className="secondary"
          type="button"
          onClick={onToggleMic}
        >
          {micEnabled ? "Couper le micro" : "Activer le micro"}
        </button>

        <button
          id="cam"
          className="secondary"
          type="button"
          onClick={onToggleCam}
        >
          {camEnabled ? "Couper ma webcam" : "Activer ma webcam"}
        </button>

        <button
          id="sound"
          className="secondary"
          type="button"
          onClick={onStartSound}
        >
          Activer le son
        </button>

        {canRecord && (
          <button
            id="recBtn"
            className="secondary"
            type="button"
            disabled={recordingBusy}
            onClick={onToggleRecording}
          >
            {isRecording ? "Arrêter l'enregistrement" : "Enregistrer"}
          </button>
        )}

        <button
          id="leave"
          className="secondary"
          type="button"
          onClick={onLeave}
        >
          Quitter
        </button>
      </section>
    </header>
  );
}
