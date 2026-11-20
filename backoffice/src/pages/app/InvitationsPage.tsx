import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  CopyButton,
  Group,
  Modal,
  MultiSelect,
  Paper,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Mail, UserCheck, UserPlus, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { ROLE_LABELS, formatSportLabel } from "../../lib/constants";
import type { GroupItem, InvitationItem, JoinRequestItem } from "../../types";

const INV_STATUS_COLOR: Record<string, string> = {
  pending: "blue",
  accepted: "green",
  revoked: "gray",
  expired: "orange",
  declined: "red",
};

const INV_STATUS_LABEL: Record<string, string> = {
  pending: "En attente",
  accepted: "Acceptée",
  revoked: "Révoquée",
  expired: "Expirée",
  declined: "Refusée",
};

const REQ_STATUS_COLOR: Record<string, string> = {
  pending: "blue",
  approved: "teal",
  rejected: "red",
  cancelled: "gray",
};

const REQ_STATUS_LABEL: Record<string, string> = {
  pending: "En attente",
  approved: "Acceptée",
  rejected: "Refusée",
  cancelled: "Annulée",
};

export function InvitationsPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const orgType = activeMembership?.organization?.type ?? "club";
  const myRole = activeMembership?.role ?? "athlete";
  const isAdmin = myRole === "owner" || myRole === "admin";
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const initialTab = searchParams.get("tab") === "requests" ? "requests" : "invitations";
  const [activeTab, setActiveTab] = useState<string>(initialTab);
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);

  const [approveTarget, setApproveTarget] = useState<JoinRequestItem | null>(null);
  const [approveRole, setApproveRole] = useState<"athlete" | "coach">("athlete");
  const [approveGroups, setApproveGroups] = useState<string[]>([]);
  const [approveNote, setApproveNote] = useState("");

  const [rejectTarget, setRejectTarget] = useState<JoinRequestItem | null>(null);
  const [rejectNote, setRejectNote] = useState("");

  useEffect(() => {
    const paramTab = searchParams.get("tab");
    if (paramTab === "requests" || paramTab === "invitations") {
      setActiveTab(paramTab);
    }
  }, [searchParams]);

  const { data: invsData } = useQuery({
    queryKey: ["invitations", orgId],
    queryFn: () =>
      apiFetch<{ invitations: InvitationItem[] }>(`/v1/orgs/${orgId}/invitations`),
    enabled: Boolean(orgId),
  });

  const { data: reqsData } = useQuery({
    queryKey: ["join-requests", orgId],
    queryFn: () =>
      apiFetch<{ join_requests: JoinRequestItem[]; count: number; pending_count: number }>(
        `/v1/orgs/${orgId}/join-requests`
      ),
    enabled: Boolean(orgId),
  });

  const { data: groupsData } = useQuery({
    queryKey: ["groups", orgId],
    queryFn: () => apiFetch<{ groups: GroupItem[] }>(`/v1/orgs/${orgId}/groups`),
    enabled: Boolean(orgId) && orgType === "club",
  });

  const groupOptions =
    groupsData?.groups.map((g) => ({
      value: g.id,
      label: `${g.name} (${formatSportLabel(g.sport)})`,
    })) ?? [];

  const resendMutation = useMutation({
    mutationFn: (invId: string) =>
      apiFetch<{ email_sent: boolean; invite_url: string | null }>(
        `/v1/orgs/${orgId}/invitations/${invId}/resend`,
        { method: "POST" }
      ),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["invitations", orgId] });
      if (res.invite_url) {
        setCopiedUrl(res.invite_url);
      } else {
        notifications.show({ color: "green", message: "Invitation renvoyée." });
      }
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Renvoi impossible.",
      });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (invId: string) =>
      apiFetch(`/v1/orgs/${orgId}/invitations/${invId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invitations", orgId] });
      notifications.show({ color: "green", message: "Invitation révoquée." });
    },
  });

  const approveMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/join-requests/${approveTarget?.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          role: approveRole,
          group_ids: orgType === "club" ? approveGroups : [],
          decision_note: approveNote.trim() || null,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["join-requests", orgId] });
      queryClient.invalidateQueries({ queryKey: ["join-requests-pending", orgId] });
      queryClient.invalidateQueries({ queryKey: ["members", orgId] });
      queryClient.invalidateQueries({ queryKey: ["invitations", orgId] });
      setApproveTarget(null);
      notifications.show({
        color: "teal",
        message: "Demande acceptée : le membre a été ajouté à l'organisation.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible d'accepter la demande.",
      });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/join-requests/${rejectTarget?.id}/reject`, {
        method: "POST",
        body: JSON.stringify({
          decision_note: rejectNote.trim() || null,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["join-requests", orgId] });
      queryClient.invalidateQueries({ queryKey: ["join-requests-pending", orgId] });
      setRejectTarget(null);
      notifications.show({ color: "gray", message: "Demande d'adhésion refusée." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible de refuser la demande.",
      });
    },
  });

  const pendingReqCount = reqsData?.pending_count ?? 0;

  const openApproveModal = (req: JoinRequestItem) => {
    setApproveTarget(req);
    setApproveRole("athlete");
    setApproveGroups(req.requested_group_id ? [req.requested_group_id] : []);
    setApproveNote("");
  };

  const openRejectModal = (req: JoinRequestItem) => {
    setRejectTarget(req);
    setRejectNote("");
  };

  const formatApplicantName = (req: JoinRequestItem) => {
    const fn = req.applicant?.first_name ?? "";
    const ln = req.applicant?.last_name ?? "";
    const full = `${fn} ${ln}`.trim();
    return full || req.applicant?.email || req.uid;
  };

  return (
    <Stack gap="md">
      <Title order={2}>Invitations &amp; Demandes d'adhésion</Title>

      <Tabs
        value={activeTab}
        onChange={(v) => {
          if (v) {
            setActiveTab(v);
            setSearchParams({ tab: v }, { replace: true });
          }
        }}
      >
        <Tabs.List mb="md">
          <Tabs.Tab
            value="requests"
            leftSection={<UserPlus size={15} />}
            rightSection={
              pendingReqCount > 0 ? (
                <Badge size="xs" circle color="blue" variant="filled">
                  {pendingReqCount}
                </Badge>
              ) : null
            }
          >
            Demandes reçues (mobile)
          </Tabs.Tab>
          <Tabs.Tab value="invitations" leftSection={<Mail size={15} />}>
            Invitations envoyées
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="requests">
          <Paper withBorder radius="md" p="md">
            {reqsData?.join_requests.length ? (
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Candidat</Table.Th>
                    <Table.Th>Sports souhaités / Profil</Table.Th>
                    <Table.Th>Message &amp; Ciblage</Table.Th>
                    <Table.Th>Statut</Table.Th>
                    <Table.Th>Date</Table.Th>
                    <Table.Th>Actions</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {reqsData.join_requests.map((req) => (
                    <Table.Tr key={req.id}>
                      <Table.Td>
                        <Stack gap={2}>
                          <Text size="sm" fw={600}>
                            {formatApplicantName(req)}
                          </Text>
                          {req.applicant?.email && (
                            <Text size="xs" c="dimmed">
                              {req.applicant.email}
                            </Text>
                          )}
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={4}>
                          {req.requested_sports.length > 0 ? (
                            <Group gap={4} wrap="wrap">
                              {req.requested_sports.map((s) => (
                                <Badge key={s} size="xs" color="cyan" variant="light">
                                  {formatSportLabel(s)}
                                </Badge>
                              ))}
                            </Group>
                          ) : null}
                          {req.applicant?.athlete_profile?.sport && (
                            <Text size="xs" c="dimmed">
                              Profil : {formatSportLabel(req.applicant.athlete_profile.sport)}
                              {req.applicant.athlete_profile.level
                                ? ` · ${req.applicant.athlete_profile.level}`
                                : ""}
                            </Text>
                          )}
                        </Stack>
                      </Table.Td>
                      <Table.Td maw={280}>
                        <Stack gap={2}>
                          {req.requested_group_name && (
                            <Text size="xs" fw={500}>
                              Groupe : {req.requested_group_name}
                            </Text>
                          )}
                          {req.target_coach_name && (
                            <Text size="xs" c="dimmed">
                              Coach : {req.target_coach_name}
                            </Text>
                          )}
                          {req.message ? (
                            <Text size="xs" lineClamp={3}>
                              « {req.message} »
                            </Text>
                          ) : (
                            <Text size="xs" c="dimmed">
                              Aucun message
                            </Text>
                          )}
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Badge
                          color={REQ_STATUS_COLOR[req.status] ?? "gray"}
                          variant="light"
                        >
                          {REQ_STATUS_LABEL[req.status] ?? req.status}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs">
                          {new Date(req.created_at).toLocaleDateString("fr-FR")}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {req.status === "pending" ? (
                          <Group gap="xs">
                            <Button
                              size="xs"
                              color="teal"
                              leftSection={<UserCheck size={14} />}
                              onClick={() => openApproveModal(req)}
                            >
                              Accepter
                            </Button>
                            <Button
                              size="xs"
                              color="red"
                              variant="subtle"
                              leftSection={<X size={14} />}
                              onClick={() => openRejectModal(req)}
                            >
                              Refuser
                            </Button>
                          </Group>
                        ) : (
                          <Text size="xs" c="dimmed">
                            {req.decision_note ? `Note : ${req.decision_note}` : "Traitée"}
                          </Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Text size="sm" c="dimmed">
                Aucune demande d'adhésion reçue pour le moment.
              </Text>
            )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="invitations">
          <Paper withBorder radius="md" p="md">
            {invsData?.invitations.length ? (
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>E-mail</Table.Th>
                    <Table.Th>Rôle</Table.Th>
                    <Table.Th>Statut</Table.Th>
                    <Table.Th>Expiration</Table.Th>
                    <Table.Th>Actions</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {invsData.invitations.map((inv) => (
                    <Table.Tr key={inv.id}>
                      <Table.Td>{inv.email}</Table.Td>
                      <Table.Td>{ROLE_LABELS[inv.role] ?? inv.role}</Table.Td>
                      <Table.Td>
                        <Badge
                          color={INV_STATUS_COLOR[inv.status] ?? "gray"}
                          variant="light"
                        >
                          {INV_STATUS_LABEL[inv.status] ?? inv.status}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs">
                          {new Date(inv.expires_at).toLocaleDateString("fr-FR")}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Group gap="xs">
                          {(inv.status === "pending" || inv.status === "expired") && (
                            <Button
                              size="xs"
                              variant="light"
                              onClick={() => resendMutation.mutate(inv.id)}
                            >
                              Renvoyer
                            </Button>
                          )}
                          {inv.status === "pending" && (
                            <Button
                              size="xs"
                              color="red"
                              variant="subtle"
                              onClick={() => revokeMutation.mutate(inv.id)}
                            >
                              Révoquer
                            </Button>
                          )}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Text size="sm" c="dimmed">
                Aucune invitation envoyée.
              </Text>
            )}
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <Modal
        opened={Boolean(approveTarget)}
        onClose={() => setApproveTarget(null)}
        title="Accepter la demande d'adhésion"
      >
        <Stack>
          {approveTarget && (
            <Text size="sm">
              Accepter <strong>{formatApplicantName(approveTarget)}</strong> dans
              l'organisation.
            </Text>
          )}
          {isAdmin && orgType === "club" && (
            <Select
              label="Rôle attribué"
              value={approveRole}
              onChange={(v) => setApproveRole((v as "athlete" | "coach") ?? "athlete")}
              data={[
                { value: "athlete", label: "Athlète" },
                { value: "coach", label: "Coach" },
              ]}
            />
          )}
          {orgType === "club" && (
            <MultiSelect
              label="Groupes assignés"
              placeholder="Sélectionner un ou plusieurs groupes"
              data={groupOptions}
              value={approveGroups}
              onChange={setApproveGroups}
            />
          )}
          <Textarea
            label="Message de bienvenue (optionnel)"
            placeholder="Ex : Bienvenue au club ! Rendez-vous mardi pour la séance home-trainer."
            value={approveNote}
            onChange={(e) => setApproveNote(e.currentTarget.value)}
            maxLength={500}
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setApproveTarget(null)}>
              Annuler
            </Button>
            <Button
              color="teal"
              loading={approveMutation.isPending}
              onClick={() => approveMutation.mutate()}
            >
              Confirmer l'adhésion
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={Boolean(rejectTarget)}
        onClose={() => setRejectTarget(null)}
        title="Refuser la demande d'adhésion"
      >
        <Stack>
          {rejectTarget && (
            <Text size="sm">
              Refuser la demande de{" "}
              <strong>{formatApplicantName(rejectTarget)}</strong>.
            </Text>
          )}
          <Textarea
            label="Motif du refus (optionnel)"
            placeholder="Ex : Effectif complet pour cette saison."
            value={rejectNote}
            onChange={(e) => setRejectNote(e.currentTarget.value)}
            maxLength={500}
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setRejectTarget(null)}>
              Annuler
            </Button>
            <Button
              color="red"
              loading={rejectMutation.isPending}
              onClick={() => rejectMutation.mutate()}
            >
              Refuser la demande
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={Boolean(copiedUrl)}
        onClose={() => setCopiedUrl(null)}
        title="Nouveau lien d'invitation"
      >
        <Stack>
          <Alert color="blue">
            Copiez ce nouveau lien pour le transmettre au destinataire.
          </Alert>
          <TextInput value={copiedUrl ?? ""} readOnly />
          <CopyButton value={copiedUrl ?? ""}>
            {({ copied, copy }) => (
              <Button
                color={copied ? "teal" : "blue"}
                leftSection={copied ? <Check size={16} /> : <Copy size={16} />}
                onClick={copy}
              >
                {copied ? "Lien copié" : "Copier le lien"}
              </Button>
            )}
          </CopyButton>
        </Stack>
      </Modal>
    </Stack>
  );
}
