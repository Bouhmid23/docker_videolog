import { useEffect, useRef, useState } from "react";
import type { LocalTrack } from "livekit-client";

interface SelfViewPipProps {
  track: LocalTrack | null;
}

export function SelfViewPip({ track }: SelfViewPipProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const dragOffsetRef = useRef<{ dx: number; dy: number } | null>(null);

  useEffect(() => {
    const vid = videoRef.current;
    if (!vid || !track) {
      if (vid) vid.srcObject = null;
      return;
    }
    track.attach(vid);
    return () => {
      track.detach(vid);
      vid.srcObject = null;
    };
  }, [track]);

  return (
    <div
      ref={boxRef}
      id="selfView"
      className={dragging ? "dragging" : undefined}
      hidden={!track}
      style={pos ? { left: pos.left, top: pos.top, bottom: "auto" } : undefined}
      onPointerDown={(e) => {
        const el = boxRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        dragOffsetRef.current = {
          dx: e.clientX - rect.left,
          dy: e.clientY - rect.top,
        };
        setDragging(true);
        el.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!dragOffsetRef.current || !boxRef.current) return;
        const w = boxRef.current.offsetWidth;
        const h = boxRef.current.offsetHeight;
        const left = Math.max(
          8,
          Math.min(e.clientX - dragOffsetRef.current.dx, window.innerWidth - w - 8)
        );
        const top = Math.max(
          64,
          Math.min(e.clientY - dragOffsetRef.current.dy, window.innerHeight - h - 8)
        );
        setPos({ left, top });
      }}
      onPointerUp={() => {
        dragOffsetRef.current = null;
        setDragging(false);
      }}
    >
      <video ref={videoRef} autoPlay muted playsInline />
      <span>Vous</span>
    </div>
  );
}
