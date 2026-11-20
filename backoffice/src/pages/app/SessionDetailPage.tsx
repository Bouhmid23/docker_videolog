import { useCallback, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  CopyButton,
  Grid,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Slider,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, Check, Copy, RefreshCw, Video } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { AlertsPanel } from "../../components/analysis/AlertsPanel";
import { MarkersPanel } from "../../components/analysis/MarkersPanel";
import { StreamChart } from "../../components/analysis/StreamChart";
import { SyncedGpsMap } from "../../components/analysis/SyncedGpsMap";
import { SyncedVideoPlayer } from "../../components/analysis/SyncedVideoPlayer";
import { extractGpsFromTelemetry } from "../../components/analysis/gpsUtils";
import {
  findClosestTelemetry,
  formatPtsWithTenths,
} from "../../components/analysis/timeUtils";
import { ApiError, apiFetch } from "../../lib/api";
import type {
  CoachMarker,
  SessionAlert,
  SessionDetail,
  SessionRecording,
  TelemetryPoint,
} from "../../types";

export function SessionDetailPage() {
  const { roomName = "" } = useParams<{ roomName: string }>();
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const canManageMarkers =
    role === "owner" || role === "admin" || role === "coach";
  const queryClient = useQueryClient();

  const videoRef = useRef<HTMLVideoElement>(null);
  const [selectedRecordingIndex, setSelectedRecordingIndex] = useState(0);
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [videoDurationMs, setVideoDurationMs] = useState(0);
  const [selectedParticipant, setSelectedParticipant] = useState<string | null>(
    null
  );
  const [generatedActivityId, setGeneratedActivityId] = useState<string | null>(
    null
  );

  const [modalOpened, { open: openModal, close: closeModal }] =
    useDisclosure(false);
  const [markerPtsMs, setMarkerPtsMs] = useState<number>(0);
  const [markerLabel, setMarkerLabel] = useState("");
  const [markerCategory, setMarkerCategory] = useState("technique");
  const [markerNote, setMarkerNote] = useState("");

  const {
    data: session,
    isLoading: sessionLoading,
    error: sessionError,
  } = useQuery({
    queryKey: ["session-detail", orgId, roomName],
    queryFn: () =>
      apiFetch<SessionDetail>(`/v1/orgs/${orgId}/sessions/${roomName}`),
    enabled: Boolean(orgId && roomName),
  });

  const { data: telemetry = [] } = useQuery<TelemetryPoint[]>({
    queryKey: ["session-telemetry", orgId, roomName, selectedParticipant],
    queryFn: () => {
      const q = selectedParticipant
        ? `?participant=${encodeURIComponent(selectedParticipant)}`
        : "";
      return apiFetch<TelemetryPoint[]>(
        `/v1/orgs/${orgId}/sessions/${roomName}/telemetry${q}`
      );
    },
    enabled: Boolean(orgId && roomName),
  });

  const { data: alerts = [] } = useQuery<SessionAlert[]>({
    queryKey: ["session-alerts", orgId, roomName, selectedParticipant],
    queryFn: () => {
      const q = selectedParticipant
        ? `?participant=${encodeURIComponent(selectedParticipant)}`
        : "";
      return apiFetch<SessionAlert[]>(
        `/v1/orgs/${orgId}/sessions/${roomName}/alerts${q}`
      );
    },
    enabled: Boolean(orgId && roomName),
  });

  const { data: markers = [] } = useQuery<CoachMarker[]>({
    queryKey: ["session-markers", orgId, roomName],
    queryFn: () =>
      apiFetch<CoachMarker[]>(
        `/v1/orgs/${orgId}/sessions/${roomName}/markers`
      ),
    enabled: Boolean(orgId && roomName),
  });

  const createMarkerMutation = useMutation({
    mutationFn: (body: {
      pts_ms: number;
      label: string;
      category: string;
      note?: string | null;
    }) =>
      apiFetch<CoachMarker>(`/v1/orgs/${orgId}/sessions/${roomName}/markers`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["session-markers", orgId, roomName],
      });
      closeModal();
      setMarkerLabel("");
      setMarkerNote("");
      notifications.show({
        color: "teal",
        message: "Marqueur ajouté avec succès.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Erreur lors de la création du marqueur.",
      });
    },
  });

  const finishSessionMutation = useMutation({
    mutationFn: () =>
      apiFetch<{
        status: string;
        room_name: string;
        converted_count?: number;
        activity_ids?: string[];
      }>(`/v1/orgs/${orgId}/sessions/${roomName}/finish`, {
        method: "POST",
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({
        queryKey: ["session-detail", orgId, roomName],
      });
      queryClient.invalidateQueries({ queryKey: ["sessions", orgId] });
      queryClient.invalidateQueries({ queryKey: ["activities", orgId] });
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      if (res?.activity_ids?.length) {
        setGeneratedActivityId(res.activity_ids[0]);
      }
      const count = res?.converted_count ?? 0;
      notifications.show({
        color: "teal",
        message:
          count > 0
            ? `Session terminée et ${count} activité(s) générée(s) automatiquement.`
            : "Session terminée. L'accès à la room est désormais fermé.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Impossible de terminer la session.",
      });
    },
  });

  const convertLiveMutation = useMutation({
    mutationFn: () =>
      apiFetch<{
        converted_count: number;
        activities: Array<{ id: string; title?: string }>;
      }>(`/v1/orgs/${orgId}/activities/convert-live/${roomName}`, {
        method: "POST",
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["activities", orgId] });
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      if (res.activities?.length) {
        setGeneratedActivityId(res.activities[0].id);
      }
      notifications.show({
        color: res.converted_count > 0 ? "teal" : "blue",
        message:
          res.converted_count > 0
            ? `${res.converted_count} activité(s) synchronisée(s) vers l'historique & la forme (PMC).`
            : "Aucune nouvelle télémétrie exploitable à convertir (ou activité déjà générée).",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Impossible de générer l'activité depuis cette session.",
      });
    },
  });

  const deleteMarkerMutation = useMutation({
    mutationFn: (markerId: string) =>
      apiFetch(
        `/v1/orgs/${orgId}/sessions/${roomName}/markers/${markerId}`,
        { method: "DELETE" }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["session-markers", orgId, roomName],
      });
      notifications.show({
        color: "teal",
        message: "Marqueur supprimé.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Erreur lors de la suppression.",
      });
    },
  });

  const recordings: SessionRecording[] = useMemo(() => {
    return session?.recordings?.filter((r) => Boolean(r.mp4_url)) ?? [];
  }, [session]);

  const activeRecording = recordings[selectedRecordingIndex] ?? null;

  const effectiveDurationMs = useMemo(() => {
    if (videoDurationMs > 0 && Number.isFinite(videoDurationMs)) {
      return Math.round(videoDurationMs);
    }
    if (activeRecording?.duration_s && activeRecording.duration_s > 0) {
      return Math.round(activeRecording.duration_s * 1000);
    }
    return 0;
  }, [videoDurationMs, activeRecording?.duration_s]);

  const handleSelectRecording = useCallback((index: number) => {
    setSelectedRecordingIndex(index);
    setVideoDurationMs(0);
    setCurrentTimeMs(0);
  }, []);

  const alignedTelemetry: TelemetryPoint[] = useMemo(() => {
    const startIso = activeRecording?.video_started_at;
    if (!startIso) return telemetry;
    const startMs = new Date(startIso).getTime();
    if (!Number.isFinite(startMs)) return telemetry;

    const hasServerTs = telemetry.some((p) => p.server_ts && p.server_ts > 0);
    if (!hasServerTs) return telemetry;

    const mapped = telemetry
      .filter((p) => p.server_ts && p.server_ts >= startMs - 2000)
      .map((p) => ({
        ...p,
        pts_ms: Math.max(0, (p.server_ts as number) - startMs),
      }));
    return mapped.length > 0 ? mapped : telemetry;
  }, [telemetry, activeRecording?.video_started_at]);

  const gpsTrack = useMemo(
    () => extractGpsFromTelemetry(alignedTelemetry),
    [alignedTelemetry]
  );

  const handleTimeUpdate = useCallback(() => {
    if (!videoRef.current) return;
    setCurrentTimeMs(videoRef.current.currentTime * 1000);
  }, []);

  const handleLoadedMetadata = useCallback(() => {
    if (!videoRef.current) return;
    setVideoDurationMs(videoRef.current.duration * 1000);
  }, []);

  const seekToMs = useCallback((ptsMs: number) => {
    const targetSeconds = Math.max(0, ptsMs / 1000);
    if (videoRef.current) {
      videoRef.current.currentTime = targetSeconds;
    }
    setCurrentTimeMs(ptsMs);
  }, []);

  const handleOpenAddMarker = useCallback(() => {
    const raw = Math.round(currentTimeMs);
    const clamped =
      effectiveDurationMs > 0 ? Math.min(raw, effectiveDurationMs) : raw;
    setMarkerPtsMs(Math.max(0, clamped));
    setMarkerLabel("");
    setMarkerCategory("technique");
    setMarkerNote("");
    openModal();
  }, [currentTimeMs, effectiveDurationMs, openModal]);

  const closestTelemetry = useMemo(() => {
    return findClosestTelemetry(alignedTelemetry, currentTimeMs);
  }, [alignedTelemetry, currentTimeMs]);

  const hasPower = useMemo(
    () => alignedTelemetry.some((p) => p.power_w != null && p.power_w > 0),
    [alignedTelemetry]
  );

  const participantOptions = useMemo(() => {
    const list = (session?.participants ?? []).filter(
      (p) => !p.role || p.role === "athlete"
    );
    return [
      { value: "", label: "Tous les athlètes" },
      ...list.map((p) => ({
        value: p.identity || p.uid || "",
        label:
          p.display_name || p.email || p.identity || p.uid || "Athlète",
      })),
    ];
  }, [session]);

  if (sessionLoading) {
    return (
      <Center py="xl">
        <Loader size="lg" />
      </Center>
    );
  }

  if (sessionError || !session) {
    return (
      <Stack gap="md">
        <Button
          component={Link}
          to="/app/sessions"
          variant="subtle"
          leftSection={<ArrowLeft size={16} />}
          style={{ alignSelf: "flex-start" }}
        >
          Retour aux sessions
        </Button>
        <Alert color="red" title="Erreur">
          {sessionError instanceof ApiError
            ? sessionError.detail
            : "Impossible de charger la session."}
        </Alert>
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <Group justify="space-between" align="center">
          <Button
            component={Link}
            to="/app/sessions"
            variant="subtle"
            leftSection={<ArrowLeft size={16} />}
          >
            Retour aux sessions
          </Button>

          {participantOptions.length > 2 && (
            <Select
              size="xs"
              style={{ width: 220 }}
              placeholder="Filtrer par participant"
              value={selectedParticipant ?? ""}
              onChange={(val) => setSelectedParticipant(val || null)}
              data={participantOptions}
            />
          )}
        </Group>

        <Group justify="space-between" align="flex-start" wrap="wrap">
          <div>
            <Title order={2}>{session.title || session.room_name}</Title>
            <Group gap="xs" mt={4}>
              <Text size="xs" ff="monospace" c="dimmed">
                {session.room_name}
              </Text>
              <CopyButton value={session.room_name}>
                {({ copied, copy }) => (
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    color={copied ? "teal" : "gray"}
                    onClick={copy}
                    title="Copier l'identifiant"
                  >
                    {copied ? <Check size={12} /> : <Copy size={12} />}
                  </ActionIcon>
                )}
              </CopyButton>
              {session.created_at && (
                <Text size="xs" c="dimmed">
                  ·{" "}
                  {new Date(session.created_at).toLocaleDateString("fr-FR", {
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </Text>
              )}
            </Group>
          </div>

          <Group gap="xs">
            {!session.finished && session.joinable !== false && (
              <Button
                size="xs"
                color="cyan"
                leftSection={<Video size={14} />}
                component="a"
                href={`/app/live/${encodeURIComponent(session.room_name)}`}
                target="_blank"
                rel="noreferrer"
              >
                Rejoindre en direct (OnAir)
              </Button>
            )}
            {!session.finished && session.joinable !== false && canManageMarkers && (
              <Button
                size="xs"
                color="red"
                variant="light"
                loading={finishSessionMutation.isPending}
                onClick={() => finishSessionMutation.mutate()}
              >
                Terminer la session
              </Button>
            )}
            {canManageMarkers && telemetry.length > 0 && (
              <Button
                size="xs"
                color="teal"
                variant="light"
                leftSection={<RefreshCw size={14} />}
                loading={convertLiveMutation.isPending}
                onClick={() => convertLiveMutation.mutate()}
              >
                Générer l&apos;activité &amp; PMC
              </Button>
            )}
            {generatedActivityId && (
              <Button
                size="xs"
                variant="outline"
                color="teal"
                component={Link}
                to={`/app/activities/${generatedActivityId}`}
              >
                Voir l&apos;activité
              </Button>
            )}
            {session.finished ? (
              <Badge color="gray">Terminée</Badge>
            ) : session.joinable === false ? (
              <Badge color="orange">Expirée</Badge>
            ) : (
              <Badge color="green">Active</Badge>
            )}
            <Badge variant="outline" color="blue">
              {session.participants?.length ?? 0} participant(s)
            </Badge>
          </Group>
        </Group>
      </Stack>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <Stack gap="md">
            <SyncedVideoPlayer
              videoRef={videoRef}
              recordings={recordings}
              selectedRecordingIndex={selectedRecordingIndex}
              onSelectRecording={handleSelectRecording}
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={handleLoadedMetadata}
              closestTelemetry={closestTelemetry}
              hasPower={hasPower}
              currentTimeMs={currentTimeMs}
              durationMs={effectiveDurationMs}
              markers={markers}
              onSeek={seekToMs}
            />

            <Card withBorder radius="md" p="md">
              <Group justify="space-between" mb="xs">
                <Group gap="xs">
                  <Activity size={18} />
                  <Text fw={600} size="sm">
                    Télémétrie synchronisée ({telemetry.length} points)
                  </Text>
                </Group>
              </Group>

              {alignedTelemetry.length > 0 ? (
                <StreamChart
                  telemetry={alignedTelemetry}
                  currentTimeMs={currentTimeMs}
                  durationMs={effectiveDurationMs}
                  onSeek={seekToMs}
                />
              ) : (
                <Text
                  size="sm"
                  c="dimmed"
                  py="lg"
                  style={{ textAlign: "center" }}
                >
                  Aucune donnée de télémétrie enregistrée pour cette session.
                </Text>
              )}
            </Card>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 4 }}>
          <Stack gap="md">
            <SyncedGpsMap
              track={gpsTrack}
              currentTimeMs={currentTimeMs}
              onSeek={seekToMs}
            />

            <MarkersPanel
              markers={markers}
              canManageMarkers={canManageMarkers}
              onAddClick={handleOpenAddMarker}
              onSeek={seekToMs}
              onDelete={(id) => deleteMarkerMutation.mutate(id)}
              deleting={deleteMarkerMutation.isPending}
            />

            <AlertsPanel alerts={alerts} onSeek={seekToMs} />
          </Stack>
        </Grid.Col>
      </Grid>

      <Modal
        opened={modalOpened}
        onClose={closeModal}
        title="Ajouter un marqueur coach"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!markerLabel.trim()) return;
            const clampedPts =
              effectiveDurationMs > 0
                ? Math.min(Math.max(0, markerPtsMs), effectiveDurationMs)
                : Math.max(0, markerPtsMs);
            createMarkerMutation.mutate({
              pts_ms: clampedPts,
              label: markerLabel.trim(),
              category: markerCategory,
              note: markerNote.trim() || null,
            });
          }}
        >
          <Stack gap="sm">
            {effectiveDurationMs > 0 && (
              <Stack gap={4}>
                <Group justify="space-between">
                  <Text size="sm" fw={500}>
                    Position sur la vidéo
                  </Text>
                  <Text size="xs" ff="monospace" c="dimmed">
                    {formatPtsWithTenths(markerPtsMs)} /{" "}
                    {formatPtsWithTenths(effectiveDurationMs)}
                  </Text>
                </Group>
                <Slider
                  min={0}
                  max={effectiveDurationMs}
                  step={100}
                  value={Math.min(markerPtsMs, effectiveDurationMs)}
                  onChange={(val) => setMarkerPtsMs(val)}
                  label={(val) => formatPtsWithTenths(val)}
                />
              </Stack>
            )}
            <NumberInput
              label="Position vidéo (ms)"
              description={`Équivaut à ${formatPtsWithTenths(markerPtsMs)}`}
              value={markerPtsMs}
              onChange={(val) => {
                const n = Math.max(0, Number(val ?? 0));
                setMarkerPtsMs(
                  effectiveDurationMs > 0
                    ? Math.min(n, effectiveDurationMs)
                    : n
                );
              }}
              min={0}
              max={effectiveDurationMs > 0 ? effectiveDurationMs : undefined}
              required
            />
            <TextInput
              label="Libellé"
              placeholder="Ex : Sprint virage, Décrochage, Relance..."
              value={markerLabel}
              onChange={(e) => setMarkerLabel(e.currentTarget.value)}
              required
            />
            <Select
              label="Catégorie"
              value={markerCategory}
              onChange={(val) => setMarkerCategory(val ?? "technique")}
              data={[
                { value: "technique", label: "Technique" },
                { value: "sprint", label: "Sprint" },
                { value: "incident", label: "Incident" },
                { value: "general", label: "Général" },
              ]}
              required
            />
            <Textarea
              label="Note d'analyse (optionnelle)"
              placeholder="Commentaires pour l'athlète..."
              value={markerNote}
              onChange={(e) => setMarkerNote(e.currentTarget.value)}
              rows={3}
            />
            <Group justify="flex-end" mt="md">
              <Button variant="default" onClick={closeModal}>
                Annuler
              </Button>
              <Button
                type="submit"
                loading={createMarkerMutation.isPending}
                disabled={!markerLabel.trim()}
              >
                Créer le marqueur
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
