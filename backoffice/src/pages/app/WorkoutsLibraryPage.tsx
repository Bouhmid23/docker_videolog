import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Dumbbell, Pencil, Plus, Trash2 } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { WorkoutBuilderModal } from "../../components/planning/WorkoutBuilderModal";
import { WorkoutIntensityBar } from "../../components/planning/WorkoutIntensityBar";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS, formatSportLabel } from "../../lib/constants";
import type { WorkoutStep, WorkoutTemplate } from "../../types";

export function WorkoutsLibraryPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const queryClient = useQueryClient();

  const [sportFilter, setSportFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [modalOpened, { open: openModal, close: closeModal }] =
    useDisclosure(false);
  const [editingTemplate, setEditingTemplate] = useState<WorkoutTemplate | null>(
    null
  );

  const { data, isLoading } = useQuery<{ items: WorkoutTemplate[] }>({
    queryKey: ["workout-templates", orgId, sportFilter],
    queryFn: () => {
      const q = sportFilter ? `?sport=${encodeURIComponent(sportFilter)}` : "";
      return apiFetch<{ items: WorkoutTemplate[] }>(
        `/v1/orgs/${orgId}/workouts/templates${q}`
      );
    },
    enabled: Boolean(orgId),
  });

  const createMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      sport: string;
      description: string;
      steps: WorkoutStep[];
    }) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/templates`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workout-templates", orgId] });
      closeModal();
      notifications.show({ color: "teal", message: "Modèle de séance créé." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur création.",
      });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: {
        name: string;
        sport: string;
        description: string;
        steps: WorkoutStep[];
      };
    }) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/templates/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workout-templates", orgId] });
      setEditingTemplate(null);
      closeModal();
      notifications.show({ color: "teal", message: "Modèle de séance mis à jour." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur modification.",
      });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/templates/${id}/duplicate`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workout-templates", orgId] });
      notifications.show({ color: "teal", message: "Modèle dupliqué." });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/templates/${id}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workout-templates", orgId] });
      notifications.show({ color: "teal", message: "Modèle supprimé." });
    },
  });

  const items = (data?.items ?? []).filter((t) =>
    t.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center" wrap="wrap">
        <Group gap="xs">
          <Dumbbell size={24} color="#18abf8" />
          <Title order={2}>Bibliothèque de séances</Title>
        </Group>
        {isStaff && (
          <Button
            leftSection={<Plus size={16} />}
            onClick={() => {
              setEditingTemplate(null);
              openModal();
            }}
          >
            Nouveau modèle
          </Button>
        )}
      </Group>

      <Group gap="sm" wrap="wrap">
        <TextInput
          placeholder="Rechercher un modèle..."
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          style={{ width: 240 }}
        />
        <Select
          placeholder="Tous les sports"
          clearable
          searchable
          value={sportFilter}
          onChange={setSportFilter}
          data={SPORTS}
          style={{ width: 230 }}
        />
      </Group>

      {isLoading ? (
        <Text c="dimmed">Chargement...</Text>
      ) : items.length === 0 ? (
        <Card withBorder p="lg">
          <Text c="dimmed" style={{ textAlign: "center" }}>
            Aucun modèle de séance dans la bibliothèque.
          </Text>
        </Card>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {items.map((tpl) => (
            <Card key={tpl.id} withBorder radius="md" p="md">
              <Stack gap="xs">
                <Group justify="space-between" align="flex-start" wrap="nowrap">
                  <div>
                    <Text fw={700}>{tpl.name}</Text>
                    <Badge size="xs" color="cyan" variant="light">
                      {formatSportLabel(tpl.sport)}
                    </Badge>
                  </div>
                  {isStaff && (
                    <Group gap={4}>
                      <Tooltip label="Modifier">
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => {
                            setEditingTemplate(tpl);
                            openModal();
                          }}
                        >
                          <Pencil size={14} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Dupliquer">
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => duplicateMutation.mutate(tpl.id)}
                        >
                          <Copy size={14} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Supprimer">
                        <ActionIcon
                          size="sm"
                          color="red"
                          variant="subtle"
                          onClick={() => deleteMutation.mutate(tpl.id)}
                        >
                          <Trash2 size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  )}
                </Group>

                <WorkoutIntensityBar blocks={tpl.blocks} height={38} />

                <Group gap="xs">
                  <Badge size="xs" variant="outline" color="gray">
                    {Math.round(tpl.estimated_duration_s / 60)} min
                  </Badge>
                  <Badge size="xs" variant="outline" color="grape">
                    Charge : ~{tpl.estimated_tss} pts
                  </Badge>
                </Group>

                {tpl.description && (
                  <Text size="xs" c="dimmed" lineClamp={2}>
                    {tpl.description}
                  </Text>
                )}
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <WorkoutBuilderModal
        opened={modalOpened}
        onClose={() => {
          setEditingTemplate(null);
          closeModal();
        }}
        titleLabel={
          editingTemplate
            ? `Modifier : ${editingTemplate.name}`
            : "Créer un modèle de séance"
        }
        initialName={editingTemplate?.name ?? ""}
        initialSport={editingTemplate?.sport ?? "cyclisme"}
        initialDescription={editingTemplate?.description ?? ""}
        initialSteps={editingTemplate?.steps}
        saving={createMutation.isPending || updateMutation.isPending}
        onSave={(payload) => {
          if (editingTemplate) {
            updateMutation.mutate({ id: editingTemplate.id, payload });
          } else {
            createMutation.mutate(payload);
          }
        }}
      />
    </Stack>
  );
}
