import {
  ActionIcon,
  Alert,
  AppShell,
  Badge,
  Box,
  Burger,
  Group,
  Image,
  Indicator,
  NavLink,
  Popover,
  ScrollArea,
  Select,
  Text,
  Button,
  Stack,
  Divider,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  LayoutDashboard,
  Users,
  Mail,
  Layers,
  Video,
  Activity,
  Calendar,
  Dumbbell,
  TrendingUp,
  Settings,
  UserCircle,
  LogOut,
} from "lucide-react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { ApiError, apiFetch } from "../lib/api";
import { ROLE_LABELS } from "../lib/constants";
import type { NotificationItem, PendingInvitation } from "../types";

export function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();
  const { me, activeMembership, setActiveOrgId, refreshMe, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: pendingInvsData } = useQuery<{ invitations: PendingInvitation[] }>({
    queryKey: ["my-invitations", me?.uid],
    queryFn: () => apiFetch<{ invitations: PendingInvitation[] }>("/v1/me/invitations"),
    enabled: Boolean(me?.uid),
  });

  const acceptInvMutation = useMutation({
    mutationFn: (invId: string) =>
      apiFetch<{ status: string; org_id: string; role: string }>(
        `/v1/me/invitations/${invId}/accept`,
        { method: "POST" }
      ),
    onSuccess: async (res) => {
      await refreshMe();
      setActiveOrgId(res.org_id);
      queryClient.invalidateQueries({ queryKey: ["my-invitations"] });
      notifications.show({
        color: "teal",
        message: "Invitation acceptée : vous êtes maintenant rattaché à cette organisation.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible d'accepter l'invitation.",
      });
    },
  });

  const declineInvMutation = useMutation({
    mutationFn: (invId: string) =>
      apiFetch(`/v1/me/invitations/${invId}/decline`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-invitations"] });
      notifications.show({ color: "gray", message: "Invitation refusée." });
    },
  });

  const pendingInvs = pendingInvsData?.invitations ?? [];

  const { data: notifsData } = useQuery<{ items: NotificationItem[]; unread_count: number }>({
    queryKey: ["my-notifications", me?.uid],
    queryFn: () =>
      apiFetch<{ items: NotificationItem[]; unread_count: number }>("/v1/me/notifications"),
    enabled: Boolean(me?.uid),
    refetchInterval: 30000,
  });

  const markReadMutation = useMutation({
    mutationFn: (notifId: string) =>
      apiFetch(`/v1/me/notifications/${notifId}/read`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-notifications"] });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: () => apiFetch("/v1/me/notifications/read-all", { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-notifications"] });
    },
  });

  const notifItems = notifsData?.items ?? [];
  const unreadNotifs = notifsData?.unread_count ?? 0;

  const role = activeMembership?.role ?? "athlete";
  const org = activeMembership?.organization;
  const isStaff = role === "owner" || role === "admin" || role === "coach";
  const isAdmin = role === "owner" || role === "admin";

  const { data: orgJoinReqsData } = useQuery<{ pending_count: number }>({
    queryKey: ["join-requests-pending", activeMembership?.org_id],
    queryFn: () =>
      apiFetch<{ pending_count: number }>(
        `/v1/orgs/${activeMembership?.org_id}/join-requests?status=pending`
      ),
    enabled: Boolean(activeMembership?.org_id) && isStaff,
    refetchInterval: 30000,
  });
  const pendingJoinReqCount = orgJoinReqsData?.pending_count ?? 0;

  const navItems = [
    { label: "Tableau de bord", path: "/app", icon: LayoutDashboard, show: true, badge: 0 },
    { label: "Membres", path: "/app/members", icon: Users, show: isStaff, badge: 0 },
    {
      label: "Invitations & Demandes",
      path: "/app/invitations",
      icon: Mail,
      show: isStaff,
      badge: pendingJoinReqCount,
    },
    { label: "Groupes", path: "/app/groups", icon: Layers, show: isStaff && org?.type === "club", badge: 0 },
    { label: "Sessions", path: "/app/sessions", icon: Video, show: true, badge: 0 },
    { label: "Activités", path: "/app/activities", icon: Activity, show: true, badge: 0 },
    { label: "Calendrier", path: "/app/calendar", icon: Calendar, show: true, badge: 0 },
    { label: "Séances", path: "/app/workouts", icon: Dumbbell, show: isStaff, badge: 0 },
    { label: "Plans", path: "/app/plans", icon: Layers, show: isStaff, badge: 0 },
    { label: "Performance", path: "/app/performance", icon: TrendingUp, show: true, badge: 0 },
    { label: "Organisation", path: "/app/settings", icon: Settings, show: isAdmin, badge: 0 },
    { label: "Mon profil", path: "/app/profile", icon: UserCircle, show: true, badge: 0 },
  ];

  const handleNav = (path: string) => {
    navigate(path);
    close();
  };

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 250, breakpoint: "sm", collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header className="vl-header-glass">
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Group gap={10} style={{ cursor: "pointer" }} onClick={() => handleNav("/app")}>
              <Image src="/logo.png" alt="VideoLog" h={30} w="auto" />
              <Text className="vl-display" fw={700} size="lg" c="#eef2f6">
                VIDEOLOG
              </Text>
            </Group>
            <Box
              visibleFrom="xs"
              style={{
                borderLeft: "1px solid #232b35",
                paddingLeft: 10,
              }}
            >
              <Text size="xs" fw={600} c="#98a3b0" tt="uppercase" style={{ letterSpacing: "0.08em" }}>
                Espace Club & Coach
              </Text>
            </Box>
            {org && (
              <Badge variant="light" color="cyan">
                {org.name}
              </Badge>
            )}
          </Group>
          <Group gap="sm">
            <Popover width={320} position="bottom-end" shadow="md">
              <Popover.Target>
                <Indicator
                  inline
                  label={unreadNotifs}
                  size={16}
                  color="red"
                  disabled={unreadNotifs === 0}
                >
                  <ActionIcon variant="subtle" color="gray" aria-label="Notifications">
                    <Bell size={18} />
                  </ActionIcon>
                </Indicator>
              </Popover.Target>
              <Popover.Dropdown>
                <Stack gap="xs">
                  <Group justify="space-between">
                    <Text size="sm" fw={600}>Notifications</Text>
                    {unreadNotifs > 0 && (
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        onClick={() => markAllReadMutation.mutate()}
                      >
                        Tout marquer lu
                      </Button>
                    )}
                  </Group>
                  <Divider />
                  {notifItems.length === 0 ? (
                    <Text size="xs" c="dimmed" py="sm">Aucune notification.</Text>
                  ) : (
                    <ScrollArea.Autosize mah={260}>
                      <Stack gap={6}>
                        {notifItems.map((n) => (
                          <Box
                            key={n.id}
                            p="xs"
                            style={{
                              borderRadius: 6,
                              backgroundColor: n.read ? "transparent" : "rgba(24, 171, 248, 0.08)",
                              cursor: n.link ? "pointer" : "default",
                            }}
                            onClick={() => {
                              if (!n.read) markReadMutation.mutate(n.id);
                              if (n.link) {
                                if (n.org_id) setActiveOrgId(n.org_id);
                                navigate(n.link);
                              }
                            }}
                          >
                            <Text size="xs" fw={n.read ? 500 : 700}>{n.title}</Text>
                            <Text size="xs" c="dimmed" lineClamp={2}>{n.body}</Text>
                          </Box>
                        ))}
                      </Stack>
                    </ScrollArea.Autosize>
                  )}
                </Stack>
              </Popover.Dropdown>
            </Popover>
            <Badge variant="outline" color="gray" style={{ borderColor: "#232b35", color: "#98a3b0" }}>
              {ROLE_LABELS[role] ?? role}
            </Badge>
            <Text size="sm" c="#98a3b0" visibleFrom="sm">
              {me?.user?.first_name
                ? `${me.user.first_name} ${me.user.last_name ?? ""}`
                : me?.email}
            </Text>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md" className="vl-navbar">
        {me && me.memberships.length > 1 && (
          <AppShell.Section mb="xs">
            <Select
              label="Organisation active"
              size="xs"
              value={activeMembership?.org_id ?? null}
              onChange={(v) => v && setActiveOrgId(v)}
              data={me.memberships.map((m) => ({
                value: m.org_id,
                label: m.organization?.name ?? m.org_id,
              }))}
            />
            <Divider mt="xs" />
          </AppShell.Section>
        )}

        <AppShell.Section grow component={ScrollArea} mx="-md" px="md">
          <Stack gap={4}>
            {navItems
              .filter((item) => item.show)
              .map((item) => {
                const Icon = item.icon;
                const active =
                  item.path === "/app"
                    ? location.pathname === "/app"
                    : location.pathname.startsWith(item.path);
                return (
                  <NavLink
                    key={item.path}
                    label={item.label}
                    leftSection={<Icon size={17} />}
                    rightSection={
                      item.badge > 0 ? (
                        <Badge size="xs" circle color="blue" variant="filled">
                          {item.badge}
                        </Badge>
                      ) : undefined
                    }
                    active={active}
                    onClick={() => handleNav(item.path)}
                    style={{ borderRadius: 6 }}
                  />
                );
              })}
          </Stack>
        </AppShell.Section>

        <AppShell.Section pt="xs">
          <Divider mb="xs" />
          <Button
            fullWidth
            variant="subtle"
            color="red"
            leftSection={<LogOut size={16} />}
            onClick={logout}
            justify="flex-start"
          >
            Déconnexion
          </Button>
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main bg="transparent">
        {pendingInvs.length > 0 && (
          <Stack gap="xs" mb="md">
            {pendingInvs.map((inv) => (
              <Alert
                key={inv.id}
                color="cyan"
                variant="light"
                title={`Invitation reçue : ${inv.org_name}`}
              >
                <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                  <Text size="sm">
                    {inv.inviter_name ? `${inv.inviter_name} vous invite` : "Vous êtes invité(e)"} à rejoindre{" "}
                    <strong>{inv.org_name}</strong> ({inv.org_type === "solo_coach" ? "Coach" : "Club"}) en tant que{" "}
                    <strong>{ROLE_LABELS[inv.role] ?? inv.role}</strong>.
                  </Text>
                  <Group gap="xs">
                    <Button
                      size="xs"
                      color="teal"
                      loading={acceptInvMutation.isPending}
                      onClick={() => acceptInvMutation.mutate(inv.id)}
                    >
                      Accepter
                    </Button>
                    <Button
                      size="xs"
                      variant="subtle"
                      color="gray"
                      loading={declineInvMutation.isPending}
                      onClick={() => declineInvMutation.mutate(inv.id)}
                    >
                      Refuser
                    </Button>
                  </Group>
                </Group>
              </Alert>
            ))}
          </Stack>
        )}
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
