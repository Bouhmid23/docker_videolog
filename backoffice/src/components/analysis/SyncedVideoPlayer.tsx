import type { RefObject } from "react";
import { Alert, Card, Group, Paper, Select, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { Activity, Clock, Gauge, Heart, VideoOff, Zap } from "lucide-react";
import type { CoachMarker, SessionRecording, TelemetryPoint } from "../../types";
import { formatPts, formatPtsWithTenths } from "./timeUtils";

const MARKER_HEX: Record<string, string> = {
  technique: "#339af0",
  sprint: "#ff922b",
  incident: "#fa5252",
  general: "#868e96",
};

export interface SyncedVideoPlayerProps {
  videoRef: RefObject<HTMLVideoElement>;
  recordings: SessionRecording[];
  selectedRecordingIndex: number;
  onSelectRecording: (index: number) => void;
  onTimeUpdate: () => void;
  onLoadedMetadata: () => void;
  closestTelemetry: TelemetryPoint | null;
  hasPower?: boolean;
  currentTimeMs: number;
  durationMs?: number;
  markers?: CoachMarker[];
  onSeek?: (ptsMs: number) => void;
}

export function SyncedVideoPlayer({
  videoRef,
  recordings,
  selectedRecordingIndex,
  onSelectRecording,
  onTimeUpdate,
  onLoadedMetadata,
  closestTelemetry,
  hasPower = false,
  currentTimeMs,
  durationMs = 0,
  markers = [],
  onSeek,
}: SyncedVideoPlayerProps) {
  const activeRecording = recordings[selectedRecordingIndex] ?? null;

  return (
    <Stack gap="md">
      <Card withBorder radius="md" p={0} style={{ overflow: "hidden" }}>
        {recordings.length > 0 && activeRecording ? (
          <div>
            {recordings.length > 1 && (
              <Paper p="xs" withBorder style={{ borderBottom: "1px solid #21262d" }}>
                <Group justify="space-between">
                  <Text size="xs" fw={500}>
                    Enregistrements ({recordings.length})
                  </Text>
                  <Select
                    size="xs"
                    value={String(selectedRecordingIndex)}
                    onChange={(val) => onSelectRecording(Number(val ?? 0))}
                    data={recordings.map((r, i) => ({
                      value: String(i),
                      label: `Enregistrement ${i + 1}${
                        r.duration_s ? ` (${Math.round(r.duration_s)}s)` : ""
                      }`,
                    }))}
                  />
                </Group>
              </Paper>
            )}
            <video
              ref={videoRef}
              src={activeRecording.mp4_url}
              controls
              playsInline
              style={{
                width: "100%",
                display: "block",
                backgroundColor: "#000",
                maxHeight: 480,
              }}
              onTimeUpdate={onTimeUpdate}
              onLoadedMetadata={onLoadedMetadata}
            />
            {durationMs > 0 && (
              <div
                style={{
                  padding: "10px 14px",
                  backgroundColor: "#10141a",
                  borderTop: "1px solid #21262d",
                }}
              >
                <div
                  role="slider"
                  aria-label="Timeline des marqueurs"
                  aria-valuemin={0}
                  aria-valuemax={durationMs}
                  aria-valuenow={Math.round(currentTimeMs)}
                  tabIndex={0}
                  onClick={(e) => {
                    if (!onSeek) return;
                    const rect = e.currentTarget.getBoundingClientRect();
                    const ratio = Math.min(
                      1,
                      Math.max(0, (e.clientX - rect.left) / rect.width)
                    );
                    onSeek(Math.round(ratio * durationMs));
                  }}
                  style={{
                    position: "relative",
                    height: 22,
                    borderRadius: 6,
                    backgroundColor: "#1c222b",
                    cursor: onSeek ? "pointer" : "default",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 8,
                      bottom: 8,
                      width: `${Math.min(100, Math.max(0, (currentTimeMs / durationMs) * 100))}%`,
                      backgroundColor: "#18abf8",
                      borderRadius: 4,
                      pointerEvents: "none",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      top: 2,
                      bottom: 2,
                      width: 2,
                      left: `${Math.min(100, Math.max(0, (currentTimeMs / durationMs) * 100))}%`,
                      backgroundColor: "#fcc419",
                      pointerEvents: "none",
                    }}
                  />
                  {markers
                    .filter((m) => m.pts_ms >= 0 && m.pts_ms <= durationMs)
                    .map((m) => {
                      const pct = (m.pts_ms / durationMs) * 100;
                      const color =
                        MARKER_HEX[m.category?.toLowerCase()] ?? "#20c997";
                      const key = m.id || m._id || `${m.pts_ms}-${m.label}`;
                      return (
                        <Tooltip
                          key={key}
                          label={`${formatPts(m.pts_ms)} · ${m.label} (${m.category || "technique"})`}
                        >
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSeek?.(m.pts_ms);
                            }}
                            style={{
                              position: "absolute",
                              left: `${pct}%`,
                              top: "50%",
                              transform: "translate(-50%, -50%)",
                              width: 12,
                              height: 12,
                              borderRadius: "50%",
                              backgroundColor: color,
                              border: "2px solid #0d1117",
                              cursor: "pointer",
                              padding: 0,
                              zIndex: 2,
                            }}
                            aria-label={`Marqueur ${m.label} à ${formatPts(m.pts_ms)}`}
                          />
                        </Tooltip>
                      );
                    })}
                </div>
                <Group justify="space-between" mt={4}>
                  <Text size="xs" c="dimmed" ff="monospace">
                    00:00
                  </Text>
                  <Text size="xs" c="dimmed">
                    {markers.length} marqueur{markers.length > 1 ? "s" : ""} sur la timeline
                  </Text>
                  <Text size="xs" c="dimmed" ff="monospace">
                    {formatPts(durationMs)}
                  </Text>
                </Group>
              </div>
            )}
          </div>
        ) : (
          <Alert
            icon={<VideoOff size={18} />}
            title="Aucun enregistrement vidéo"
            color="blue"
            m="md"
          >
            Aucun enregistrement MP4 n&apos;est disponible pour cette session. Les
            enregistrements apparaîtront une fois le traitement egress finalisé.
          </Alert>
        )}
      </Card>

      <SimpleGrid cols={{ base: 1, sm: hasPower ? 5 : 4 }} spacing="xs">
        <Paper withBorder radius="md" p="sm" style={{ textAlign: "center" }}>
          <Group justify="center" gap={4} mb={4}>
            <Gauge size={16} color="#339af0" />
            <Text size="xs" c="dimmed" fw={600}>
              VITESSE
            </Text>
          </Group>
          <Text size="xl" fw={700} c="#339af0">
            {closestTelemetry?.speed != null
              ? `${Number(closestTelemetry.speed).toFixed(1)}`
              : "--"}
          </Text>
          <Text size="xs" c="dimmed">
            km/h
          </Text>
        </Paper>

        {hasPower && (
          <Paper withBorder radius="md" p="sm" style={{ textAlign: "center" }}>
            <Group justify="center" gap={4} mb={4}>
              <Zap size={16} color="#a855f7" />
              <Text size="xs" c="dimmed" fw={600}>
                PUISSANCE
              </Text>
            </Group>
            <Text size="xl" fw={700} c="#a855f7">
              {closestTelemetry?.power_w != null
                ? `${Math.round(Number(closestTelemetry.power_w))}`
                : "--"}
            </Text>
            <Text size="xs" c="dimmed">
              W
            </Text>
          </Paper>
        )}

        <Paper withBorder radius="md" p="sm" style={{ textAlign: "center" }}>
          <Group justify="center" gap={4} mb={4}>
            <Heart size={16} color="#ff6b6b" />
            <Text size="xs" c="dimmed" fw={600}>
              CARDIO
            </Text>
          </Group>
          <Text size="xl" fw={700} c="#ff6b6b">
            {closestTelemetry?.heart_rate != null
              ? `${Math.round(Number(closestTelemetry.heart_rate))}`
              : "--"}
          </Text>
          <Text size="xs" c="dimmed">
            bpm
          </Text>
        </Paper>

        <Paper withBorder radius="md" p="sm" style={{ textAlign: "center" }}>
          <Group justify="center" gap={4} mb={4}>
            <Activity size={16} color="#51cf66" />
            <Text size="xs" c="dimmed" fw={600}>
              CADENCE
            </Text>
          </Group>
          <Text size="xl" fw={700} c="#51cf66">
            {closestTelemetry?.cadence != null
              ? `${Math.round(Number(closestTelemetry.cadence))}`
              : "--"}
          </Text>
          <Text size="xs" c="dimmed">
            rpm
          </Text>
        </Paper>

        <Paper withBorder radius="md" p="sm" style={{ textAlign: "center" }}>
          <Group justify="center" gap={4} mb={4}>
            <Clock size={16} color="#ffd43b" />
            <Text size="xs" c="dimmed" fw={600}>
              TEMPS
            </Text>
          </Group>
          <Text size="xl" fw={700} c="#ffd43b" ff="monospace">
            {formatPtsWithTenths(currentTimeMs)}
          </Text>
          <Text size="xs" c="dimmed">
            pts
          </Text>
        </Paper>
      </SimpleGrid>
    </Stack>
  );
}
