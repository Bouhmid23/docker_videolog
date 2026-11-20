import { useEffect, useMemo, useRef } from "react";
import { Group, Paper, Stack, Text } from "@mantine/core";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { ActivityStreams } from "../../types";
import { formatPts } from "./timeUtils";

export interface ActivityStreamsChartProps {
  streams: ActivityStreams;
  currentTimeMs?: number;
  onSeek?: (ms: number) => void;
}

export function ActivityStreamsChart({
  streams,
  currentTimeMs,
  onSeek,
}: ActivityStreamsChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const currentTimeRef = useRef<number | undefined>(currentTimeMs);
  const onSeekRef = useRef(onSeek);

  currentTimeRef.current = currentTimeMs;
  onSeekRef.current = onSeek;

  const hasPower = useMemo(
    () => (streams.power || []).some((v) => v != null),
    [streams.power]
  );
  const hasHr = useMemo(
    () => (streams.hr || []).some((v) => v != null),
    [streams.hr]
  );
  const hasSpeed = useMemo(
    () => (streams.speed || []).some((v) => v != null),
    [streams.speed]
  );

  const data = useMemo<uPlot.AlignedData>(() => {
    const t = streams.t || [];
    return [
      t,
      streams.power || t.map(() => null),
      streams.hr || t.map(() => null),
      streams.speed || t.map(() => null),
    ];
  }, [streams]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !streams.t || streams.t.length === 0) return;

    const width = Math.max(320, container.clientWidth || 720);
    const opts: uPlot.Options = {
      width,
      height: 240,
      padding: [12, 12, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
      scales: {
        x: { time: false },
        power: { auto: true },
        hr: { auto: true },
        speed: { auto: true },
      },
      axes: [
        {
          stroke: "#6b7280",
          grid: { stroke: "#1f2937", width: 1 },
          values: (_u, vals) => vals.map((v) => formatPts(v * 1000)),
        },
        {
          scale: hasPower ? "power" : "speed",
          stroke: hasPower ? "#a855f7" : "#339af0",
          grid: { stroke: "#1f2937", width: 1 },
          size: 48,
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
          label: "Puissance (W)",
          scale: "power",
          stroke: "#a855f7",
          width: 1.5,
          show: hasPower,
          spanGaps: true,
        },
        {
          label: "FC (bpm)",
          scale: "hr",
          stroke: "#ff6b6b",
          width: 1.5,
          show: hasHr,
          spanGaps: true,
        },
        {
          label: "Vitesse (km/h)",
          scale: "speed",
          stroke: "#339af0",
          width: 1.5,
          show: hasSpeed,
          spanGaps: true,
        },
      ],
      hooks: {
        draw: [
          (u) => {
            if (currentTimeRef.current == null) return;
            const xPos = u.valToPos(currentTimeRef.current / 1000, "x", true);
            if (!Number.isFinite(xPos)) return;
            if (xPos < u.bbox.left || xPos > u.bbox.left + u.bbox.width) return;
            const ctx = u.ctx;
            ctx.save();
            ctx.strokeStyle = "#fcc419";
            ctx.lineWidth = 2 * devicePixelRatio;
            ctx.beginPath();
            ctx.moveTo(xPos, u.bbox.top);
            ctx.lineTo(xPos, u.bbox.top + u.bbox.height);
            ctx.stroke();
            ctx.restore();
          },
        ],
        ready: [
          (u) => {
            if (!onSeekRef.current) return;
            u.over.style.cursor = "pointer";
            u.over.addEventListener("click", (e) => {
              const rect = u.over.getBoundingClientRect();
              const sec = u.posToVal(e.clientX - rect.left, "x");
              if (Number.isFinite(sec)) {
                onSeekRef.current?.(Math.max(0, sec * 1000));
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
          plotRef.current.setSize({ width: w, height: 240 });
        }
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, [data, hasPower, hasHr, hasSpeed, streams.t]);

  useEffect(() => {
    if (plotRef.current && currentTimeMs != null) {
      plotRef.current.redraw(false);
    }
  }, [currentTimeMs]);

  return (
    <Stack gap="xs">
      <Group gap="md">
        {hasPower && (
          <Group gap={6}>
            <div style={{ width: 12, height: 3, backgroundColor: "#a855f7", borderRadius: 2 }} />
            <Text size="xs" c="dimmed">Puissance (W)</Text>
          </Group>
        )}
        {hasHr && (
          <Group gap={6}>
            <div style={{ width: 12, height: 3, backgroundColor: "#ff6b6b", borderRadius: 2 }} />
            <Text size="xs" c="dimmed">FC (bpm)</Text>
          </Group>
        )}
        {hasSpeed && (
          <Group gap={6}>
            <div style={{ width: 12, height: 3, backgroundColor: "#339af0", borderRadius: 2 }} />
            <Text size="xs" c="dimmed">Vitesse (km/h)</Text>
          </Group>
        )}
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
