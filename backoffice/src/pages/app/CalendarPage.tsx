import { useMemo, useState } from "react";
import {
  DndContext,
  type DragEndEvent,
  useDraggable,
  useDroppable,
} from "@dnd-kit/core";
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Menu,
  Modal,
  Paper,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Download,
  Flag,
  MessageSquare,
  Plus,
  Trash2,
  Video,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { WorkoutIntensityBar } from "../../components/planning/WorkoutIntensityBar";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS, formatSportLabel } from "../../lib/constants";
import { auth } from "../../lib/firebase";
import type {
  CalendarResponse,
  OrgMember,
  PlannedWorkout,
  WorkoutComment,
  WorkoutTemplate,
} from "../../types";

function mondayOf(d: Date): Date {
  const copy = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = copy.getUTCDay();
  const diff = (day === 0 ? -6 : 1) - day;
  copy.setUTCDate(copy.getUTCDate() + diff);
  return copy;
}

function addDays(d: Date, n: number): Date {
  const copy = new Date(d);
  copy.setUTCDate(copy.getUTCDate() + n);
  return copy;
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function isWorkoutPast(workout: PlannedWorkout): boolean {
  const todayStr = isoDate(new Date());
  if (workout.date < todayStr) return true;
  if (workout.date > todayStr) return false;
  if (!workout.start_time) return false;
  const startMs = new Date(`${workout.date}T${workout.start_time}:00`).getTime();
  if (!Number.isFinite(startMs)) return false;
  const durationMs = Math.max(1800, workout.estimated_duration_s || 3600) * 1000;
  return Date.now() > startMs + durationMs;
}

function DraggablePlannedCard({
  workout,
  onExport,
  onDelete,
  onStartLive,
  onOpenDetail,
}: {
  workout: PlannedWorkout;
  onExport: (id: string, fmt: "fit" | "zwo" | "erg") => void;
  onDelete: (id: string) => void;
  onStartLive?: (workout: PlannedWorkout) => void;
  onOpenDetail: (workout: PlannedWorkout) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: workout.id,
  });
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`, opacity: 0.85, zIndex: 20 }
    : undefined;

  const badgeColor =
    workout.compliance?.color === "green"
      ? "teal"
      : workout.compliance?.color === "orange"
      ? "orange"
      : workout.compliance?.color === "red"
      ? "red"
      : "cyan";

  return (
    <Paper
      ref={setNodeRef}
      style={{
        ...style,
        backgroundColor: "#151a21",
        cursor: isDragging ? "grabbing" : "grab",
        borderColor: workout.compliance?.color === "green" ? "#3ddc97" : undefined,
      }}
      withBorder
      p="xs"
      radius="sm"
      {...listeners}
      {...attributes}
    >
      <Stack gap={4}>
        <Group justify="space-between" wrap="nowrap">
          <Group gap={4} wrap="nowrap">
            {workout.start_time && (
              <Badge size="xs" color="gray" variant="light">
                {workout.start_time}
              </Badge>
            )}
            <Badge size="xs" color={badgeColor}>
              {formatSportLabel(workout.sport)}
            </Badge>
          </Group>
          <Group gap={2} onPointerDown={(e) => e.stopPropagation()}>
            <Tooltip label="Détails & discussion">
              <ActionIcon size="xs" variant="subtle" onClick={() => onOpenDetail(workout)}>
                <MessageSquare size={12} />
              </ActionIcon>
            </Tooltip>
            {onStartLive && workout.status !== "done" && !isWorkoutPast(workout) && (
              <Tooltip label="Démarrer en live">
                <ActionIcon size="xs" color="cyan" variant="subtle" onClick={() => onStartLive(workout)}>
                  <Video size={12} />
                </ActionIcon>
              </Tooltip>
            )}
            <Menu withinPortal position="bottom-end">
              <Menu.Target>
                <Tooltip label="Exporter (.fit / .zwo / .erg)">
                  <ActionIcon size="xs" variant="subtle">
                    <Download size={12} />
                  </ActionIcon>
                </Tooltip>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item onClick={() => onExport(workout.id, "fit")}>
                  Garmin (.fit)
                </Menu.Item>
                <Menu.Item onClick={() => onExport(workout.id, "zwo")}>
                  Zwift (.zwo)
                </Menu.Item>
                <Menu.Item onClick={() => onExport(workout.id, "erg")}>
                  Home-Trainer (.erg)
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Tooltip label="Supprimer">
              <ActionIcon size="xs" color="red" variant="subtle" onClick={() => onDelete(workout.id)}>
                <Trash2 size={12} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <Text size="xs" fw={600} lineClamp={1}>
          {workout.title}
        </Text>
        <WorkoutIntensityBar blocks={workout.blocks} height={18} />
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {Math.round(workout.estimated_duration_s / 60)}m · {workout.estimated_tss} pts d&apos;effort
          </Text>
          {workout.compliance && (
            <Badge size="xs" color={badgeColor} variant="light">
              {workout.compliance.score}%
            </Badge>
          )}
        </Group>
      </Stack>
    </Paper>
  );
}

function DayCell({
  dateStr,
  label,
  children,
  onQuickAdd,
}: {
  dateStr: string;
  label: string;
  children: React.ReactNode;
  onQuickAdd: (dateStr: string) => void;
}) {
  const { isOver, setNodeRef } = useDroppable({ id: dateStr });
  return (
    <Card
      ref={setNodeRef}
      withBorder
      p="xs"
      radius="md"
      style={{
        minHeight: 190,
        backgroundColor: isOver ? "#1b2430" : "#0f1318",
        borderColor: isOver ? "#18abf8" : undefined,
      }}
    >
      <Group justify="space-between" mb="xs">
        <Text size="xs" fw={700} c="dimmed">
          {label}
        </Text>
        <ActionIcon size="xs" variant="subtle" onClick={() => onQuickAdd(dateStr)}>
          <Plus size={12} />
        </ActionIcon>
      </Group>
      <Stack gap="xs">{children}</Stack>
    </Card>
  );
}

export function CalendarPage() {
  const { me, activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [weekStart, setWeekStart] = useState(() => mondayOf(new Date()));
  const [addStartTime, setAddStartTime] = useState("18:00");
  const [detailWorkout, setDetailWorkout] = useState<PlannedWorkout | null>(null);
  const [commentBody, setCommentBody] = useState("");
  const [selectedUid, setSelectedUid] = useState<string>(me?.uid ?? "");

  const [addOpened, { open: openAdd, close: closeAdd }] = useDisclosure(false);
  const [addDate, setAddDate] = useState(() => isoDate(new Date()));
  const [addTemplateId, setAddTemplateId] = useState<string | null>(null);
  const [addTitle, setAddTitle] = useState("");

  const [goalOpened, { open: openGoal, close: closeGoal }] = useDisclosure(false);
  const [goalTitle, setGoalTitle] = useState("");
  const [goalDate, setGoalDate] = useState(() => isoDate(new Date()));
  const [goalSport, setGoalSport] = useState("cyclisme");
  const [goalPriority, setGoalPriority] = useState<"A" | "B" | "C">("A");
  const [goalNotes, setGoalNotes] = useState("");

  const fromStr = isoDate(weekStart);
  const toStr = isoDate(addDays(weekStart, 6));

  const { data: membersData } = useQuery<{ members: OrgMember[] }>({
    queryKey: ["members", orgId, "athlete"],
    queryFn: () =>
      apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members?role=athlete`),
    enabled: Boolean(orgId && isStaff),
  });
  const athletes = membersData?.members ?? [];
  const effectiveUid = isStaff
    ? athletes.some((a) => a.uid === selectedUid)
      ? selectedUid
      : athletes[0]?.uid ?? ""
    : me?.uid ?? "";

  const { data: templatesData } = useQuery<{ items: WorkoutTemplate[] }>({
    queryKey: ["workout-templates", orgId],
    queryFn: () =>
      apiFetch<{ items: WorkoutTemplate[] }>(`/v1/orgs/${orgId}/workouts/templates`),
    enabled: Boolean(orgId),
  });

  const { data: detailCommentsData } = useQuery<{ items: WorkoutComment[] }>({
    queryKey: ["workout-comments", orgId, detailWorkout?.id],
    queryFn: () =>
      apiFetch<{ items: WorkoutComment[] }>(
        `/v1/orgs/${orgId}/workouts/planned/${detailWorkout!.id}/comments`
      ),
    enabled: Boolean(orgId && detailWorkout?.id),
  });

  const postCommentMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/workouts/planned/${detailWorkout!.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: commentBody.trim() }),
      }),
    onSuccess: () => {
      setCommentBody("");
      queryClient.invalidateQueries({
        queryKey: ["workout-comments", orgId, detailWorkout?.id],
      });
      notifications.show({ color: "teal", message: "Message envoyé." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur d'envoi.",
      });
    },
  });

  const { data: cal } = useQuery<CalendarResponse>({
    queryKey: ["calendar", orgId, fromStr, toStr, effectiveUid],
    queryFn: () => {
      const q = new URLSearchParams({ from: fromStr, to: toStr });
      if (effectiveUid) q.set("uid", effectiveUid);
      return apiFetch<CalendarResponse>(`/v1/orgs/${orgId}/calendar?${q.toString()}`);
    },
    enabled: Boolean(orgId),
  });

  const moveMutation = useMutation({
    mutationFn: ({ id, date }: { id: string; date: string }) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/planned/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ date }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
    },
  });

  const createPlannedMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/workouts/planned`, {
        method: "POST",
        body: JSON.stringify({
          uid: effectiveUid,
          date: addDate,
          start_time: addStartTime || null,
          template_id: addTemplateId || null,
          title: addTitle.trim() || undefined,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      closeAdd();
      setAddTitle("");
      setAddTemplateId(null);
      notifications.show({ color: "teal", message: "Séance planifiée." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur planification.",
      });
    },
  });

  const deletePlannedMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/orgs/${orgId}/workouts/planned/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
    },
  });

  const createGoalMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/goals`, {
        method: "POST",
        body: JSON.stringify({
          uid: effectiveUid,
          title: goalTitle.trim(),
          date: goalDate,
          sport: goalSport,
          priority: goalPriority,
          notes: goalNotes.trim() || null,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      closeGoal();
      setGoalTitle("");
      setGoalNotes("");
      notifications.show({ color: "teal", message: "Objectif ajouté." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur création objectif.",
      });
    },
  });

  const deleteGoalMutation = useMutation({
    mutationFn: (goalId: string) =>
      apiFetch(`/v1/orgs/${orgId}/goals/${goalId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar", orgId] });
      notifications.show({ color: "teal", message: "Objectif supprimé." });
    },
  });

  const startLiveMutation = useMutation({
    mutationFn: (w: PlannedWorkout) => {
      const scheduledAt = w.start_time
        ? new Date(`${w.date}T${w.start_time}:00`).toISOString()
        : undefined;
      return apiFetch<{ session: { room_name: string } }>(`/v1/orgs/${orgId}/sessions`, {
        method: "POST",
        body: JSON.stringify({
          title: w.title,
          participant_uids: w.uid ? [w.uid] : [],
          planned_workout_id: w.id,
          scheduled_at: scheduledAt,
          duration_s: w.estimated_duration_s || 3600,
        }),
      });
    },
    onSuccess: (res) => {
      const roomName = res.session.room_name;
      window.open(`/app/live/${encodeURIComponent(roomName)}`, "_blank", "noopener");
      notifications.show({
        color: "cyan",
        message: `Session live créée (${roomName}).`,
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible de créer la session live.",
      });
    },
  });

  const handleExport = async (id: string, format: "fit" | "zwo" | "erg") => {
    const token = auth.currentUser ? await auth.currentUser.getIdToken() : "";
    const res = await fetch(
      `/api/v1/orgs/${orgId}/workouts/planned/${id}/export?format=${format}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
    );
    if (!res.ok) {
      notifications.show({ color: "red", message: "Échec de l'export de la séance." });
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `workout_${id}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart]
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const workoutId = String(active.id);
    const targetDate = String(over.id);
    const existing = cal?.planned_workouts.find((w) => w.id === workoutId);
    if (existing && existing.date !== targetDate) {
      moveMutation.mutate({ id: workoutId, date: targetDate });
    }
  };

  const weekSummary = cal?.weekly_totals?.[0];

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center" wrap="wrap">
        <Group gap="xs">
          <CalendarIcon size={24} color="#18abf8" />
          <Title order={2}>Calendrier d&apos;entraînement</Title>
        </Group>

        <Group gap="xs">
          {isStaff && (membersData?.members ?? []).length > 0 && (
            <Select
              placeholder="Athlète"
              value={effectiveUid}
              onChange={(v) => v && setSelectedUid(v)}
              data={(membersData?.members ?? []).map((m) => ({
                value: m.uid,
                label:
                  `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim() ||
                  m.email ||
                  m.uid,
              }))}
              style={{ width: 220 }}
            />
          )}
          <Button
            variant="light"
            color="yellow"
            size="xs"
            leftSection={<Flag size={14} />}
            onClick={() => {
              setGoalDate(isoDate(weekStart));
              openGoal();
            }}
          >
            + Objectif
          </Button>
          <ActionIcon variant="default" onClick={() => setWeekStart(addDays(weekStart, -7))}>
            <ChevronLeft size={16} />
          </ActionIcon>
          <Button variant="default" size="xs" onClick={() => setWeekStart(mondayOf(new Date()))}>
            Aujourd&apos;hui
          </Button>
          <ActionIcon variant="default" onClick={() => setWeekStart(addDays(weekStart, 7))}>
            <ChevronRight size={16} />
          </ActionIcon>
        </Group>
      </Group>

      {weekSummary && (
        <Group gap="sm">
          <Badge color="cyan" variant="light">
            Prévu : {Math.round(weekSummary.planned_duration_s / 60)} min · {weekSummary.planned_tss} pts d&apos;effort
          </Badge>
          <Badge color="teal" variant="light">
            Réalisé : {Math.round(weekSummary.actual_duration_s / 60)} min · {weekSummary.actual_tss} pts d&apos;effort
          </Badge>
        </Group>
      )}

      <DndContext onDragEnd={handleDragEnd}>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 4, lg: 7 }} spacing="xs">
          {days.map((d) => {
            const dStr = isoDate(d);
            const label = d.toLocaleDateString("fr-FR", {
              weekday: "short",
              day: "numeric",
              month: "short",
            });
            const dayPlanned = (cal?.planned_workouts ?? [])
              .filter((w) => w.date === dStr)
              .sort((a, b) => (a.start_time ?? "99:99").localeCompare(b.start_time ?? "99:99"));
            const dayActs = (cal?.activities ?? []).filter((a) => a.date === dStr);
            const dayGoals = (cal?.goals ?? []).filter((g) => g.date === dStr);

            return (
              <DayCell
                key={dStr}
                dateStr={dStr}
                label={label}
                onQuickAdd={(date) => {
                  setAddDate(date);
                  openAdd();
                }}
              >
                {dayGoals.map((g) => (
                  <Group key={g.id} gap={4} justify="space-between" wrap="nowrap">
                    <Badge
                      color={g.priority === "A" ? "yellow" : g.priority === "B" ? "orange" : "gray"}
                      leftSection={<Flag size={10} />}
                      style={{ flex: 1, justifyContent: "flex-start" }}
                    >
                      [{g.priority}] {g.title}
                    </Badge>
                    <ActionIcon
                      size="xs"
                      color="red"
                      variant="subtle"
                      title="Supprimer l'objectif"
                      onClick={() => deleteGoalMutation.mutate(g.id)}
                    >
                      <Trash2 size={11} />
                    </ActionIcon>
                  </Group>
                ))}
                {dayPlanned.map((w) => (
                  <DraggablePlannedCard
                    key={w.id}
                    workout={w}
                    onExport={handleExport}
                    onDelete={(id) => deletePlannedMutation.mutate(id)}
                    onStartLive={isStaff ? (workout) => startLiveMutation.mutate(workout) : undefined}
                    onOpenDetail={setDetailWorkout}
                  />
                ))}
                {dayActs.map((a) => (
                  <Paper
                    key={a.id}
                    withBorder
                    p="xs"
                    radius="sm"
                    style={{
                      backgroundColor: "#12211d",
                      borderColor: "#3ddc97",
                      cursor: "pointer",
                    }}
                    onClick={() => navigate(`/app/activities/${a.id}`)}
                  >
                    <Text size="xs" fw={600} c="#3ddc97" lineClamp={1}>
                      ✓ {a.title}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {Math.round(a.duration_s / 60)}m
                      {a.tss != null ? ` · ${Math.round(a.tss)} pts d'effort` : ""}
                    </Text>
                  </Paper>
                ))}
              </DayCell>
            );
          })}
        </SimpleGrid>
      </DndContext>

      <Modal opened={addOpened} onClose={closeAdd} title={`Planifier le ${addDate}`}>
        <Stack gap="sm">
          <Select
            label="Modèle de la bibliothèque (optionnel)"
            clearable
            searchable
            value={addTemplateId}
            onChange={setAddTemplateId}
            data={(templatesData?.items ?? []).map((t) => ({
              value: t.id,
              label: `${t.name} (${Math.round(t.estimated_duration_s / 60)}m · ${t.estimated_tss} pts d'effort)`,
            }))}
          />
          <TextInput
            label="Titre (requis si pas de modèle)"
            placeholder="Ex : Endurance Z2 1h30"
            value={addTitle}
            onChange={(e) => setAddTitle(e.currentTarget.value)}
          />
          <TextInput
            label="Heure de début"
            type="time"
            value={addStartTime}
            onChange={(e) => setAddStartTime(e.currentTarget.value)}
          />
          <Group justify="flex-end" mt="md">
            <Button variant="default" onClick={closeAdd}>
              Annuler
            </Button>
            <Button
              loading={createPlannedMutation.isPending}
              disabled={!addTemplateId && !addTitle.trim()}
              onClick={() => createPlannedMutation.mutate()}
            >
              Planifier
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={Boolean(detailWorkout)}
        onClose={() => {
          setDetailWorkout(null);
          setCommentBody("");
        }}
        title={detailWorkout?.title ?? "Séance planifiée"}
        size="lg"
      >
        {detailWorkout && (
          <Stack gap="md">
            <Group justify="space-between" wrap="wrap">
              <Group gap="xs">
                <Badge color="cyan">{formatSportLabel(detailWorkout.sport)}</Badge>
                {detailWorkout.start_time && (
                  <Badge variant="light" color="gray">{detailWorkout.start_time}</Badge>
                )}
                <Text size="xs" c="dimmed">
                  {detailWorkout.date} · {Math.round(detailWorkout.estimated_duration_s / 60)} min · {detailWorkout.estimated_tss} pts d&apos;effort
                </Text>
              </Group>
              <Group gap="xs">
                {isStaff &&
                  detailWorkout.status !== "done" &&
                  !isWorkoutPast(detailWorkout) && (
                    <Button
                      size="xs"
                      color="cyan"
                      leftSection={<Video size={14} />}
                      loading={startLiveMutation.isPending}
                      onClick={() => startLiveMutation.mutate(detailWorkout)}
                    >
                      Démarrer en live
                    </Button>
                  )}
                <Menu withinPortal position="bottom-end">
                  <Menu.Target>
                    <Button
                      size="xs"
                      variant="light"
                      leftSection={<Download size={14} />}
                    >
                      Exporter
                    </Button>
                  </Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Item onClick={() => handleExport(detailWorkout.id, "fit")}>
                      Garmin (.fit)
                    </Menu.Item>
                    <Menu.Item onClick={() => handleExport(detailWorkout.id, "zwo")}>
                      Zwift (.zwo)
                    </Menu.Item>
                    <Menu.Item onClick={() => handleExport(detailWorkout.id, "erg")}>
                      Home-Trainer (.erg)
                    </Menu.Item>
                  </Menu.Dropdown>
                </Menu>
              </Group>
            </Group>

            {detailWorkout.description && (
              <Text size="sm" c="dimmed">{detailWorkout.description}</Text>
            )}

            <Divider label="Discussion coach / athlète" labelPosition="left" />

            <ScrollArea.Autosize mah={220}>
              <Stack gap="xs">
                {(detailCommentsData?.items ?? []).length === 0 ? (
                  <Text size="xs" c="dimmed">Aucun message pour cette séance.</Text>
                ) : (
                  (detailCommentsData?.items ?? []).map((c) => (
                    <Paper key={c.id} withBorder p="xs" radius="sm">
                      <Group justify="space-between" mb={2}>
                        <Text size="xs" fw={700}>{c.author_name}</Text>
                        <Text size="xs" c="dimmed">
                          {new Date(c.created_at).toLocaleString("fr-FR")}
                        </Text>
                      </Group>
                      <Text size="sm">{c.body}</Text>
                    </Paper>
                  ))
                )}
              </Stack>
            </ScrollArea.Autosize>

            {isWorkoutPast(detailWorkout) ? (
              <Text size="xs" c="dimmed" fs="italic">
                Séance passée — discussion en lecture seule.
              </Text>
            ) : (
              <Group align="flex-end" gap="xs">
                <Textarea
                  style={{ flex: 1 }}
                  placeholder="Écrire un message ou une consigne..."
                  minRows={2}
                  value={commentBody}
                  onChange={(e) => setCommentBody(e.currentTarget.value)}
                />
                <Button
                  loading={postCommentMutation.isPending}
                  disabled={!commentBody.trim()}
                  onClick={() => postCommentMutation.mutate()}
                >
                  Envoyer
                </Button>
              </Group>
            )}
          </Stack>
        )}
      </Modal>

      <Modal opened={goalOpened} onClose={closeGoal} title="Ajouter un objectif (A/B/C)">
        <Stack gap="sm">
          <TextInput
            label="Nom de l'objectif / compétition"
            placeholder="Ex : Marathon de Paris, Cyclosportive..."
            value={goalTitle}
            onChange={(e) => setGoalTitle(e.currentTarget.value)}
            required
          />
          <Group grow>
            <TextInput
              label="Date"
              type="date"
              value={goalDate}
              onChange={(e) => setGoalDate(e.currentTarget.value)}
              required
            />
            <Select
              label="Priorité"
              value={goalPriority}
              onChange={(v) => setGoalPriority((v as "A" | "B" | "C") || "A")}
              data={[
                { value: "A", label: "A — Objectif majeur" },
                { value: "B", label: "B — Préparation" },
                { value: "C", label: "C — Entraînement" },
              ]}
            />
          </Group>
          <Select
            label="Discipline"
            searchable
            value={goalSport}
            onChange={(v) => v && setGoalSport(v)}
            data={SPORTS}
          />
          <Textarea
            label="Notes (optionnel)"
            rows={2}
            value={goalNotes}
            onChange={(e) => setGoalNotes(e.currentTarget.value)}
          />
          <Group justify="flex-end" mt="md">
            <Button variant="default" onClick={closeGoal}>
              Annuler
            </Button>
            <Button
              color="yellow"
              loading={createGoalMutation.isPending}
              disabled={!goalTitle.trim() || !goalDate || !effectiveUid}
              onClick={() => createGoalMutation.mutate()}
            >
              Enregistrer l&apos;objectif
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
