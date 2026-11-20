import { useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Divider,
  Grid,
  Group,
  Loader,
  MultiSelect,
  Paper,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MessageSquare, RefreshCw, Trash2 } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ActivityStreamsChart } from "../../components/analysis/ActivityStreamsChart";
import { formatPts } from "../../components/analysis/timeUtils";
import { ApiError, apiFetch } from "../../lib/api";
import { formatSportLabel } from "../../lib/constants";
import type {
  ActivityComment,
  ActivityDetail,
  ActivityStreams,
} from "../../types";

function formatDur(s: number): string {
  const sec = Math.max(0, Math.round(s));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const rem = sec % 60;
  return h > 0
    ? `${h}h${m.toString().padStart(2, "0")}m`
    : `${m}:${rem.toString().padStart(2, "0")}`;
}

export function ActivityDetailPage() {
  const { activityId = "" } = useParams<{ activityId: string }>();
  const { me, activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [commentBody, setCommentBody] = useState("");

  const {
    data: activity,
    isLoading,
    error,
  } = useQuery<ActivityDetail>({
    queryKey: ["activity-detail", orgId, activityId],
    queryFn: () =>
      apiFetch<ActivityDetail>(`/v1/orgs/${orgId}/activities/${activityId}`),
    enabled: Boolean(orgId && activityId),
  });

  const { data: streams } = useQuery<ActivityStreams>({
    queryKey: ["activity-streams", orgId, activityId],
    queryFn: () =>
      apiFetch<ActivityStreams>(
        `/v1/orgs/${orgId}/activities/${activityId}/streams`
      ),
    enabled: Boolean(orgId && activityId),
  });

  const { data: commentsData } = useQuery<{ items: ActivityComment[] }>({
    queryKey: ["activity-comments", orgId, activityId],
    queryFn: () =>
      apiFetch<{ items: ActivityComment[] }>(
        `/v1/orgs/${orgId}/activities/${activityId}/comments`
      ),
    enabled: Boolean(orgId && activityId),
  });

  const rpeMutation = useMutation({
    mutationFn: (rpe: number) =>
      apiFetch(`/v1/orgs/${orgId}/activities/${activityId}`, {
        method: "PATCH",
        body: JSON.stringify({ rpe }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["activity-detail", orgId, activityId],
      });
      notifications.show({ color: "teal", message: "RPE mis à jour." });
    },
  });

  const recalcMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/activities/${activityId}/recalculate`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["activity-detail", orgId, activityId],
      });
      notifications.show({ color: "teal", message: "Métriques recalculées." });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/activities/${activityId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["activities", orgId] });
      notifications.show({ color: "teal", message: "Activité supprimée." });
      navigate("/app/activities");
    },
  });

  const shareMutation = useMutation({
    mutationFn: (sharedOrgIds: string[]) =>
      apiFetch(`/v1/orgs/${orgId}/activities/${activityId}/sharing`, {
        method: "PATCH",
        body: JSON.stringify({ shared_org_ids: sharedOrgIds }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["activity", orgId, activityId] });
      notifications.show({ color: "teal", message: "Partage multi-clubs mis à jour." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur lors du partage.",
      });
    },
  });

  const commentMutation = useMutation({
    mutationFn: (body: string) =>
      apiFetch(`/v1/orgs/${orgId}/activities/${activityId}/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      }),
    onSuccess: () => {
      setCommentBody("");
      queryClient.invalidateQueries({
        queryKey: ["activity-comments", orgId, activityId],
      });
    },
  });

  if (isLoading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  if (error || !activity) {
    return (
      <Alert color="red" title="Erreur">
        {error instanceof ApiError
          ? error.detail
          : "Impossible de charger l'activité."}
      </Alert>
    );
  }

  const m = activity.metrics || {};
  const powerZonesDist = m.time_in_power_zones || {};
  const hrZonesDist = m.time_in_hr_zones || {};
  const totalPowerZ = Object.values(powerZonesDist).reduce((a, b) => a + b, 0);
  const totalHrZ = Object.values(hrZonesDist).reduce((a, b) => a + b, 0);
  const video = activity.video_assets?.find((v) => Boolean(v.mp4_url));
  const comments = commentsData?.items ?? [];

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="center" wrap="wrap">
        <Button
          component={Link}
          to="/app/activities"
          variant="subtle"
          leftSection={<ArrowLeft size={16} />}
        >
          Retour aux activités
        </Button>

        <Group gap="xs">
          <Button
            size="xs"
            variant="light"
            leftSection={<RefreshCw size={14} />}
            loading={recalcMutation.isPending}
            onClick={() => recalcMutation.mutate()}
          >
            Recalculer
          </Button>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<Trash2 size={14} />}
            loading={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate()}
          >
            Supprimer
          </Button>
        </Group>
      </Group>

      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={2}>{activity.title}</Title>
          <Text size="sm" c="dimmed">
            {activity.athlete_name || activity.uid} ·{" "}
            {new Date(activity.start_time).toLocaleString("fr-FR")}
          </Text>
        </div>
        <Group gap="xs">
          <Badge color="cyan">{formatSportLabel(activity.sport)}</Badge>
          <Badge variant="outline" color="gray">
            {activity.source}
          </Badge>
          {(me?.memberships.length ?? 0) > 1 && activity.org_id === orgId && (
            <MultiSelect
              size="xs"
              placeholder="Partager avec un autre club/coach"
              data={me!.memberships
                .filter((m) => m.org_id !== orgId)
                .map((m) => ({
                  value: m.org_id,
                  label: m.organization?.name ?? m.org_id,
                }))}
              value={activity.shared_org_ids ?? []}
              onChange={(vals) => shareMutation.mutate(vals)}
              style={{ minWidth: 230 }}
            />
          )}
        </Group>
      </Group>

      {activity.planned_workout_summary && (
        <Card withBorder radius="md" p="sm">
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap="xs">
              <Badge color="cyan" variant="light">
                PRÉVU VS RÉALISÉ
              </Badge>
              <Text fw={600} size="sm">
                {activity.planned_workout_summary.title}
              </Text>
              {activity.planned_workout_summary.date && (
                <Text size="xs" c="dimmed">
                  ({activity.planned_workout_summary.date})
                </Text>
              )}
            </Group>
            <Group gap="md">
              <Text size="xs" c="dimmed">
                Durée prévue :{" "}
                <Text span fw={600} c="#eef2f6">
                  {formatDur(activity.planned_workout_summary.estimated_duration_s)}
                </Text>{" "}
                / réalisée :{" "}
                <Text span fw={600} c="#18abf8">
                  {formatDur(activity.duration_s)}
                </Text>
              </Text>
              <Text size="xs" c="dimmed">
                Effort prévu :{" "}
                <Text span fw={600} c="#eef2f6">
                  {Math.round(activity.planned_workout_summary.estimated_tss)} pts
                </Text>{" "}
                / réalisé :{" "}
                <Text span fw={600} c="grape">
                  {m.tss != null ? `${Math.round(m.tss)} pts` : "—"}
                </Text>
              </Text>
              {activity.planned_workout_summary.compliance && (
                <Badge
                  color={activity.planned_workout_summary.compliance.color || "teal"}
                  variant="filled"
                >
                  Conformité {Math.round(activity.planned_workout_summary.compliance.score)}%
                </Badge>
              )}
            </Group>
          </Group>
        </Card>
      )}

      <SimpleGrid cols={{ base: 2, sm: 4, md: 7 }} spacing="xs">
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">DURÉE</Text>
          <Text fw={700} size="lg">{formatDur(activity.duration_s)}</Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">DISTANCE</Text>
          <Text fw={700} size="lg">
            {activity.distance_m > 0
              ? `${(activity.distance_m / 1000).toFixed(2)} km`
              : "—"}
          </Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">DÉNIVELÉ +</Text>
          <Text fw={700} size="lg">
            {m.elevation_gain_m != null ? `${Math.round(m.elevation_gain_m)} m` : "—"}
          </Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">PUISSANCE PONDÉRÉE / MOY.</Text>
          <Text fw={700} size="lg" c="#a855f7">
            {m.normalized_power != null
              ? `${Math.round(m.normalized_power)} W`
              : m.avg_power != null
              ? `${Math.round(m.avg_power)} W`
              : "—"}
          </Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">FC MOY / MAX</Text>
          <Text fw={700} size="lg" c="#ff6b6b">
            {m.avg_hr != null ? `${Math.round(m.avg_hr)}` : "—"} /{" "}
            {m.max_hr != null ? `${m.max_hr}` : "—"}
          </Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">EFFORT ({m.tss_method || "—"})</Text>
          <Text fw={700} size="lg" c="grape">
            {m.tss != null ? `${Math.round(m.tss)} pts` : "—"}
          </Text>
        </Paper>
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" c="dimmed">INTENSITÉ / RESSENTI</Text>
          <Group gap={6} align="center">
            <Text fw={700} size="lg">
              {m.intensity_factor != null ? m.intensity_factor.toFixed(2) : "—"}
            </Text>
            <Select
              size="xs"
              placeholder="Effort"
              style={{ width: 72 }}
              value={activity.rpe != null ? String(activity.rpe) : null}
              onChange={(v) => v && rpeMutation.mutate(Number(v))}
              data={Array.from({ length: 10 }, (_, i) => ({
                value: String(i + 1),
                label: `${i + 1}/10`,
              }))}
            />
          </Group>
        </Paper>
      </SimpleGrid>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <Stack gap="md">
            {video && (
              <Card withBorder radius="md" p={0} style={{ overflow: "hidden" }}>
                <video
                  ref={videoRef}
                  src={video.mp4_url}
                  controls
                  playsInline
                  style={{ width: "100%", display: "block", maxHeight: 420, backgroundColor: "#000" }}
                  onTimeUpdate={() => {
                    if (videoRef.current) {
                      setCurrentTimeMs(
                        Math.max(0, videoRef.current.currentTime * 1000 + (video.offset_ms || 0))
                      );
                    }
                  }}
                />
              </Card>
            )}

            <Card withBorder radius="md" p="md">
              <Title order={4} mb="sm">
                Courbes de séance (1 Hz)
              </Title>
              {streams && streams.t.length > 0 ? (
                <ActivityStreamsChart
                  streams={streams}
                  currentTimeMs={video ? currentTimeMs : undefined}
                  onSeek={
                    video
                      ? (ms) => {
                          if (videoRef.current) {
                            videoRef.current.currentTime = Math.max(
                              0,
                              (ms - (video.offset_ms || 0)) / 1000
                            );
                          }
                          setCurrentTimeMs(ms);
                        }
                      : undefined
                  }
                />
              ) : (
                <Text size="sm" c="dimmed">
                  Aucun flux temporel disponible.
                </Text>
              )}
            </Card>

            {activity.laps && activity.laps.length > 0 && (
              <Card withBorder radius="md" p="md">
                <Title order={4} mb="sm">
                  Tours / Intervalles ({activity.laps.length})
                </Title>
                <Table striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>#</Table.Th>
                      <Table.Th>Durée</Table.Th>
                      <Table.Th>Distance</Table.Th>
                      <Table.Th>P moy</Table.Th>
                      <Table.Th>FC moy</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {activity.laps.map((l) => (
                      <Table.Tr key={l.index}>
                        <Table.Td>{l.index}</Table.Td>
                        <Table.Td>{formatDur(l.duration_s)}</Table.Td>
                        <Table.Td>{(l.distance_m / 1000).toFixed(2)} km</Table.Td>
                        <Table.Td>{l.avg_power != null ? `${l.avg_power} W` : "—"}</Table.Td>
                        <Table.Td>{l.avg_hr != null ? `${l.avg_hr} bpm` : "—"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Card>
            )}
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 4 }}>
          <Stack gap="md">
            {totalPowerZ > 0 && (
              <Card withBorder radius="md" p="md">
                <Title order={5} mb="sm">Temps par zone de puissance</Title>
                <Stack gap="xs">
                  {Object.entries(powerZonesDist).map(([z, sec]) => (
                    <div key={z}>
                      <Group justify="space-between" mb={2}>
                        <Text size="xs" fw={600}>{z}</Text>
                        <Text size="xs" c="dimmed">{formatPts(sec * 1000)}</Text>
                      </Group>
                      <Progress value={(sec / totalPowerZ) * 100} color="grape" size="sm" />
                    </div>
                  ))}
                </Stack>
              </Card>
            )}

            {totalHrZ > 0 && (
              <Card withBorder radius="md" p="md">
                <Title order={5} mb="sm">Temps par zone cardiaque</Title>
                <Stack gap="xs">
                  {Object.entries(hrZonesDist).map(([z, sec]) => (
                    <div key={z}>
                      <Group justify="space-between" mb={2}>
                        <Text size="xs" fw={600}>{z}</Text>
                        <Text size="xs" c="dimmed">{formatPts(sec * 1000)}</Text>
                      </Group>
                      <Progress value={(sec / totalHrZ) * 100} color="red" size="sm" />
                    </div>
                  ))}
                </Stack>
              </Card>
            )}

            {m.mean_max_power && Object.keys(m.mean_max_power).length > 0 && (
              <Card withBorder radius="md" p="md">
                <Title order={5} mb="sm">Records de puissance (Mean-Max)</Title>
                <SimpleGrid cols={3} spacing="xs">
                  {Object.entries(m.mean_max_power).map(([dur, val]) => (
                    <Paper key={dur} withBorder p="xs" radius="sm" style={{ textAlign: "center" }}>
                      <Text size="xs" c="dimmed">{Number(dur) >= 60 ? `${Number(dur) / 60}m` : `${dur}s`}</Text>
                      <Text size="sm" fw={700}>{Math.round(val)} W</Text>
                    </Paper>
                  ))}
                </SimpleGrid>
              </Card>
            )}

            <Card withBorder radius="md" p="md">
              <Group gap="xs" mb="sm">
                <MessageSquare size={16} />
                <Title order={5}>Commentaires ({comments.length})</Title>
              </Group>
              <Divider mb="sm" />
              <Stack gap="xs" mb="sm">
                {comments.map((c) => (
                  <Paper key={c.id} withBorder p="xs" radius="sm">
                    <Group justify="space-between" mb={2}>
                      <Text size="xs" fw={600}>{c.author_name || c.author_uid}</Text>
                      <Text size="xs" c="dimmed">
                        {new Date(c.created_at).toLocaleDateString("fr-FR")}
                      </Text>
                    </Group>
                    <Text size="sm">{c.body}</Text>
                  </Paper>
                ))}
              </Stack>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!commentBody.trim()) return;
                  commentMutation.mutate(commentBody.trim());
                }}
              >
                <Stack gap="xs">
                  <Textarea
                    placeholder="Débriefing coach / ressenti athlète..."
                    value={commentBody}
                    onChange={(e) => setCommentBody(e.currentTarget.value)}
                    rows={2}
                  />
                  <Button
                    type="submit"
                    size="xs"
                    loading={commentMutation.isPending}
                    disabled={!commentBody.trim()}
                  >
                    Publier
                  </Button>
                </Stack>
              </form>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
