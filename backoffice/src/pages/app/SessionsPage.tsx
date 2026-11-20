import { useState } from "react";
import {
  ActionIcon,
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
import { Check, Copy, Play, Plus, Video } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import type { GroupItem, OrgMember, SessionItem } from "../../types";

export function SessionsPage() {
  const navigate = useNavigate();
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const orgType = activeMembership?.organization?.type ?? "club";
  const role = activeMembership?.role ?? "athlete";
  const canCreate = role === "owner" || role === "admin" || role === "coach";
  const queryClient = useQueryClient();

  const [opened, { open, close }] = useDisclosure(false);
  const [title, setTitle] = useState("");
  const [groupId, setGroupId] = useState<string | null>(null);
  const [participantUids, setParticipantUids] = useState<string[]>([]);

  const { data: sessionsData } = useQuery({
    queryKey: ["sessions", orgId],
    queryFn: () =>
      apiFetch<{ sessions: SessionItem[] }>(`/v1/orgs/${orgId}/sessions`),
    enabled: Boolean(orgId),
  });

  const { data: groupsData } = useQuery({
    queryKey: ["groups", orgId],
    queryFn: () => apiFetch<{ groups: GroupItem[] }>(`/v1/orgs/${orgId}/groups`),
    enabled: Boolean(orgId && canCreate && orgType === "club"),
  });

  const { data: membersData } = useQuery({
    queryKey: ["members", orgId],
    queryFn: () => apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members`),
    enabled: Boolean(orgId && canCreate),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/sessions`, {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          group_id: groupId,
          participant_uids: participantUids,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sessions", orgId] });
      setTitle("");
      setGroupId(null);
      setParticipantUids([]);
      close();
      notifications.show({ color: "green", message: "Session créée." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Création impossible.",
      });
    },
  });

  const memberOptions =
    membersData?.members.map((m) => ({
      value: m.uid,
      label:
        m.first_name || m.last_name
          ? `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim()
          : m.email ?? m.uid,
    })) ?? [];

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Sessions</Title>
        {canCreate && (
          <Button leftSection={<Plus size={16} />} onClick={open}>
            Nouvelle session
          </Button>
        )}
      </Group>

      <Paper withBorder radius="md" p="md">
        {sessionsData?.sessions.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Titre</Table.Th>
                <Table.Th>Identifiant room (mobile)</Table.Th>
                <Table.Th>Participants</Table.Th>
                <Table.Th>Statut</Table.Th>
                <Table.Th style={{ textAlign: "right" }}>Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sessionsData.sessions.map((s) => (
                <Table.Tr
                  key={s.room_name}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/app/sessions/${s.room_name}`)}
                >
                  <Table.Td fw={500}>{s.title || s.room_name}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <Text size="xs" ff="monospace">
                        {s.room_name}
                      </Text>
                      <CopyButton value={s.room_name}>
                        {({ copied, copy }) => (
                          <ActionIcon
                            size="sm"
                            variant="subtle"
                            color={copied ? "teal" : "gray"}
                            onClick={(e) => {
                              e.stopPropagation();
                              copy();
                            }}
                            title="Copier le nom de la room"
                          >
                            {copied ? <Check size={14} /> : <Copy size={14} />}
                          </ActionIcon>
                        )}
                      </CopyButton>
                    </Group>
                  </Table.Td>
                  <Table.Td>{s.participant_uids?.length ?? 0}</Table.Td>
                  <Table.Td>
                    {s.finished ? (
                      <Badge color="gray">Terminée</Badge>
                    ) : s.joinable === false ? (
                      <Badge color="orange">Expirée</Badge>
                    ) : s.recording ? (
                      <Badge color="red">Enregistrement</Badge>
                    ) : (
                      <Badge color="green">Active</Badge>
                    )}
                  </Table.Td>
                  <Table.Td style={{ textAlign: "right" }}>
                    <Group gap="xs" justify="flex-end">
                      {!s.finished && s.joinable !== false && (
                        <Button
                          size="xs"
                          variant="filled"
                          color="cyan"
                          leftSection={<Video size={14} />}
                          component="a"
                          href={`/app/live/${encodeURIComponent(s.room_name)}`}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Rejoindre en direct
                        </Button>
                      )}
                      <Button
                        size="xs"
                        variant="light"
                        leftSection={<Play size={14} />}
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/app/sessions/${s.room_name}`);
                        }}
                      >
                        Consulter
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text size="sm" c="dimmed">
            Aucune session disponible.
          </Text>
        )}
      </Paper>

      <Modal opened={opened} onClose={close} title="Nouvelle session">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <Stack>
            <TextInput
              label="Titre de la séance"
              placeholder="Ex : Sortie seuil mardi"
              required
              value={title}
              onChange={(e) => setTitle(e.currentTarget.value)}
            />
            {orgType === "club" && (
              <Select
                label="Groupe (optionnel)"
                clearable
                value={groupId}
                onChange={setGroupId}
                data={
                  groupsData?.groups.map((g) => ({ value: g.id, label: g.name })) ?? []
                }
              />
            )}
            <MultiSelect
              label="Participants"
              placeholder="Sélectionnez les athlètes et coachs"
              data={memberOptions}
              value={participantUids}
              onChange={setParticipantUids}
            />
            <Button type="submit" loading={createMutation.isPending}>
              Créer la session
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
