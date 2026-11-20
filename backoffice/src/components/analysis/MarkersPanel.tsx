import { ActionIcon, Badge, Button, Card, Divider, Group, Paper, Stack, Text, Tooltip } from "@mantine/core";
import { Bookmark, Play, Plus, Trash2 } from "lucide-react";
import type { CoachMarker } from "../../types";
import { formatPts } from "./timeUtils";

export interface MarkersPanelProps {
  markers: CoachMarker[];
  canManageMarkers: boolean;
  onAddClick: () => void;
  onSeek: (ptsMs: number) => void;
  onDelete: (markerId: string) => void;
  deleting: boolean;
}

const CATEGORY_COLORS: Record<string, string> = {
  technique: "blue",
  sprint: "orange",
  incident: "red",
  general: "gray",
};

export function MarkersPanel({
  markers,
  canManageMarkers,
  onAddClick,
  onSeek,
  onDelete,
  deleting,
}: MarkersPanelProps) {
  return (
    <Card withBorder radius="md" p="md">
      <Group justify="space-between" mb="sm">
        <Group gap="xs">
          <Bookmark size={18} />
          <Text fw={600} size="sm">
            Marqueurs coach ({markers.length})
          </Text>
        </Group>
        {canManageMarkers && (
          <Button
            size="xs"
            variant="light"
            leftSection={<Plus size={14} />}
            onClick={onAddClick}
          >
            Ajouter
          </Button>
        )}
      </Group>

      <Divider mb="sm" />

      {markers.length > 0 ? (
        <Stack gap="xs" style={{ maxHeight: 360, overflowY: "auto" }}>
          {markers.map((m) => {
            const markerId = m.id || m._id || "";
            const badgeColor =
              CATEGORY_COLORS[m.category?.toLowerCase()] || "teal";

            return (
              <Paper
                key={markerId || `${m.pts_ms}-${m.label}`}
                withBorder
                p="xs"
                radius="sm"
                style={{
                  cursor: "pointer",
                  backgroundColor: "#161b22",
                }}
                onClick={() => onSeek(m.pts_ms)}
              >
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <Stack gap={2} style={{ flex: 1 }}>
                    <Group gap="xs" wrap="nowrap">
                      <Badge size="xs" color={badgeColor}>
                        {m.category || "technique"}
                      </Badge>
                      <Badge
                        size="xs"
                        variant="outline"
                        color="yellow"
                        leftSection={<Play size={10} />}
                      >
                        {formatPts(m.pts_ms)}
                      </Badge>
                    </Group>
                    <Text size="sm" fw={600} mt={4}>
                      {m.label}
                    </Text>
                    {m.note && (
                      <Text size="xs" c="dimmed">
                        {m.note}
                      </Text>
                    )}
                    {m.author_name && (
                      <Text size="xs" c="dimmed" mt={2}>
                        Par {m.author_name}
                      </Text>
                    )}
                  </Stack>

                  {canManageMarkers && markerId && (
                    <Tooltip label="Supprimer le marqueur">
                      <ActionIcon
                        size="sm"
                        color="red"
                        variant="subtle"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(markerId);
                        }}
                        loading={deleting}
                      >
                        <Trash2 size={14} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Group>
              </Paper>
            );
          })}
        </Stack>
      ) : (
        <Text size="xs" c="dimmed" py="sm">
          Aucun marqueur coach pour le moment.
        </Text>
      )}
    </Card>
  );
}
