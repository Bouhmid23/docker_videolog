import type { WorkoutBlockPreview } from "../../types";

export interface WorkoutIntensityBarProps {
  blocks: WorkoutBlockPreview[];
  height?: number;
}

function blockColor(intensityPct: number): string {
  if (intensityPct < 60) return "#6b7280";
  if (intensityPct < 76) return "#339af0";
  if (intensityPct < 91) return "#51cf66";
  if (intensityPct < 106) return "#fcc419";
  if (intensityPct < 121) return "#ff922b";
  return "#fa5252";
}

export function WorkoutIntensityBar({
  blocks,
  height = 36,
}: WorkoutIntensityBarProps) {
  if (!blocks || blocks.length === 0) return null;
  const totalDur = blocks.reduce((acc, b) => acc + Math.max(1, b.duration_s), 0);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: 1,
        height,
        width: "100%",
        backgroundColor: "#0f1318",
        borderRadius: 4,
        padding: 2,
        overflow: "hidden",
      }}
    >
      {blocks.map((b) => {
        const wPct = (Math.max(1, b.duration_s) / totalDur) * 100;
        const hPct = Math.max(20, Math.min(100, (b.intensity_pct / 130) * 100));
        return (
          <div
            key={b.index}
            title={`${b.kind} · ${Math.round(b.duration_s / 60)} min @ ${b.intensity_pct}%`}
            style={{
              width: `${wPct}%`,
              height: `${hPct}%`,
              backgroundColor: blockColor(b.intensity_pct),
              borderRadius: 2,
            }}
          />
        );
      })}
    </div>
  );
}
