import {
  Alert,
  Badge,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ThresholdsCard } from "../../components/analysis/ThresholdsCard";
import { ApiError, apiFetch } from "../../lib/api";
import type { UserProfile } from "../../types";

interface AthleteDetailResponse {
  uid: string;
  role: string;
  group_ids: string[];
  user: UserProfile | null;
}

export function AthleteDetailPage() {
  const { uid } = useParams<{ uid: string }>();
  const { activeMembership } = useAuth();
  const orgId = activeMembership?.org_id ?? "";

  const { data, isLoading, error } = useQuery({
    queryKey: ["athlete", orgId, uid],
    queryFn: () =>
      apiFetch<AthleteDetailResponse>(`/v1/orgs/${orgId}/athletes/${uid}`),
    enabled: Boolean(orgId && uid),
  });

  if (isLoading) return <Text>Chargement...</Text>;
  if (error) {
    return (
      <Alert color="red">
        {error instanceof ApiError ? error.detail : "Impossible de charger la fiche."}
      </Alert>
    );
  }

  const user = data?.user;
  const profile = user?.athlete_profile;

  return (
    <Stack gap="md">
      <Group>
        <Button
          component={Link}
          to="/app/members"
          variant="subtle"
          leftSection={<ArrowLeft size={16} />}
        >
          Retour aux membres
        </Button>
      </Group>

      <Title order={2}>
        {user?.first_name || user?.last_name
          ? `${user.first_name ?? ""} ${user.last_name ?? ""}`.trim()
          : user?.email ?? uid}
      </Title>

      <Paper withBorder radius="md" p="md">
        <Title order={4} mb="sm">
          Profil sportif
        </Title>
        {profile ? (
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <div>
              <Text size="xs" c="dimmed">
                Sport
              </Text>
              <Text fw={500}>{profile.sport}</Text>
            </div>
            <div>
              <Text size="xs" c="dimmed">
                Niveau
              </Text>
              <Text fw={500}>{profile.level}</Text>
            </div>
            <div>
              <Text size="xs" c="dimmed">
                Date de naissance
              </Text>
              <Text fw={500}>{profile.birth_date}</Text>
            </div>
            <div>
              <Text size="xs" c="dimmed">
                Taille
              </Text>
              <Text fw={500}>
                {profile.height_cm ? `${profile.height_cm} cm` : "—"}
              </Text>
            </div>
            <div>
              <Text size="xs" c="dimmed">
                Poids
              </Text>
              <Text fw={500}>
                {profile.weight_kg ? `${profile.weight_kg} kg` : "—"}
              </Text>
            </div>
            <div>
              <Text size="xs" c="dimmed">
                FC max / FC repos
              </Text>
              <Text fw={500}>
                {profile.hr_max ?? "—"} / {profile.hr_rest ?? "—"} bpm
              </Text>
            </div>
          </SimpleGrid>
        ) : (
          <Text size="sm" c="dimmed">
            L'athlète n'a pas encore complété son profil sportif.
          </Text>
        )}

        <Group mt="md">
          <Badge color={user?.health_consent?.granted ? "teal" : "gray"} variant="light">
            {user?.health_consent?.granted
              ? "Consentement données de santé accordé"
              : "Données de santé non consenties"}
          </Badge>
        </Group>
      </Paper>

      {orgId && uid && <ThresholdsCard orgId={orgId} uid={uid} />}
    </Stack>
  );
}
