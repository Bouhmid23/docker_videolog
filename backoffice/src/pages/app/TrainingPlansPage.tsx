import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  NumberInput,
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
import { Layers, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS, formatSportLabel } from "../../lib/constants";
import type { GroupItem, OrgMember, TrainingPlan, WorkoutTemplate } from "../../types";

export function TrainingPlansPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const queryClient = useQueryClient();

  const [modalOpened, { open: openModal, close: closeModal }] = useDisclosure(false);
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);
  const [applyPlanId, setApplyPlanId] = useState<string | null>(null);

  const [planName, setPlanName] = useState("");
  const [planSport, setPlanSport] = useState("cyclisme");
  const [planWeeks, setPlanWeeks] = useState<number>(2);
  const [selectedTplId, setSelectedTplId] = useState<string | null>(null);
  const [dayOffset, setDayOffset] = useState<number>(0);
  const [itemsDraft, setItemsDraft] = useState<
    Array<{ day_offset: number; template_id?: string | null; title: string; sport: string; steps: unknown[] }>
  >([]);

  const [applyUid, setApplyUid] = useState<string | null>(null);
  const [applyGroupId, setApplyGroupId] = useState<string | null>(null);
  const [applyStartDate, setApplyStartDate] = useState(() =>
    new Date().toISOString().slice(0, 10)
  );

  const { data: plansData } = useQuery<{ items: TrainingPlan[] }>({
    queryKey: ["training-plans", orgId],
    queryFn: () => apiFetch<{ items: TrainingPlan[] }>(`/v1/orgs/${orgId}/plans`),
    enabled: Boolean(orgId),
  });

  const { data: templatesData } = useQuery<{ items: WorkoutTemplate[] }>({
    queryKey: ["workout-templates", orgId],
    queryFn: () =>
      apiFetch<{ items: WorkoutTemplate[] }>(`/v1/orgs/${orgId}/workouts/templates`),
    enabled: Boolean(orgId),
  });

  const { data: membersData } = useQuery<{ members: OrgMember[] }>({
    queryKey: ["members", orgId],
    queryFn: () => apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members`),
    enabled: Boolean(orgId && isStaff),
  });

  const { data: groupsData } = useQuery<{ groups: GroupItem[] }>({
    queryKey: ["groups", orgId],
    queryFn: () => apiFetch<{ groups: GroupItem[] }>(`/v1/orgs/${orgId}/groups`),
    enabled: Boolean(orgId && isStaff),
  });

  const handleOpenCreate = () => {
    setEditingPlanId(null);
    setPlanName("");
    setPlanSport("cyclisme");
    setPlanWeeks(2);
    setSelectedTplId(null);
    setDayOffset(0);
    setItemsDraft([]);
    openModal();
  };

  const handleOpenEdit = (plan: TrainingPlan) => {
    setEditingPlanId(plan.id);
    setPlanName(plan.name);
    setPlanSport(plan.sport);
    setPlanWeeks(plan.weeks);
    setSelectedTplId(null);
    setDayOffset(0);
    setItemsDraft(
      plan.items.map((it) => ({
        day_offset: it.day_offset,
        template_id: it.template_id ?? null,
        title: it.title,
        sport: it.sport,
        steps: it.steps,
      }))
    );
    openModal();
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch(
        editingPlanId
          ? `/v1/orgs/${orgId}/plans/${editingPlanId}`
          : `/v1/orgs/${orgId}/plans`,
        {
          method: editingPlanId ? "PATCH" : "POST",
          body: JSON.stringify({
            name: planName.trim(),
            sport: planSport,
            weeks: planWeeks,
            items: itemsDraft,
          }),
        }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["training-plans", orgId] });
      closeModal();
      setEditingPlanId(null);
      setPlanName("");
      setItemsDraft([]);
      notifications.show({
        color: "teal",
        message: editingPlanId ? "Plan mis à jour." : "Plan créé.",
      });
    },
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ created_count: number }>(`/v1/orgs/${orgId}/plans/${applyPlanId}/apply`, {
        method: "POST",
        body: JSON.stringify({
          uid: applyUid || null,
          group_id: applyGroupId || null,
          start_date: applyStartDate,
        }),
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      queryClient.invalidateQueries({ queryKey: ["pmc", orgId] });
      setApplyPlanId(null);
      notifications.show({
        color: "teal",
        message: `${res.created_count} séance(s) planifiée(s) dans le calendrier.`,
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur d'application du plan.",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/orgs/${orgId}/plans/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["training-plans", orgId] });
    },
  });

  const plans = plansData?.items ?? [];
  const templates = templatesData?.items ?? [];

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <Group gap="xs">
          <Layers size={24} color="#18abf8" />
          <Title order={2}>Plans d&apos;entraînement</Title>
        </Group>
        {isStaff && (
          <Button leftSection={<Plus size={16} />} onClick={handleOpenCreate}>
            Nouveau plan
          </Button>
        )}
      </Group>

      {plans.length === 0 ? (
        <Card withBorder p="lg">
          <Text c="dimmed" style={{ textAlign: "center" }}>
            Aucun plan multi-semaines enregistré.
          </Text>
        </Card>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {plans.map((p) => (
            <Card key={p.id} withBorder radius="md" p="md">
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text fw={700}>{p.name}</Text>
                  {isStaff && (
                    <Group gap={4}>
                      <Tooltip label="Modifier le plan">
                        <ActionIcon
                          size="sm"
                          color="cyan"
                          variant="subtle"
                          onClick={() => handleOpenEdit(p)}
                        >
                          <Pencil size={14} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Supprimer le plan">
                        <ActionIcon
                          size="sm"
                          color="red"
                          variant="subtle"
                          onClick={() => deleteMutation.mutate(p.id)}
                        >
                          <Trash2 size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  )}
                </Group>
                <Group gap="xs">
                  <Badge size="xs" color="cyan">{formatSportLabel(p.sport)}</Badge>
                  <Badge size="xs" variant="outline">{p.weeks} semaine(s)</Badge>
                  <Badge size="xs" variant="outline" color="gray">
                    {p.items.length} séance(s)
                  </Badge>
                </Group>
                {isStaff && (
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<Play size={12} />}
                    onClick={() => setApplyPlanId(p.id)}
                  >
                    Appliquer au calendrier
                  </Button>
                )}
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <Modal
        opened={modalOpened}
        onClose={closeModal}
        title={editingPlanId ? "Modifier le plan multi-semaines" : "Créer un plan multi-semaines"}
        size="lg"
      >
        <Stack gap="sm">
          <Group grow>
            <TextInput
              label="Nom du plan"
              value={planName}
              onChange={(e) => setPlanName(e.currentTarget.value)}
              required
            />
            <Select
              label="Sport"
              searchable
              value={planSport}
              onChange={(v) => v && setPlanSport(v)}
              data={SPORTS}
            />
            <NumberInput
              label="Semaines"
              min={1}
              max={52}
              value={planWeeks}
              onChange={(v) => setPlanWeeks(Number(v || 1))}
            />
          </Group>

          <Group align="flex-end">
            <Select
              label="Ajouter une séance depuis la bibliothèque"
              style={{ flex: 1 }}
              value={selectedTplId}
              onChange={setSelectedTplId}
              data={templates.map((t) => ({ value: t.id, label: t.name }))}
            />
            <NumberInput
              label="Jour (J+)"
              min={0}
              max={364}
              style={{ width: 90 }}
              value={dayOffset}
              onChange={(v) => setDayOffset(Number(v || 0))}
            />
            <Button
              variant="light"
              disabled={!selectedTplId}
              onClick={() => {
                const tpl = templates.find((t) => t.id === selectedTplId);
                if (!tpl) return;
                setItemsDraft((prev) => [
                  ...prev,
                  {
                    day_offset: dayOffset,
                    template_id: tpl.id,
                    title: tpl.name,
                    sport: tpl.sport,
                    steps: tpl.steps,
                  },
                ]);
              }}
            >
              Ajouter
            </Button>
          </Group>

          <Stack gap={4}>
            {itemsDraft.map((it, idx) => (
              <Group key={idx} justify="space-between">
                <Text size="xs">
                  J+{it.day_offset} — {it.title}
                </Text>
                <ActionIcon
                  size="xs"
                  color="red"
                  variant="subtle"
                  onClick={() => setItemsDraft((prev) => prev.filter((_, i) => i !== idx))}
                >
                  <Trash2 size={12} />
                </ActionIcon>
              </Group>
            ))}
          </Stack>

          <Group justify="flex-end" mt="md">
            <Button variant="default" onClick={closeModal}>
              Annuler
            </Button>
            <Button
              loading={saveMutation.isPending}
              disabled={!planName.trim()}
              onClick={() => saveMutation.mutate()}
            >
              {editingPlanId ? "Enregistrer" : "Créer le plan"}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={Boolean(applyPlanId)}
        onClose={() => setApplyPlanId(null)}
        title="Appliquer le plan"
      >
        <Stack gap="sm">
          <Select
            label="Athlète cible"
            clearable
            value={applyUid}
            onChange={(v) => {
              setApplyUid(v);
              if (v) setApplyGroupId(null);
            }}
            data={(membersData?.members ?? []).map((m) => ({
              value: m.uid,
              label: `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim() || m.email || m.uid,
            }))}
          />
          <Select
            label="Ou groupe cible"
            clearable
            value={applyGroupId}
            onChange={(v) => {
              setApplyGroupId(v);
              if (v) setApplyUid(null);
            }}
            data={(groupsData?.groups ?? []).map((g) => ({
              value: g.id,
              label: g.name,
            }))}
          />
          <TextInput
            label="Date de début"
            type="date"
            value={applyStartDate}
            onChange={(e) => setApplyStartDate(e.currentTarget.value)}
          />
          <Group justify="flex-end" mt="md">
            <Button variant="default" onClick={() => setApplyPlanId(null)}>
              Annuler
            </Button>
            <Button
              loading={applyMutation.isPending}
              disabled={!applyUid && !applyGroupId}
              onClick={() => applyMutation.mutate()}
            >
              Appliquer
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
