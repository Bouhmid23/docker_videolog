import { useEffect, useMemo, useRef, useState } from "react";
import type { VideoSlotRef } from "../hooks/useLiveKitRoom";

interface AvatarBabylonPanelProps {
  identity: string;
  avatarPage: string;
  avatarExp: string;
  slotRef: React.MutableRefObject<Map<string, VideoSlotRef>>;
}

export function AvatarBabylonPanel({
  identity,
  avatarPage,
  avatarExp,
  slotRef,
}: AvatarBabylonPanelProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [credit, setCredit] = useState("");

  const src = useMemo(() => {
    let saved = "";
    try {
      saved =
        localStorage.getItem("glbModel:" + identity) ||
        localStorage.getItem("glbModel") ||
        "";
    } catch {
      // ignore
    }
    return (
      `/live/${avatarPage}?embed=1` +
      (saved ? `&model=${encodeURIComponent(saved)}` : "") +
      (avatarExp ? `&exp=${avatarExp}` : "")
    );
  }, [avatarExp, avatarPage, identity]);

  useEffect(() => {
    const slots = slotRef.current;
    const win = frameRef.current?.contentWindow || null;
    const slot = slots.get(identity);
    if (slot) {
      slot.iframeWindow = win;
    }

    const onMessage = (e: MessageEvent) => {
      if (!frameRef.current || e.source !== frameRef.current.contentWindow) return;
      const d = e.data as { type?: string; credit?: string } | undefined;
      if (d?.type === "avatar-models") {
        setCredit(d.credit || "");
      }
    };
    window.addEventListener("message", onMessage);

    return () => {
      window.removeEventListener("message", onMessage);
      const s = slots.get(identity);
      if (s) {
        s.iframeWindow = null;
        s.frameReady = false;
      }
    };
  }, [identity, slotRef]);

  return (
    <div className="avatar-babylon">
      <iframe
        ref={frameRef}
        src={src}
        title="Avatar GLB (choix du modèle dans le panneau)"
        onLoad={() => {
          const slot = slotRef.current.get(identity);
          if (slot && frameRef.current) {
            slot.iframeWindow = frameRef.current.contentWindow;
          }
        }}
      />
      <span className="panel-title">Avatar GLB</span>
      <span className="avatar-credit">{credit}</span>
    </div>
  );
}
