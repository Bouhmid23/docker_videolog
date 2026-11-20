import { useState } from "react";
import {
  ActionIcon,
  Alert,
  Anchor,
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
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Trash2, UserPlus } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { ROLE_LABELS } from "../../lib/constants";
import type { GroupItem, MemberRole, OrgMember } from "../../types";

export function MembersPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const orgType = activeMembership?.organization?.type ?? "club";
  const myRole = activeMembership?.role ?? "athlete";
  const isAdmin = myRole === "owner" || myRole === "admin";
  const queryClient = useQueryClient();

  const [roleFilter, setRoleFilter] = useState<string | null>(null);
  const [groupFilter, setGroupFilter] = useState<string | null>(null);

  const [inviteOpen, { open: openInvite, close: closeInvite }] = useDisclosure(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"athlete" | "coach">("athlete");
  const [inviteGroups, setInviteGroups] = useState<string[]>([]);
  const [generatedLink, setGeneratedLink] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

  const [editMember, setEditMember] = useState<OrgMember | null>(null);
  const [editRole, setEditRole] = useState<MemberRole>("athlete");
  const [editGroups, setEditGroups] = useState<string[]>([]);

  const { data: membersData } = useQuery({
    queryKey: ["members", orgId, roleFilter, groupFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (roleFilter) params.set("role", roleFilter);
      if (groupFilter) params.set("group_id", groupFilter);
      const qs = params.toString() ? `?${params.toString()}` : "";
      return apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members${qs}`);
    },
    enabled: Boolean(orgId),
  });

  const { data: groupsData } = useQuery({
    queryKey: ["groups", orgId],
    queryFn: () => apiFetch<{ groups: GroupItem[] }>(`/v1/orgs/${orgId}/groups`),
    enabled: Boolean(orgId),
  });

  const groupOptions =
    groupsData?.groups.map((g) => ({ value: g.id, label: g.name })) ?? [];

  const inviteMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ email_sent: boolean; invite_url: string | null }>(
        `/v1/orgs/${orgId}/invitations`,
        {
          method: "POST",
          body: JSON.stringify({
            email: inviteEmail.trim(),
            role: inviteRole,
            group_ids: inviteGroups,
          }),
        }
      ),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["invitations", orgId] });
      setInviteError(null);
      if (res.invite_url) {
        setGeneratedLink(res.invite_url);
      } else {
        notifications.show({
          color: "green",
          message: `Invitation envoyée par e-mail à ${inviteEmail}`,
        });
        setInviteEmail("");
        closeInvite();
      }
    },
    onError: (err) => {
      setInviteError(
        err instanceof ApiError ? err.detail : "Impossible de créer l'invitation."
      );
    },
  });

  const updateMemberMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/members/${editMember?.uid}`, {
        method: "PATCH",
        body: JSON.stringify({
          role: editMember?.role === "owner" ? undefined : editRole,
          group_ids: editGroups,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["members", orgId] });
      setEditMember(null);
      notifications.show({ color: "green", message: "Membre mis à jour." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur lors de la mise à jour.",
      });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (uid: string) =>
      apiFetch(`/v1/orgs/${orgId}/members/${uid}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["members", orgId] });
      notifications.show({ color: "green", message: "Membre retiré." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Suppression impossible.",
      });
    },
  });

  const handleOpenInvite = () => {
    setGeneratedLink(null);
    setInviteError(null);
    setInviteEmail("");
    setInviteRole("athlete");
    setInviteGroups([]);
    openInvite();
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Membres</Title>
        <Button leftSection={<UserPlus size={16} />} onClick={handleOpenInvite}>
          Inviter
        </Button>
      </Group>

      <Group>
        <Select
          placeholder="Tous les rôles"
          clearable
          value={roleFilter}
          onChange={setRoleFilter}
          data={[
            { value: "owner", label: "Propriétaire" },
            { value: "admin", label: "Administrateur" },
            { value: "coach", label: "Coach" },
            { value: "athlete", label: "Athlète" },
          ]}
        />
        {orgType === "club" && (
          <Select
            placeholder="Tous les groupes"
            clearable
            value={groupFilter}
            onChange={setGroupFilter}
            data={groupOptions}
          />
        )}
      </Group>

      <Paper withBorder radius="md" p="md">
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Nom</Table.Th>
              <Table.Th>E-mail</Table.Th>
              <Table.Th>Rôle</Table.Th>
              <Table.Th>Profil / Sports</Table.Th>
              {isAdmin && <Table.Th>Actions</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {membersData?.members.map((m) => (
              <Table.Tr key={m.uid}>
                <Table.Td>
                  {m.role === "athlete" ? (
                    <Anchor component={Link} to={`/app/athletes/${m.uid}`} fw={500}>
                      {m.first_name || m.last_name
                        ? `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim()
                        : m.email ?? m.uid}
                    </Anchor>
                  ) : (
                    <Text size="sm" fw={500}>
                      {m.first_name || m.last_name
                        ? `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim()
                        : m.email ?? m.uid}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{m.email ?? "—"}</Table.Td>
                <Table.Td>
                  <Badge variant="light">{ROLE_LABELS[m.role] ?? m.role}</Badge>
                </Table.Td>
                <Table.Td>
                  {m.role !== "athlete" &&
                  m.coach_profile?.sports &&
                  m.coach_profile.sports.length > 0 ? (
                    <Group gap={4} wrap="wrap">
                      {m.coach_profile.sports.map((s) => (
                        <Badge key={s.sport} color="cyan" variant="light">
                          {s.sport} · {s.level}
                        </Badge>
                      ))}
                    </Group>
                  ) : m.has_athlete_profile ? (
                    <Badge color="teal" variant="light">
                      {m.sport} · {m.level}
                    </Badge>
                  ) : (
                    <Text size="xs" c="dimmed">
                      Non renseigné
                    </Text>
                  )}
                </Table.Td>
                {isAdmin && (
                  <Table.Td>
                    <Group gap="xs">
                      {orgType === "club" && (
                        <Button
                          size="xs"
                          variant="subtle"
                          onClick={() => {
                            setEditMember(m);
                            setEditRole(m.role);
                            setEditGroups(m.group_ids);
                          }}
                        >
                          Modifier
                        </Button>
                      )}
                      {m.role !== "owner" && (
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          onClick={() => removeMutation.mutate(m.uid)}
                          title="Retirer"
                        >
                          <Trash2 size={15} />
                        </ActionIcon>
                      )}
                    </Group>
                  </Table.Td>
                )}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>

      <Modal opened={inviteOpen} onClose={closeInvite} title="Inviter un membre">
        {generatedLink ? (
          <Stack>
            <Alert color="blue" title="Lien d'invitation prêt">
              Aucun serveur SMTP n'est configuré. Copiez ce lien et transmettez-le à la personne
              invitée (valable 7 jours).
            </Alert>
            <TextInput value={generatedLink} readOnly />
            <Group justify="space-between">
              <CopyButton value={generatedLink}>
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
              <Button variant="subtle" onClick={closeInvite}>
                Fermer
              </Button>
            </Group>
          </Stack>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              inviteMutation.mutate();
            }}
          >
            <Stack>
              {inviteError && <Alert color="red">{inviteError}</Alert>}
              <TextInput
                label="Adresse e-mail"
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.currentTarget.value)}
              />
              {isAdmin && orgType === "club" && (
                <Select
                  label="Rôle"
                  value={inviteRole}
                  onChange={(v) => setInviteRole((v as "athlete" | "coach") ?? "athlete")}
                  data={[
                    { value: "athlete", label: "Athlète" },
                    { value: "coach", label: "Coach" },
                  ]}
                />
              )}
              {orgType === "club" && (
                <MultiSelect
                  label="Groupes"
                  placeholder="Sélectionner des groupes"
                  data={groupOptions}
                  value={inviteGroups}
                  onChange={setInviteGroups}
                />
              )}
              <Button type="submit" loading={inviteMutation.isPending}>
                Créer l'invitation
              </Button>
            </Stack>
          </form>
        )}
      </Modal>

      <Modal
        opened={Boolean(editMember)}
        onClose={() => setEditMember(null)}
        title="Modifier le membre"
      >
        <Stack>
          {editMember?.role !== "owner" && orgType === "club" && (
            <Select
              label="Rôle"
              value={editRole}
              onChange={(v) => setEditRole((v as MemberRole) ?? "athlete")}
              data={[
                { value: "admin", label: "Administrateur" },
                { value: "coach", label: "Coach" },
                { value: "athlete", label: "Athlète" },
              ]}
            />
          )}
          {orgType === "club" && (
            <MultiSelect
              label="Groupes"
              data={groupOptions}
              value={editGroups}
              onChange={setEditGroups}
            />
          )}
          <Button
            onClick={() => updateMemberMutation.mutate()}
            loading={updateMemberMutation.isPending}
          >
            Enregistrer
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
