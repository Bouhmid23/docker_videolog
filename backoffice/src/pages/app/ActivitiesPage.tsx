import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  FileInput,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Upload } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS, formatSportLabel } from "../../lib/constants";
import type { ActivitySummary, OrgMember } from "../../types";

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h${m.toString().padStart(2, "0")}`;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function ActivitiesPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [sportFilter, setSportFilter] = useState<string | null>(null);
  const [athleteFilter, setAthleteFilter] = useState<string | null>(null);

  const [uploadOpened, { open: openUpload, close: closeUpload }] =
    useDisclosure(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadAthleteUid, setUploadAthleteUid] = useState<string | null>(null);

  const { data: membersData } = useQuery<{ members: OrgMember[] }>({
    queryKey: ["members", orgId, "athlete"],
    queryFn: () =>
      apiFetch<{ members: OrgMember[] }>(
        `/v1/orgs/${orgId}/members?role=athlete`
      ),
    enabled: Boolean(orgId && isStaff),
  });

  const { data, isLoading } = useQuery<{
    items: ActivitySummary[];
    total: number;
  }>({
    queryKey: ["activities", orgId, sportFilter, athleteFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (sportFilter) params.set("sport", sportFilter);
      if (athleteFilter) params.set("uid", athleteFilter);
      const q = params.toString();
      return apiFetch<{ items: ActivitySummary[]; total: number }>(
        `/v1/orgs/${orgId}/activities${q ? `?${q}` : ""}`
      );
    },
    enabled: Boolean(orgId),
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Sélectionnez un fichier .fit, .gpx ou .tcx.");
      const form = new FormData();
      form.append("file", file);
      if (uploadTitle.trim()) form.append("title", uploadTitle.trim());
      if (uploadAthleteUid) form.append("athlete_uid", uploadAthleteUid);

      return apiFetch<{ activity: ActivitySummary }>(
        `/v1/orgs/${orgId}/activities/upload`,
        {
          method: "POST",
          body: form,
        }
      );
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["activities", orgId] });
      closeUpload();
      setFile(null);
      setUploadTitle("");
      notifications.show({
        color: "teal",
        message: "Activité importée et analysée.",
      });
      navigate(`/app/activities/${res.activity.id}`);
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
            ? err.message
            : "Erreur lors de l'import.",
      });
    },
  });

  const memberOptions = (membersData?.members ?? []).map((m) => ({
    value: m.uid,
    label:
      `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim() ||
      m.email ||
      m.uid,
  }));

  const items = data?.items ?? [];

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center" wrap="wrap">
        <Group gap="xs">
          <Activity size={24} color="#18abf8" />
          <Title order={2}>Activités & Analyse</Title>
        </Group>
        <Button leftSection={<Upload size={16} />} onClick={openUpload}>
          Importer FIT / GPX / TCX
        </Button>
      </Group>

      <Group gap="sm" wrap="wrap">
        <Select
          placeholder="Tous les sports"
          clearable
          searchable
          value={sportFilter}
          onChange={setSportFilter}
          data={SPORTS}
          style={{ width: 230 }}
        />
        {isStaff && memberOptions.length > 0 && (
          <Select
            placeholder="Tous les athlètes"
            clearable
            searchable
            value={athleteFilter}
            onChange={setAthleteFilter}
            data={memberOptions}
            style={{ width: 240 }}
          />
        )}
      </Group>

      <Card withBorder radius="md" p={0}>
        {isLoading ? (
          <Text p="md" c="dimmed">
            Chargement des activités...
          </Text>
        ) : items.length === 0 ? (
          <Text p="lg" c="dimmed" style={{ textAlign: "center" }}>
            Aucune activité enregistrée. Importez un fichier .fit, .gpx ou .tcx
            pour démarrer l&apos;analyse.
          </Text>
        ) : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Date</Table.Th>
                <Table.Th>Séance</Table.Th>
                {isStaff && <Table.Th>Athlète</Table.Th>}
                <Table.Th>Sport</Table.Th>
                <Table.Th>Durée</Table.Th>
                <Table.Th>Distance</Table.Th>
                <Table.Th>NP / FC moy</Table.Th>
                <Table.Th>TSS</Table.Th>
                <Table.Th>Source</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((a) => (
                <Table.Tr
                  key={a.id}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/app/activities/${a.id}`)}
                >
                  <Table.Td>
                    <Text size="xs">
                      {new Date(a.start_time).toLocaleDateString("fr-FR")}
                    </Text>
                  </Table.Td>
                  <Table.Td fw={600}>{a.title}</Table.Td>
                  {isStaff && (
                    <Table.Td>
                      <Text size="xs">{a.athlete_name || a.uid}</Text>
                    </Table.Td>
                  )}
                  <Table.Td>
                    <Badge size="xs" variant="light" color="cyan">
                      {formatSportLabel(a.sport)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{formatDuration(a.duration_s)}</Table.Td>
                  <Table.Td>
                    {a.distance_m > 0
                      ? `${(a.distance_m / 1000).toFixed(1)} km`
                      : "—"}
                  </Table.Td>
                  <Table.Td>
                    {a.metrics?.normalized_power
                      ? `${Math.round(a.metrics.normalized_power)} W`
                      : a.metrics?.avg_hr
                      ? `${Math.round(a.metrics.avg_hr)} bpm`
                      : "—"}
                  </Table.Td>
                  <Table.Td>
                    {a.metrics?.tss != null ? (
                      <Badge size="sm" color="grape">
                        {Math.round(a.metrics.tss)}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge size="xs" variant="outline" color="gray">
                      {a.source}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Modal
        opened={uploadOpened}
        onClose={closeUpload}
        title="Importer une activité (.fit, .gpx, .tcx)"
      >
        <Stack gap="sm">
          <FileInput
            label="Fichier d'activité"
            placeholder="Choisir un fichier .fit, .gpx ou .tcx"
            accept=".fit,.gpx,.tcx"
            value={file}
            onChange={setFile}
            required
          />
          <TextInput
            label="Titre (optionnel)"
            placeholder="Ex : Sortie seuil 2x20'"
            value={uploadTitle}
            onChange={(e) => setUploadTitle(e.currentTarget.value)}
          />
          {isStaff && memberOptions.length > 0 && (
            <Select
              label="Athlète (optionnel, vous-même par défaut)"
              clearable
              searchable
              value={uploadAthleteUid}
              onChange={setUploadAthleteUid}
              data={memberOptions}
            />
          )}
          <Group justify="flex-end" mt="md">
            <Button variant="default" onClick={closeUpload}>
              Annuler
            </Button>
            <Button
              onClick={() => uploadMutation.mutate()}
              loading={uploadMutation.isPending}
              disabled={!file}
            >
              Importer
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
