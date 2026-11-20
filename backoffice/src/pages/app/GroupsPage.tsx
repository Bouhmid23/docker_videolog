import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
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
import { Plus, Trash2 } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS } from "../../lib/constants";
import type { GroupItem, OrgMember } from "../../types";

export function GroupsPage() {
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";
  const myRole = activeMembership?.role ?? "athlete";
  const isAdmin = myRole === "owner" || myRole === "admin";
  const queryClient = useQueryClient();

  const [opened, { open, close }] = useDisclosure(false);
  const [name, setName] = useState("");
  const [sport, setSport] = useState("cyclisme");
  const [coachUids, setCoachUids] = useState<string[]>([]);

  const { data: groupsData } = useQuery({
    queryKey: ["groups", orgId],
    queryFn: () => apiFetch<{ groups: GroupItem[] }>(`/v1/orgs/${orgId}/groups`),
    enabled: Boolean(orgId),
  });

  const { data: membersData } = useQuery({
    queryKey: ["members", orgId],
    queryFn: () => apiFetch<{ members: OrgMember[] }>(`/v1/orgs/${orgId}/members`),
    enabled: Boolean(orgId && isAdmin),
  });

  const coachOptions =
    membersData?.members
      .filter((m) => m.role === "coach" || m.role === "owner" || m.role === "admin")
      .map((m) => ({
        value: m.uid,
        label:
          m.first_name || m.last_name
            ? `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim()
            : m.email ?? m.uid,
      })) ?? [];

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${orgId}/groups`, {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), sport, coach_uids: coachUids }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["groups", orgId] });
      setName("");
      setCoachUids([]);
      close();
      notifications.show({ color: "green", message: "Groupe créé." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Création impossible.",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/orgs/${orgId}/groups/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["groups", orgId] });
      notifications.show({ color: "green", message: "Groupe supprimé." });
    },
  });

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Groupes</Title>
        {isAdmin && (
          <Button leftSection={<Plus size={16} />} onClick={open}>
            Nouveau groupe
          </Button>
        )}
      </Group>

      <Paper withBorder radius="md" p="md">
        {groupsData?.groups.length ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Nom</Table.Th>
                <Table.Th>Sport</Table.Th>
                <Table.Th>Coachs assignés</Table.Th>
                {isAdmin && <Table.Th>Actions</Table.Th>}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {groupsData.groups.map((g) => (
                <Table.Tr key={g.id}>
                  <Table.Td fw={500}>{g.name}</Table.Td>
                  <Table.Td>
                    <Badge variant="light">{g.sport}</Badge>
                  </Table.Td>
                  <Table.Td>{g.coach_uids.length}</Table.Td>
                  {isAdmin && (
                    <Table.Td>
                      <ActionIcon
                        color="red"
                        variant="subtle"
                        onClick={() => deleteMutation.mutate(g.id)}
                      >
                        <Trash2 size={15} />
                      </ActionIcon>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text size="sm" c="dimmed">
            Aucun groupe créé.
          </Text>
        )}
      </Paper>

      <Modal opened={opened} onClose={close} title="Nouveau groupe">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <Stack>
            <TextInput
              label="Nom du groupe"
              placeholder="Ex : U19 Route"
              required
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
            />
            <Select
              label="Sport"
              value={sport}
              onChange={(v) => setSport(v ?? "autre")}
              data={SPORTS.map((s) => ({ value: s.value, label: s.label }))}
            />
            <MultiSelect
              label="Coachs responsables"
              data={coachOptions}
              value={coachUids}
              onChange={setCoachUids}
            />
            <Button type="submit" loading={createMutation.isPending}>
              Créer
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
