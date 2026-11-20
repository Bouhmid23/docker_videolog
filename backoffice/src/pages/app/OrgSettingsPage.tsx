import { useEffect, useState } from "react";
import {
  Button,
  Divider,
  MultiSelect,
  NumberInput,
  Paper,
  Stack,
  Switch,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS } from "../../lib/constants";

export function OrgSettingsPage() {
  const { activeMembership, refreshMe } = useAuth();
  const org = activeMembership?.organization;

  const [name, setName] = useState(org?.name ?? "");
  const [sports, setSports] = useState<string[]>(org?.sports ?? []);
  const [city, setCity] = useState(org?.city ?? "");
  const [description, setDescription] = useState(org?.description ?? "");
  const [isPublic, setIsPublic] = useState<boolean>(org?.is_public !== false);
  const [acceptingJoinRequests, setAcceptingJoinRequests] = useState<boolean>(
    org?.accepting_join_requests !== false
  );
  const [maxRecordingMin, setMaxRecordingMin] = useState<number>(
    Math.max(1, Math.round((org?.settings?.max_recording_s ?? 180) / 60))
  );

  useEffect(() => {
    if (org) {
      setName(org.name ?? "");
      setSports(org.sports ?? []);
      setCity(org.city ?? "");
      setDescription(org.description ?? "");
      setIsPublic(org.is_public !== false);
      setAcceptingJoinRequests(org.accepting_join_requests !== false);
      setMaxRecordingMin(
        Math.max(1, Math.round((org.settings?.max_recording_s ?? 180) / 60))
      );
    }
  }, [org]);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/orgs/${org?.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          sports,
          city: city.trim() || null,
          description: description.trim() || null,
          is_public: isPublic,
          accepting_join_requests: acceptingJoinRequests,
          settings: {
            max_recording_s: Math.min(28800, Math.max(60, Math.round(maxRecordingMin * 60))),
          },
        }),
      }),
    onSuccess: async () => {
      await refreshMe();
      notifications.show({ color: "green", message: "Organisation mise à jour." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur lors de la sauvegarde.",
      });
    },
  });

  return (
    <Stack gap="md">
      <Title order={2}>Paramètres de l'organisation</Title>
      <Paper withBorder radius="md" p="md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <Stack>
            <TextInput
              label="Nom de l'organisation"
              required
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
            />
            <MultiSelect
              label="Sports pratiqués"
              data={SPORTS.map((s) => ({ value: s.value, label: s.label }))}
              value={sports}
              onChange={setSports}
            />
            <TextInput
              label="Ville"
              value={city}
              onChange={(e) => setCity(e.currentTarget.value)}
            />
            <Textarea
              label="Présentation publique (annuaire mobile)"
              description="Affichée aux sportifs dans l'application mobile lorsqu'ils recherchent un club ou un coach."
              placeholder="Ex : Club orienté cyclisme route, home-trainer structuré et préparation cyclosportives..."
              minRows={3}
              maxLength={1000}
              value={description}
              onChange={(e) => setDescription(e.currentTarget.value)}
            />

            <Divider label="Annuaire mobile & adhésions" />

            <Switch
              label="Visible dans l'annuaire mobile"
              description="Permet aux utilisateurs de l'application mobile de trouver votre structure et consulter vos sports."
              checked={isPublic}
              onChange={(e) => setIsPublic(e.currentTarget.checked)}
            />
            <Switch
              label="Accepter les demandes d'adhésion"
              description="Autorise les sportifs à envoyer une demande d'adhésion depuis l'application mobile."
              checked={acceptingJoinRequests}
              disabled={!isPublic}
              onChange={(e) => setAcceptingJoinRequests(e.currentTarget.checked)}
            />

            <Divider label="Sessions live & vidéo" />

            <NumberInput
              label="Durée maximale d'enregistrement vidéo (minutes)"
              description="Limite automatique des enregistrements Egress par session (1 à 480 min)."
              min={1}
              max={480}
              value={maxRecordingMin}
              onChange={(v) => setMaxRecordingMin(Math.max(1, Math.min(480, Number(v || 3))))}
            />
            <Button type="submit" loading={mutation.isPending} w="fit-content">
              Enregistrer
            </Button>
          </Stack>
        </form>
      </Paper>
    </Stack>
  );
}
