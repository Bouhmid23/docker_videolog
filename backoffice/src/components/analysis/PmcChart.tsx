import { useEffect, useMemo, useRef } from "react";
import { Group, Paper, Stack, Text } from "@mantine/core";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { DailyMetricPoint } from "../../types";

export interface PmcChartProps {
  items: DailyMetricPoint[];
}

export function PmcChart({ items }: PmcChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  const data = useMemo<uPlot.AlignedData>(() => {
    const ts: number[] = [];
    const ctl: number[] = [];
    const atl: number[] = [];
    const tsb: number[] = [];
    for (const p of items) {
      ts.push(Math.floor(new Date(`${p.date}T00:00:00Z`).getTime() / 1000));
      ctl.push(p.ctl);
      atl.push(p.atl);
      tsb.push(p.tsb);
    }
    return [ts, ctl, atl, tsb];
  }, [items]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || items.length === 0) return;

    const width = Math.max(320, container.clientWidth || 720);
    const opts: uPlot.Options = {
      width,
      height: 260,
      padding: [12, 12, 0, 0],
      legend: { show: false },
      scales: {
        x: { time: true },
        y: { auto: true },
      },
      axes: [
        {
          stroke: "#6b7280",
          grid: { stroke: "#1f2937", width: 1 },
        },
        {
          stroke: "#98a3b0",
          grid: { stroke: "#1f2937", width: 1 },
          size: 46,
        },
      ],
      series: [
        {},
        { label: "Forme de fond", stroke: "#18abf8", width: 2.2 },
        { label: "Fatigue récente", stroke: "#ff922b", width: 1.8 },
        { label: "Fraîcheur", stroke: "#3ddc97", width: 1.8 },
      ],
    };

    const plot = new uPlot(opts, data, container);
    plotRef.current = plot;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 100 && plotRef.current) {
          plotRef.current.setSize({ width: w, height: 260 });
        }
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, [data, items.length]);

  return (
    <Stack gap="xs">
      <Group gap="md">
        <Group gap={6}>
          <div style={{ width: 12, height: 3, backgroundColor: "#18abf8", borderRadius: 2 }} />
          <Text size="xs" c="dimmed">Forme de fond (42j)</Text>
        </Group>
        <Group gap={6}>
          <div style={{ width: 12, height: 3, backgroundColor: "#ff922b", borderRadius: 2 }} />
          <Text size="xs" c="dimmed">Fatigue récente (7j)</Text>
        </Group>
        <Group gap={6}>
          <div style={{ width: 12, height: 3, backgroundColor: "#3ddc97", borderRadius: 2 }} />
          <Text size="xs" c="dimmed">Fraîcheur (équilibre)</Text>
        </Group>
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
