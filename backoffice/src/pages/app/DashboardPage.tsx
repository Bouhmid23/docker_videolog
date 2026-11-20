import {
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { CoachInfoList } from "../../components/profile/CoachInfoList";
import { apiFetch } from "../../lib/api";
import type {
  CalendarResponse,
  CoachOverviewRow,
  InvitationItem,
  OrgMember,
  SessionItem,
} from "../../types";

const FLAG_META: Record<string, { label: string; color: string }> = {
  overload: { label: "Surcharge (TSB < -25)", color: "red" },
  high_ramp: { label: "Montée rapide", color: "orange" },
  high_fatigue: { label: "Fatigue élevée", color: "yellow" },
  missed_workouts: { label: "Séances manquées", color: "grape" },
};

export function DashboardPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id;
  const role = activeMembership?.role ?? "athlete";
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const navigate = useNavigate();

  const { data: overviewData } = useQuery({
    queryKey: ["coach-overview", orgId],
    queryFn: () =>
      apiFetch<{ athletes: CoachOverviewRow[] }>(
        `/v1/orgs/${orgId}/coach/overview`
      ),
    enabled: Boolean(orgId && isStaff),
  });

  const todayIso = new Date().toISOString().slice(0, 10);
  const { data: todayCal } = useQuery<CalendarResponse>({
    queryKey: ["dashboard-today", orgId, todayIso],
    queryFn: () =>
      apiFetch<CalendarResponse>(
        `/v1/orgs/${orgId}/calendar?from=${todayIso}&to=${todayIso}`
      ),
    enabled: Boolean(orgId && !isStaff),
  });

  const { data: membersData } = useQuery({
    queryKey: ["members", orgId],
    queryFn: () => apiFetch<{ members: OrgMember[]; count: number }>(`/v1/orgs/${orgId}/members`),
    enabled: Boolean(orgId && isStaff),
  });

  const { data: invitesData } = useQuery({
    queryKey: ["invitations", orgId],
    queryFn: () =>
      apiFetch<{ invitations: InvitationItem[] }>(`/v1/orgs/${orgId}/invitations`),
    enabled: Boolean(orgId && isStaff),
  });

  const { data: sessionsData } = useQuery({
    queryKey: ["sessions", orgId],
    queryFn: () =>
      apiFetch<{ sessions: SessionItem[]; count: number }>(`/v1/orgs/${orgId}/sessions`),
    enabled: Boolean(orgId),
  });

  const athletesCount =
    membersData?.members.filter((m) => m.role === "athlete").length ?? 0;
  const pendingInvites =
    invitesData?.invitations.filter((i) => i.status === "pending").length ?? 0;

  return (
    <Stack gap="lg">
      <Title order={2}>Tableau de bord</Title>

      <Grid>
        {isStaff && (
          <>
            <Grid.Col span={{ base: 12, sm: 4 }}>
              <Card withBorder radius="md" p="md" className="vl-card">
                <Text size="xs" c="#98a3b0" tt="uppercase" fw={700} style={{ letterSpacing: "0.08em" }}>
                  Membres actifs
                </Text>
                <Text className="vl-kpi" fz={34} fw={700} mt={4} c="#18abf8">
                  {membersData?.count ?? 0}
                </Text>
                <Text size="xs" c="#98a3b0">
                  dont {athletesCount} athlète(s)
                </Text>
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, sm: 4 }}>
              <Card withBorder radius="md" p="md" className="vl-card">
                <Text size="xs" c="#98a3b0" tt="uppercase" fw={700} style={{ letterSpacing: "0.08em" }}>
                  Invitations en attente
                </Text>
                <Text className="vl-kpi" fz={34} fw={700} mt={4} c="#eef2f6">
                  {pendingInvites}
                </Text>
              </Card>
            </Grid.Col>
          </>
        )}
        <Grid.Col span={{ base: 12, sm: isStaff ? 4 : 12 }}>
          <Card withBorder radius="md" p="md" className="vl-card">
            <Text size="xs" c="#98a3b0" tt="uppercase" fw={700} style={{ letterSpacing: "0.08em" }}>
              Sessions
            </Text>
            <Text className="vl-kpi" fz={34} fw={700} mt={4} c="#3ddc97">
              {sessionsData?.count ?? 0}
            </Text>
          </Card>
        </Grid.Col>
      </Grid>

      {isStaff && (overviewData?.athletes?.length ?? 0) > 0 && (
        <Paper withBorder radius="md" p="md">
          <Title order={4} mb="sm">
            Suivi charge & forme des athlètes
          </Title>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Athlète</Table.Th>
                <Table.Th>Forme</Table.Th>
                <Table.Th>Fatigue</Table.Th>
                <Table.Th>Fraîcheur</Table.Th>
                <Table.Th>Effort 7j</Table.Th>
                <Table.Th>Alertes</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {overviewData!.athletes.map((a) => (
                <Table.Tr
                  key={a.uid}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/app/athletes/${a.uid}`)}
                >
                  <Table.Td fw={600}>{a.name}</Table.Td>
                  <Table.Td>{a.ctl.toFixed(1)}</Table.Td>
                  <Table.Td>{a.atl.toFixed(1)}</Table.Td>
                  <Table.Td c={a.tsb < -25 ? "red" : a.tsb >= 0 ? "teal" : undefined}>
                    {a.tsb.toFixed(1)}
                  </Table.Td>
                  <Table.Td>{Math.round(a.tss_7d)}</Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {a.flags.length === 0 ? (
                        <Badge size="xs" color="teal" variant="light">OK</Badge>
                      ) : (
                        a.flags.map((f) => (
                          <Badge
                            key={f}
                            size="xs"
                            color={FLAG_META[f]?.color ?? "gray"}
                          >
                            {FLAG_META[f]?.label ?? f}
                          </Badge>
                        ))
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}

      {!isStaff && activeMembership?.coaches && activeMembership.coaches.length > 0 && (
        <Paper withBorder radius="md" p="md">
          <Title order={4} mb="sm">
            Mon entraîneur
          </Title>
          <CoachInfoList coaches={activeMembership.coaches} />
        </Paper>
      )}

      {!isStaff && (
        <Paper withBorder radius="md" p="md">
          <Group justify="space-between" mb="sm">
            <Title order={4}>Ma séance du jour</Title>
            <Button size="xs" variant="light" onClick={() => navigate("/app/calendar")}>
              Voir mon calendrier
            </Button>
          </Group>
          {(todayCal?.planned_workouts?.length ?? 0) === 0 ? (
            <Text size="sm" c="dimmed">
              Aucune séance planifiée aujourd&apos;hui.
            </Text>
          ) : (
            <Stack gap="xs">
              {todayCal!.planned_workouts.map((w) => (
                <Group
                  key={w.id}
                  justify="space-between"
                  p="xs"
                  style={{
                    border: "1px solid #232b35",
                    borderRadius: 6,
                    cursor: "pointer",
                  }}
                  onClick={() => navigate("/app/calendar")}
                >
                  <Group gap="xs">
                    {w.start_time && <Badge color="gray">{w.start_time}</Badge>}
                    <Badge color="cyan">{w.sport}</Badge>
                    <Text fw={600} size="sm">{w.title}</Text>
                  </Group>
                  <Text size="xs" c="dimmed">
                    {Math.round(w.estimated_duration_s / 60)} min · {w.estimated_tss} pts d&apos;effort
                  </Text>
                </Group>
              ))}
            </Stack>
          )}
        </Paper>
      )}

      <Paper withBorder radius="md" p="md">
        <Group justify="space-between" mb="sm">
          <Title order={4}>Sessions récentes</Title>
          <Button size="xs" variant="subtle" onClick={() => navigate("/app/sessions")}>
            Toutes les sessions
          </Button>
        </Group>
        {sessionsData?.sessions.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Titre</Table.Th>
                <Table.Th>Room mobile</Table.Th>
                <Table.Th>Statut</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sessionsData.sessions.slice(0, 5).map((s) => (
                <Table.Tr
                  key={s.room_name}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/app/sessions/${s.room_name}`)}
                >
                  <Table.Td fw={600}>{s.title || s.room_name}</Table.Td>
                  <Table.Td>
                    <Text size="xs" ff="monospace">
                      {s.room_name}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      {s.finished ? (
                        <Badge color="gray">Terminée</Badge>
                      ) : s.recording ? (
                        <Badge color="red">Enregistrement</Badge>
                      ) : (
                        <Badge color="green">Active</Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td style={{ textAlign: "right" }}>
                    {!s.finished && s.joinable !== false && (
                      <Button
                        size="compact-xs"
                        variant="light"
                        color="cyan"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/app/live/${encodeURIComponent(s.room_name)}`);
                        }}
                      >
                        Rejoindre
                      </Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text size="sm" c="dimmed">
            Aucune session créée pour le moment.
          </Text>
        )}
      </Paper>
    </Stack>
  );
}
