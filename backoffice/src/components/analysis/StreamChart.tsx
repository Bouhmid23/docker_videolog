import { useEffect, useMemo, useRef } from "react";
import { Group, Paper, Stack, Text } from "@mantine/core";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { TelemetryPoint } from "../../types";
import { formatPts } from "./timeUtils";

export interface StreamChartProps {
  telemetry: TelemetryPoint[];
  currentTimeMs: number;
  durationMs: number;
  onSeek: (ptsMs: number) => void;
}

export function StreamChart({
  telemetry,
  currentTimeMs,
  durationMs,
  onSeek,
}: StreamChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const currentTimeRef = useRef<number>(currentTimeMs);
  const onSeekRef = useRef(onSeek);

  currentTimeRef.current = currentTimeMs;
  onSeekRef.current = onSeek;

  const { data, maxPts } = useMemo(() => {
    const hasVideoDuration = durationMs > 0;
    const sorted = [...telemetry]
      .filter(
        (p) => p.pts_ms >= 0 && (!hasVideoDuration || p.pts_ms <= durationMs)
      )
      .sort((a, b) => a.pts_ms - b.pts_ms);
    const xs: number[] = [];
    const speeds: (number | null)[] = [];
    const hrs: (number | null)[] = [];
    let maxP = hasVideoDuration ? durationMs : 5000;

    for (const p of sorted) {
      if (!hasVideoDuration && p.pts_ms > maxP) maxP = p.pts_ms;
      xs.push(p.pts_ms / 1000);
      speeds.push(p.speed != null ? Number(p.speed) : null);
      hrs.push(p.heart_rate != null ? Number(p.heart_rate) : null);
    }

    return {
      data: [xs, speeds, hrs] as uPlot.AlignedData,
      maxPts: maxP,
    };
  }, [telemetry, durationMs]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || data[0].length === 0) return;

    const width = Math.max(300, container.clientWidth || 720);
    const opts: uPlot.Options = {
      width,
      height: 200,
      padding: [12, 12, 0, 0],
      legend: { show: false },
      cursor: {
        drag: { x: false, y: false },
      },
      scales: {
        x: {
          time: false,
          auto: false,
          min: 0,
          max: maxPts / 1000,
          range: () => [0, maxPts / 1000],
        },
        speed: { auto: true },
        hr: { auto: true },
      },
      axes: [
        {
          stroke: "#6b7280",
          grid: { stroke: "#1f2937", width: 1 },
          ticks: { stroke: "#374151" },
          values: (_u, vals) => vals.map((v) => formatPts(v * 1000)),
        },
        {
          scale: "speed",
          stroke: "#339af0",
          grid: { stroke: "#1f2937", width: 1 },
          size: 46,
        },
        {
          scale: "hr",
          side: 1,
          stroke: "#ff6b6b",
          grid: { show: false },
          size: 46,
        },
      ],
      series: [
        {},
        {
          label: "Vitesse (km/h)",
          scale: "speed",
          stroke: "#339af0",
          width: 2,
          spanGaps: true,
        },
        {
          label: "FC (bpm)",
          scale: "hr",
          stroke: "#ff6b6b",
          width: 2,
          spanGaps: true,
        },
      ],
      hooks: {
        draw: [
          (u) => {
            const xVal = currentTimeRef.current / 1000;
            const xPos = u.valToPos(xVal, "x", true);
            if (!Number.isFinite(xPos)) return;
            const { top, height } = u.bbox;
            if (xPos < u.bbox.left || xPos > u.bbox.left + u.bbox.width) return;
            const ctx = u.ctx;
            ctx.save();
            ctx.strokeStyle = "#fcc419";
            ctx.lineWidth = 2 * devicePixelRatio;
            ctx.beginPath();
            ctx.moveTo(xPos, top);
            ctx.lineTo(xPos, top + height);
            ctx.stroke();
            ctx.restore();
          },
        ],
        ready: [
          (u) => {
            u.over.style.cursor = "pointer";
            u.over.addEventListener("click", (e) => {
              const rect = u.over.getBoundingClientRect();
              const left = e.clientX - rect.left;
              const sec = u.posToVal(left, "x");
              if (Number.isFinite(sec)) {
                onSeekRef.current(
                  Math.min(maxPts, Math.max(0, sec * 1000))
                );
              }
            });
          },
        ],
      },
    };

    const plot = new uPlot(opts, data, container);
    plotRef.current = plot;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 100 && plotRef.current) {
          plotRef.current.setSize({ width: w, height: 200 });
        }
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, [data, maxPts, telemetry.length]);

  useEffect(() => {
    if (plotRef.current) {
      plotRef.current.redraw(true, false);
    }
  }, [currentTimeMs]);

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="center">
        <Group gap="md">
          <Group gap={6}>
            <div
              style={{
                width: 12,
                height: 3,
                backgroundColor: "#339af0",
                borderRadius: 2,
              }}
            />
            <Text size="xs" c="dimmed">
              Vitesse (km/h)
            </Text>
          </Group>
          <Group gap={6}>
            <div
              style={{
                width: 12,
                height: 3,
                backgroundColor: "#ff6b6b",
                borderRadius: 2,
              }}
            />
            <Text size="xs" c="dimmed">
              FC (bpm)
            </Text>
          </Group>
          <Group gap={6}>
            <div
              style={{
                width: 12,
                height: 3,
                backgroundColor: "#fcc419",
                borderRadius: 2,
              }}
            />
            <Text size="xs" c="dimmed">
              Lecture
            </Text>
          </Group>
        </Group>
        <Text size="xs" c="dimmed">
          Cliquez sur la courbe pour vous déplacer
        </Text>
      </Group>

      <Paper
        withBorder
        p="xs"
        radius="md"
        style={{ backgroundColor: "#10141a", overflow: "hidden" }}
      >
        <div ref={containerRef} style={{ width: "100%" }} />
      </Paper>
    </Stack>
  );
}
