import { useCallback, useEffect, useRef, useState } from "react";
import type { Participant } from "livekit-client";
import { asBytes } from "../codecs/postureCodec";

interface ReplayData {
  v: number;
  id: string | null;
  packets: Array<{ t: number; b: string }>;
}

const toB64 = (u8: Uint8Array) => {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
  }
  return btoa(s);
};

const fromB64 = (b: string) =>
  Uint8Array.from(atob(b), (c) => c.charCodeAt(0));

interface PostureReplayControlsProps {
  replayInterceptorRef: React.MutableRefObject<
    ((payload: Uint8Array | ArrayBuffer, participant?: Participant) => boolean) | null
  >;
  getVideoIdentities: () => string[];
  onInjectPosture: (buf: ArrayBuffer, targetIdentity: string) => void;
}

export function PostureReplayControls({
  replayInterceptorRef,
  getVideoIdentities,
  onInjectPosture,
}: PostureReplayControlsProps) {
  const recRef = useRef<{
    v: number;
    startedAt: number;
    id: string | null;
    packets: Array<{ t: number; b: string }>;
  } | null>(null);
  const playingRef = useRef<{ timer: number; stop: () => void } | null>(null);
  const injectingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const loopRef = useRef(true);
  loopRef.current = loop;

  const [lastRec, setLastRec] = useState<ReplayData | null>(null);
  const [recCount, setRecCount] = useState(0);

  useEffect(() => {
    replayInterceptorRef.current = (payload, participant) => {
      if (injectingRef.current) return false;
      if (playingRef.current) return true;
      if (recRef.current) {
        const bytes = asBytes(payload);
        if (bytes) {
          recRef.current.id ||= participant?.identity || "inconnu";
          recRef.current.packets.push({
            t: Math.round(performance.now() - recRef.current.startedAt),
            b: toB64(bytes),
          });
        }
      }
      return false;
    };
    return () => {
      replayInterceptorRef.current = null;
      playingRef.current?.stop();
    };
  }, [replayInterceptorRef]);

  useEffect(() => {
    if (!isRecording) return;
    const id = window.setInterval(() => {
      setRecCount(recRef.current?.packets.length || 0);
    }, 500);
    return () => window.clearInterval(id);
  }, [isRecording]);

  const startRec = () => {
    recRef.current = { v: 1, startedAt: performance.now(), id: null, packets: [] };
    setRecCount(0);
    setIsRecording(true);
  };

  const stopRec = () => {
    const r = recRef.current;
    if (!r) return;
    if (r.packets.length) {
      setLastRec({ v: 1, id: r.id, packets: r.packets });
    }
    recRef.current = null;
    setIsRecording(false);
  };

  const startPlay = useCallback(
    (data: ReplayData) => {
      if (playingRef.current) return;
      const ids = getVideoIdentities();
      const target = data.id && ids.includes(data.id) ? data.id : ids[0];
      if (!target) {
        window.alert(
          "Aucun participant avec vidéo dans le salon : le rejeu n'aurait rien à afficher."
        );
        return;
      }

      const t0 = performance.now();
      let i = 0;
      const state = {
        timer: 0,
        stop: () => {
          window.clearTimeout(state.timer);
          playingRef.current = null;
          setIsPlaying(false);
        },
      };

      const step = () => {
        const now = performance.now() - t0;
        while (i < data.packets.length && data.packets[i].t <= now) {
          const buf = fromB64(data.packets[i++].b).buffer;
          injectingRef.current = true;
          try {
            onInjectPosture(buf, target);
          } finally {
            injectingRef.current = false;
          }
        }
        if (i >= data.packets.length) {
          state.stop();
          if (loopRef.current) startPlay(data);
          return;
        }
        state.timer = window.setTimeout(
          step,
          Math.max(1, data.packets[i].t - (performance.now() - t0))
        );
      };

      playingRef.current = state;
      setIsPlaying(true);
      step();
    },
    [getVideoIdentities, onInjectPosture]
  );

  const togglePlay = () => {
    if (playingRef.current) {
      playingRef.current.stop();
    } else if (lastRec) {
      startPlay(lastRec);
    }
  };

  const handleSave = () => {
    if (!lastRec) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(
      new Blob([JSON.stringify(lastRec)], { type: "application/json" })
    );
    a.download = `posture-${new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[:T]/g, "-")}.json`;
    document.body.append(a);
    a.click();
    window.setTimeout(() => {
      a.remove();
      URL.revokeObjectURL(a.href);
    }, 1000);
  };

  const handleOpenFile = async (file: File) => {
    try {
      const txt = await file.text();
      const d = JSON.parse(txt) as ReplayData;
      if (!d || d.v !== 1 || !Array.isArray(d.packets)) {
        throw new Error("format inconnu");
      }
      setLastRec(d);
    } catch (e) {
      window.alert(
        "Fichier illisible : " + (e instanceof Error ? e.message : String(e))
      );
    }
  };

  const n = isRecording ? recCount : lastRec ? lastRec.packets.length : 0;
  const dur =
    ((isRecording ? recRef.current?.packets : lastRec?.packets) || []).slice(
      -1
    )[0]?.t || 0;
  const infoText = isRecording
    ? `● ${n} paquets`
    : lastRec
      ? `${n} paquets · ${(dur / 1000).toFixed(1)} s`
      : "";

  return (
    <span
      id="replayUi"
      style={{
        display: "inline-flex",
        gap: 6,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <button
        type="button"
        className="secondary"
        onClick={isRecording ? stopRec : startRec}
      >
        {isRecording ? "Arrêter l'enregistrement" : "Enregistrer les poses"}
      </button>
      <button
        type="button"
        className="secondary"
        disabled={!lastRec || isRecording}
        onClick={togglePlay}
      >
        {isPlaying ? "Arrêter le rejeu" : "Rejouer"}
      </button>
      <label title="Rejoue en boucle">
        <input
          type="checkbox"
          id="replayLoop"
          checked={loop}
          onChange={(e) => setLoop(e.target.checked)}
        />{" "}
        boucle
      </label>
      <button
        type="button"
        className="secondary"
        disabled={!lastRec}
        onClick={handleSave}
      >
        Enregistrer sous…
      </button>
      <button
        type="button"
        className="secondary"
        onClick={() => fileInputRef.current?.click()}
      >
        Ouvrir…
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleOpenFile(f);
          e.target.value = "";
        }}
      />
      <small style={{ color: "var(--muted)" }}>{infoText}</small>
    </span>
  );
}
