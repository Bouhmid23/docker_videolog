import { Badge, Card, Divider, Group, Paper, Stack, Text } from "@mantine/core";
import { AlertTriangle, Play } from "lucide-react";
import type { SessionAlert } from "../../types";
import { formatPts } from "./timeUtils";

export interface AlertsPanelProps {
  alerts: SessionAlert[];
  onSeek: (ptsMs: number) => void;
}

export function AlertsPanel({ alerts, onSeek }: AlertsPanelProps) {
  return (
    <Card withBorder radius="md" p="md">
      <Group justify="space-between" mb="sm">
        <Group gap="xs">
          <AlertTriangle size={18} color="#fa5252" />
          <Text fw={600} size="sm">
            Alertes session ({alerts.length})
          </Text>
        </Group>
      </Group>

      <Divider mb="sm" />

      {alerts.length > 0 ? (
        <Stack gap="xs" style={{ maxHeight: 360, overflowY: "auto" }}>
          {alerts.map((a, idx) => {
            const alertPts =
              a.pts_ms != null && a.pts_ms > 0
                ? a.pts_ms
                : a.ts
                ? a.ts * 1000
                : 0;

            return (
              <Paper
                key={a.id || a._id || idx}
                withBorder
                p="xs"
                radius="sm"
                style={{
                  cursor: "pointer",
                  backgroundColor: "#161b22",
                  borderLeft: "3px solid #fa5252",
                }}
                onClick={() => onSeek(alertPts)}
              >
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <Stack gap={2} style={{ flex: 1 }}>
                    <Group gap="xs">
                      <Badge size="xs" color="red">
                        {a.type}
                      </Badge>
                      <Badge
                        size="xs"
                        variant="outline"
                        color="yellow"
                        leftSection={<Play size={10} />}
                      >
                        {formatPts(alertPts)}
                      </Badge>
                    </Group>
                    <Text size="xs" fw={500} mt={4}>
                      {a.message || a.type}
                    </Text>
                    {a.value != null && a.threshold != null && (
                      <Text size="xs" c="dimmed">
                        Valeur : {a.value} (seuil : {a.threshold})
                      </Text>
                    )}
                    {a.participant && (
                      <Text size="xs" c="dimmed">
                        Athlète : {a.participant}
                      </Text>
                    )}
                  </Stack>
                </Group>
              </Paper>
            );
          })}
        </Stack>
      ) : (
        <Text size="xs" c="dimmed" py="sm">
          Aucune alerte enregistrée pour cette session.
        </Text>
      )}
    </Card>
  );
}
