import { useCallback, useEffect, useRef, useState } from "react";
import type { Room } from "livekit-client";

const TOPIC = "marker";
const TTL_MS = 5000;
const FADE_MS = 1000;
const POP_MS = 180;
const MAX_BALLS = 5;
const MIN_INTERVAL_MS = 200;
const GLASSES_TOPIC = "engo_message";
const GLASSES_MAX = 70;
const GLASSES_SHOW_MS = 5000;

const BALL_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">' +
  '<path d="M32 56C25 59 39 60 32 63.5" fill="none" stroke="#1b1b1b" stroke-width="1.6" stroke-linecap="round"/>' +
  '<path d="M32 3C46 3 53.5 14 53.5 26C53.5 38 43 49 32 50C21 49 10.5 38 10.5 26C10.5 14 18 3 32 3Z" fill="#e5373a" stroke="#1b1b1b" stroke-width="2.5" stroke-linejoin="round"/>' +
  '<path d="M32 50L27.5 57H36.5Z" fill="#e5373a" stroke="#1b1b1b" stroke-width="2" stroke-linejoin="round"/>' +
  '<ellipse cx="23" cy="15" rx="5" ry="9" transform="rotate(25 23 15)" fill="#fff" opacity=".5"/>' +
  "</svg>";
const BALL_URI = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(BALL_SVG);
const CURSOR_SVG = BALL_SVG.replace('width="64" height="64"', 'width="32" height="32"');
export const BALL_CURSOR_STYLE = `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(CURSOR_SVG)}") 16 16, crosshair`;

let cachedBallImg: HTMLImageElement | null = null;
function getBallImage() {
  if (!cachedBallImg) {
    cachedBallImg = new Image();
    cachedBallImg.src = BALL_URI;
  }
  return cachedBallImg;
}

interface CoachPaletteOverlayProps {
  identity: string;
  stageRef: React.RefObject<HTMLDivElement | null>;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  roomRef: React.MutableRefObject<Room | null>;
  mirrorVideo: boolean;
  onSent: (bytes: Uint8Array, text: string, topic?: string) => void;
}

export function CoachPaletteOverlay({
  identity,
  stageRef,
  videoRef,
  roomRef,
  mirrorVideo,
  onSent,
}: CoachPaletteOverlayProps) {
  const [toolBall, setToolBall] = useState(false);
  const [glassesOpen, setGlassesOpen] = useState(false);
  const [glassesText, setGlassesText] = useState("");
  const [glassesStatus, setGlassesStatus] = useState("");
  const [cooldownLeft, setCooldownLeft] = useState(0);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const ballsRef = useRef<Array<{ id: string; x: number; y: number; t0: number }>>([]);
  const rafRef = useRef(0);
  const lastSentRef = useRef(0);
  const cooldownUntilRef = useRef(0);
  const mirrorRef = useRef(mirrorVideo);
  mirrorRef.current = mirrorVideo;
  const toolBallRef = useRef(toolBall);
  toolBallRef.current = toolBall;

  const imageBox = useCallback(() => {
    const stage = stageRef.current;
    const video = videoRef.current;
    const cw = stage?.clientWidth || 1;
    const ch = stage?.clientHeight || 1;
    const sw = video?.videoWidth || cw;
    const sh = video?.videoHeight || ch;
    const scale = Math.min(cw / sw, ch / sh);
    return {
      w: sw * scale,
      h: sh * scale,
      ox: (cw - sw * scale) / 2,
      oy: (ch - sh * scale) / 2,
      cw,
      ch,
    };
  }, [stageRef, videoRef]);

  const drawBalls = useCallback(() => {
    rafRef.current = 0;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const now = performance.now();
    ballsRef.current = ballsRef.current.filter((b) => now - b.t0 < TTL_MS);
    const dpr = window.devicePixelRatio || 1;
    const b = imageBox();
    const w = Math.round(b.cw * dpr);
    const h = Math.round(b.ch * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, b.cw, b.ch);

    const img = getBallImage();
    const m = mirrorRef.current;
    for (const ball of ballsRef.current) {
      const age = now - ball.t0;
      const pop = Math.min(1, age / POP_MS);
      const alpha = age > TTL_MS - FADE_MS ? (TTL_MS - age) / FADE_MS : 1;
      const size = Math.max(28, Math.min(b.w, b.h) * 0.1) * (0.4 + 0.6 * pop);
      const px = b.ox + (m ? 1 - ball.x : ball.x) * b.w;
      const py = b.oy + ball.y * b.h;
      ctx.globalAlpha = alpha;
      if (img.complete && img.naturalWidth) {
        ctx.drawImage(img, px - size / 2, py - size / 2, size, size);
      }
    }
    ctx.globalAlpha = 1;
    if (ballsRef.current.length) {
      rafRef.current = requestAnimationFrame(drawBalls);
    }
  }, [imageBox]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const onStageClick = (e: MouseEvent) => {
      if (!toolBallRef.current) return;
      if (barRef.current?.contains(e.target as Node)) return;
      const room = roomRef.current;
      if (!room || room.state !== "connected") return;
      const rect = stage.getBoundingClientRect();
      const b = imageBox();
      const u = (e.clientX - rect.left - b.ox) / b.w;
      const v = (e.clientY - rect.top - b.oy) / b.h;
      if (u < 0 || u > 1 || v < 0 || v > 1) return;
      const now = performance.now();
      if (now - lastSentRef.current < MIN_INTERVAL_MS) return;
      lastSentRef.current = now;

      const x = mirrorRef.current ? 1 - u : u;
      const msg = {
        v: 1,
        id: `${room.localParticipant.identity}-${Date.now().toString(36)}`,
        type: "ball",
        x: +x.toFixed(4),
        y: +v.toFixed(4),
        ttl: TTL_MS,
        ts: Date.now(),
      };
      ballsRef.current.push({ id: msg.id, x: msg.x, y: msg.y, t0: performance.now() });
      if (ballsRef.current.length > MAX_BALLS) ballsRef.current.shift();
      if (!rafRef.current) rafRef.current = requestAnimationFrame(drawBalls);

      const text = JSON.stringify(msg);
      const bytes = new TextEncoder().encode(text);
      onSent(bytes, text, TOPIC);
      room.localParticipant
        .publishData(bytes, {
          reliable: true,
          topic: TOPIC,
          destinationIdentities: [identity],
        })
        .catch((err) => console.warn("[marker] envoi impossible :", err));
    };

    stage.addEventListener("click", onStageClick);
    return () => {
      stage.removeEventListener("click", onStageClick);
      cancelAnimationFrame(rafRef.current);
    };
  }, [drawBalls, identity, imageBox, onSent, roomRef, stageRef]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    stage.style.cursor = toolBall ? BALL_CURSOR_STYLE : "";
    return () => {
      stage.style.cursor = "";
    };
  }, [stageRef, toolBall]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (toolBall) setToolBall(false);
      if (glassesOpen && stageRef.current?.contains(document.activeElement)) {
        setGlassesOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [glassesOpen, stageRef, toolBall]);

  useEffect(() => {
    if (!cooldownLeft) return;
    const id = window.setInterval(() => {
      const left = Math.ceil((cooldownUntilRef.current - Date.now()) / 1000);
      if (left <= 0) {
        setCooldownLeft(0);
        setGlassesStatus("");
      } else {
        setCooldownLeft(left);
        setGlassesStatus(`Affiché sur les lunettes : ${left} s`);
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [cooldownLeft]);

  const sendGlasses = () => {
    const text = glassesText.trim();
    if (!text || Date.now() < cooldownUntilRef.current) return;
    const room = roomRef.current;
    if (!room || room.state !== "connected") {
      setGlassesStatus("Salle non connectée");
      return;
    }
    const msg = {
      v: 1,
      id: `${room.localParticipant.identity}-${Date.now().toString(36)}`,
      type: "text",
      text: text.slice(0, GLASSES_MAX),
      ttl: GLASSES_SHOW_MS,
      ts: Date.now(),
    };
    const json = JSON.stringify(msg);
    const bytes = new TextEncoder().encode(json);
    onSent(bytes, json, GLASSES_TOPIC);
    room.localParticipant
      .publishData(bytes, {
        reliable: true,
        topic: GLASSES_TOPIC,
        destinationIdentities: [identity],
      })
      .then(() => {
        setGlassesText("");
        cooldownUntilRef.current = Date.now() + GLASSES_SHOW_MS;
        const left = Math.ceil(GLASSES_SHOW_MS / 1000);
        setCooldownLeft(left);
        setGlassesStatus(`Affiché sur les lunettes : ${left} s`);
      })
      .catch((err) => {
        setGlassesStatus("Échec de l'envoi");
        console.warn("[engo_message] envoi impossible :", err);
      });
  };

  return (
    <>
      <canvas ref={canvasRef} className="marker-overlay" />

      <div className="glasses-panel" hidden={!glassesOpen}>
        <textarea
          ref={taRef}
          maxLength={GLASSES_MAX}
          rows={3}
          placeholder="Message pour les lunettes…"
          aria-label="Message pour les lunettes"
          value={glassesText}
          onChange={(e) => {
            setGlassesText(e.target.value);
            if (Date.now() >= cooldownUntilRef.current) setGlassesStatus("");
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendGlasses();
            }
          }}
        />
        <div className="glasses-row">
          <span>
            {glassesText.length}/{GLASSES_MAX}
          </span>
          <span className="glasses-status">{glassesStatus}</span>
          <button
            type="button"
            disabled={!glassesText.trim() || cooldownLeft > 0}
            onClick={sendGlasses}
          >
            Envoyer
          </button>
        </div>
      </div>

      <div ref={barRef} className="coach-palette">
        <button
          type="button"
          title="Ballon : cliquez sur la vidéo pour le poser chez le sportif (Échap pour quitter)"
          aria-label="Ballon"
          aria-pressed={toolBall}
          onClick={(e) => {
            e.stopPropagation();
            if (!toolBall) setGlassesOpen(false);
            setToolBall((v) => !v);
          }}
        >
          <img src={BALL_URI} alt="" />
        </button>

        <button
          type="button"
          title="Lunettes : envoyer un message texte affiché sur les lunettes du sportif"
          aria-label="Lunettes"
          aria-pressed={glassesOpen}
          onClick={(e) => {
            e.stopPropagation();
            const next = !glassesOpen;
            setGlassesOpen(next);
            if (next) {
              setToolBall(false);
              setGlassesStatus("");
              window.setTimeout(() => taRef.current?.focus(), 0);
            }
          }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 32 32"
            fill="none"
            stroke="#eee"
            strokeWidth="2.2"
            strokeLinecap="round"
          >
            <circle cx="9" cy="19" r="5.5" />
            <circle cx="23" cy="19" r="5.5" />
            <path d="M14.5 18.5Q16 16.5 17.5 18.5M3.5 19L5 10M28.5 19L27 10" />
          </svg>
        </button>
      </div>
    </>
  );
}
