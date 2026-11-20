import type { ConnectionQuality } from "livekit-client";
import type { MicInfo, NetSample, PostureDisplay } from "../types";
import { TraineeHeaderBar } from "./TraineeHeaderBar";

interface SoloEmitterBandProps {
  identity: string;
  displayName?: string;
  role?: string;
  mic?: MicInfo;
  quality?: ConnectionQuality;
  posture?: PostureDisplay;
  netListenersRef: React.MutableRefObject<
    Map<string, Set<(s: NetSample) => void>>
  >;
}

export function SoloEmitterBand({
  identity,
  displayName,
  role,
  mic,
  quality,
  posture,
  netListenersRef,
}: SoloEmitterBandProps) {
  return (
    <TraineeHeaderBar
      identity={identity}
      displayName={displayName}
      role={role}
      mic={mic}
      quality={quality}
      posture={posture}
      defaultSyncText="Pas de vidéo reçue : posture et télémétrie seulement."
      netListenersRef={netListenersRef}
    />
  );
}
