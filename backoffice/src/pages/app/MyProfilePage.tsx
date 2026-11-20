import { useEffect, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
import { ThresholdsCard } from "../../components/analysis/ThresholdsCard";
import { CoachInfoList } from "../../components/profile/CoachInfoList";
import { ApiError, apiFetch } from "../../lib/api";
import { LEVELS, ROLE_LABELS, SPORTS, computeAge } from "../../lib/constants";
import type { CoachSportEntry } from "../../types";

export function MyProfilePage() {
  const { me, activeMembership, setActiveOrgId, refreshMe } = useAuth();
  const user = me?.user;
  const ap = user?.athlete_profile;
  const cp = user?.coach_profile;
  const activeRole = activeMembership?.role ?? "athlete";
  const isStaffRole =
    activeRole === "owner" || activeRole === "admin" || activeRole === "coach";
  const isAthlete = activeRole === "athlete" || (!isStaffRole && Boolean(ap));

  const leaveOrgMutation = useMutation({
    mutationFn: (targetOrgId: string) =>
      apiFetch(`/v1/orgs/${targetOrgId}/members/me`, { method: "DELETE" }),
    onSuccess: async () => {
      await refreshMe();
      notifications.show({
        color: "teal",
        message: "Vous avez quitté cette organisation.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Impossible de quitter cette organisation.",
      });
    },
  });

  const [firstName, setFirstName] = useState(user?.first_name ?? "");
  const [lastName, setLastName] = useState(user?.last_name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");

  const [sport, setSport] = useState(ap?.sport ?? "cyclisme");
  const [level, setLevel] = useState(ap?.level ?? "loisir");
  const [birthDate, setBirthDate] = useState(ap?.birth_date ?? "");
  const [heightCm, setHeightCm] = useState<number | string>(ap?.height_cm ?? "");
  const [weightKg, setWeightKg] = useState<number | string>(ap?.weight_kg ?? "");
  const [hrMax, setHrMax] = useState<number | string>(ap?.hr_max ?? "");
  const [hrRest, setHrRest] = useState<number | string>(ap?.hr_rest ?? "");
  const [healthConsent, setHealthConsent] = useState(
    Boolean(user?.health_consent?.granted)
  );
  const [profileError, setProfileError] = useState<string | null>(null);

  const [coachSports, setCoachSports] = useState<CoachSportEntry[]>(
    cp?.sports && cp.sports.length > 0
      ? cp.sports
      : [{ sport: "cyclisme", level: "loisir", years_experience: null }]
  );
  const [coachCertifications, setCoachCertifications] = useState(
    cp?.certifications ?? ""
  );
  const [coachBio, setCoachBio] = useState(cp?.bio ?? "");

  useEffect(() => {
    if (user) {
      setFirstName(user.first_name ?? "");
      setLastName(user.last_name ?? "");
      setPhone(user.phone ?? "");
      setHealthConsent(Boolean(user.health_consent?.granted));
      if (user.athlete_profile) {
        setSport(user.athlete_profile.sport);
        setLevel(user.athlete_profile.level);
        setBirthDate(user.athlete_profile.birth_date);
        setHeightCm(user.athlete_profile.height_cm ?? "");
        setWeightKg(user.athlete_profile.weight_kg ?? "");
        setHrMax(user.athlete_profile.hr_max ?? "");
        setHrRest(user.athlete_profile.hr_rest ?? "");
      }
      if (user.coach_profile) {
        setCoachSports(
          user.coach_profile.sports.length > 0
            ? user.coach_profile.sports
            : [{ sport: "cyclisme", level: "loisir", years_experience: null }]
        );
        setCoachCertifications(user.coach_profile.certifications ?? "");
        setCoachBio(user.coach_profile.bio ?? "");
      }
    }
  }, [user]);

  const age = computeAge(birthDate);
  const underAge = age !== null && age < 15;

  const identityMutation = useMutation({
    mutationFn: () =>
      apiFetch("/v1/me", {
        method: "PATCH",
        body: JSON.stringify({
          first_name: firstName.trim(),
          last_name: lastName.trim(),
          phone: phone.trim() || null,
        }),
      }),
    onSuccess: async () => {
      await refreshMe();
      notifications.show({ color: "green", message: "Identité enregistrée." });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof ApiError ? err.detail : "Erreur lors de la sauvegarde.",
      });
    },
  });

  const athleteMutation = useMutation({
    mutationFn: () =>
      apiFetch("/v1/me/athlete-profile", {
        method: "PUT",
        body: JSON.stringify({
          sport,
          level,
          birth_date: birthDate,
          height_cm: heightCm === "" ? null : Number(heightCm),
          weight_kg: healthConsent && weightKg !== "" ? Number(weightKg) : null,
          hr_max: healthConsent && hrMax !== "" ? Number(hrMax) : null,
          hr_rest: healthConsent && hrRest !== "" ? Number(hrRest) : null,
          health_consent: healthConsent,
        }),
      }),
    onSuccess: async () => {
      setProfileError(null);
      await refreshMe();
      notifications.show({ color: "green", message: "Profil sportif enregistré." });
    },
    onError: (err) => {
      setProfileError(
        err instanceof ApiError ? err.detail : "Impossible d'enregistrer le profil."
      );
    },
  });

  const revokeMutation = useMutation({
    mutationFn: () => apiFetch("/v1/me/health-consent", { method: "DELETE" }),
    onSuccess: async () => {
      setHealthConsent(false);
      setWeightKg("");
      setHrMax("");
      setHrRest("");
      await refreshMe();
      notifications.show({
        color: "blue",
        message: "Consentement retiré et données de santé effacées.",
      });
    },
  });

  const coachMutation = useMutation({
    mutationFn: () =>
      apiFetch("/v1/me/coach-profile", {
        method: "PUT",
        body: JSON.stringify({
          sports: coachSports,
          certifications: coachCertifications.trim() || null,
          bio: coachBio.trim() || null,
        }),
      }),
    onSuccess: async () => {
      await refreshMe();
      notifications.show({
        color: "green",
        message: "Profil entraîneur enregistré.",
      });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message:
          err instanceof ApiError
            ? err.detail
            : "Impossible d'enregistrer le profil entraîneur.",
      });
    },
  });

  return (
    <Stack gap="lg">
      <Title order={2}>Mon profil</Title>

      <Paper withBorder radius="md" p="md">
        <Title order={4} mb="sm">
          Identité
        </Title>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            identityMutation.mutate();
          }}
        >
          <Stack>
            <Group grow>
              <TextInput
                label="Prénom"
                required
                value={firstName}
                onChange={(e) => setFirstName(e.currentTarget.value)}
              />
              <TextInput
                label="Nom"
                required
                value={lastName}
                onChange={(e) => setLastName(e.currentTarget.value)}
              />
            </Group>
            <TextInput
              label="Téléphone"
              value={phone}
              onChange={(e) => setPhone(e.currentTarget.value)}
            />
            <Button type="submit" loading={identityMutation.isPending} w="fit-content">
              Enregistrer l'identité
            </Button>
          </Stack>
        </form>
      </Paper>

      {isAthlete && (
        <Paper withBorder radius="md" p="md">
          <Title order={4} mb="sm">
            Profil sportif
          </Title>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (underAge) {
                setProfileError(
                  "L'accès à la plateforme est interdit aux personnes de moins de 15 ans."
                );
                return;
              }
              athleteMutation.mutate();
            }}
          >
            <Stack>
              {profileError && <Alert color="red">{profileError}</Alert>}
              {underAge && (
                <Alert color="red">
                  L'accès à Videolog est strictement réservé aux personnes âgées de 15 ans et plus.
                </Alert>
              )}

              <Group grow>
                <Select
                  label="Sport principal"
                  value={sport}
                  onChange={(v) => setSport(v ?? "autre")}
                  data={SPORTS.map((s) => ({ value: s.value, label: s.label }))}
                />
                <Select
                  label="Niveau"
                  value={level}
                  onChange={(v) => setLevel(v ?? "loisir")}
                  data={LEVELS.map((l) => ({ value: l.value, label: l.label }))}
                />
              </Group>

              <Group grow>
                <TextInput
                  label="Date de naissance"
                  type="date"
                  required
                  value={birthDate}
                  onChange={(e) => setBirthDate(e.currentTarget.value)}
                />
                <NumberInput
                  label="Taille (cm)"
                  min={100}
                  max={230}
                  value={heightCm}
                  onChange={setHeightCm}
                />
              </Group>

              <Divider label="Données physiologiques et de santé (optionnel)" />

              <Checkbox
                label="J'autorise explicitement Videolog et mon club/coach à collecter et traiter mes données physiologiques (poids, FC max, FC de repos) pour le suivi de mon entraînement."
                checked={healthConsent}
                onChange={(e) => setHealthConsent(e.currentTarget.checked)}
              />

              <Text size="xs" c="dimmed">
                Sans consentement, ces champs restent vides et ne sont pas enregistrés.
              </Text>

              <Group grow>
                <NumberInput
                  label="Poids (kg)"
                  min={25}
                  max={250}
                  disabled={!healthConsent}
                  value={weightKg}
                  onChange={setWeightKg}
                />
                <NumberInput
                  label="FC max (bpm)"
                  min={40}
                  max={250}
                  disabled={!healthConsent}
                  value={hrMax}
                  onChange={setHrMax}
                />
                <NumberInput
                  label="FC repos (bpm)"
                  min={25}
                  max={120}
                  disabled={!healthConsent}
                  value={hrRest}
                  onChange={setHrRest}
                />
              </Group>

              <Group>
                <Button
                  type="submit"
                  loading={athleteMutation.isPending}
                  disabled={underAge}
                >
                  Enregistrer le profil sportif
                </Button>
                {user?.health_consent?.granted && (
                  <Button
                    variant="subtle"
                    color="red"
                    onClick={() => revokeMutation.mutate()}
                    loading={revokeMutation.isPending}
                  >
                    Retirer mon consentement et effacer ces données
                  </Button>
                )}
              </Group>
            </Stack>
          </form>
        </Paper>
      )}

      {isAthlete && activeMembership?.org_id && me?.uid && (
        <ThresholdsCard orgId={activeMembership.org_id} uid={me.uid} />
      )}

      {isStaffRole && (
        <Paper withBorder radius="md" p="md">
          <Title order={4} mb="xs">
            Profil entraîneur
          </Title>
          <Text size="xs" c="dimmed" mb="md">
            Renseignez les sports que vous encadrez, votre niveau dans chaque discipline et vos qualifications.
          </Text>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              coachMutation.mutate();
            }}
          >
            <Stack gap="md">
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="sm" fw={600}>
                    Sports encadrés &amp; niveau
                  </Text>
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<Plus size={14} />}
                    onClick={() =>
                      setCoachSports((prev) => [
                        ...prev,
                        { sport: "autre", level: "loisir", years_experience: null },
                      ])
                    }
                  >
                    Ajouter un sport
                  </Button>
                </Group>

                {coachSports.map((entry, idx) => (
                  <Paper key={idx} withBorder p="xs" radius="sm">
                    <Group gap="xs" align="flex-end" wrap="wrap">
                      <Select
                        label="Sport"
                        size="xs"
                        style={{ flex: 1, minWidth: 160 }}
                        value={entry.sport}
                        onChange={(v) =>
                          v &&
                          setCoachSports((prev) =>
                            prev.map((item, i) =>
                              i === idx ? { ...item, sport: v } : item
                            )
                          )
                        }
                        data={SPORTS.map((s) => ({ value: s.value, label: s.label }))}
                      />
                      <Select
                        label="Niveau"
                        size="xs"
                        style={{ flex: 1, minWidth: 170 }}
                        value={entry.level}
                        onChange={(v) =>
                          v &&
                          setCoachSports((prev) =>
                            prev.map((item, i) =>
                              i === idx ? { ...item, level: v } : item
                            )
                          )
                        }
                        data={LEVELS.map((l) => ({ value: l.value, label: l.label }))}
                      />
                      <NumberInput
                        label="Expérience (ans)"
                        size="xs"
                        style={{ width: 120 }}
                        min={0}
                        max={70}
                        value={entry.years_experience ?? ""}
                        onChange={(v) =>
                          setCoachSports((prev) =>
                            prev.map((item, i) =>
                              i === idx
                                ? {
                                    ...item,
                                    years_experience: v === "" ? null : Number(v),
                                  }
                                : item
                            )
                          )
                        }
                      />
                      {coachSports.length > 1 && (
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          onClick={() =>
                            setCoachSports((prev) =>
                              prev.filter((_, i) => i !== idx)
                            )
                          }
                          title="Retirer ce sport"
                        >
                          <Trash2 size={14} />
                        </ActionIcon>
                      )}
                    </Group>
                  </Paper>
                ))}
              </Stack>

              <TextInput
                label="Diplômes / Certifications (optionnel)"
                placeholder="Ex : DEJEPS Cyclisme, BF3 Triathlon, BPJEPS..."
                value={coachCertifications}
                onChange={(e) => setCoachCertifications(e.currentTarget.value)}
              />

              <Textarea
                label="Présentation &amp; spécialités (optionnel)"
                placeholder="Ex : Préparation endurance, contre-la-montre, reprise après blessure..."
                rows={3}
                value={coachBio}
                onChange={(e) => setCoachBio(e.currentTarget.value)}
              />

              <Button
                type="submit"
                loading={coachMutation.isPending}
                w="fit-content"
              >
                Enregistrer le profil entraîneur
              </Button>
            </Stack>
          </form>
        </Paper>
      )}

      {(me?.memberships?.length ?? 0) > 0 && (
        <Paper withBorder p="md" radius="md">
          <Stack gap="sm">
            <Title order={4}>Mes clubs &amp; coachs</Title>
            <Text size="xs" c="dimmed">
              Vous pouvez être rattaché à plusieurs clubs ou coachs simultanément.
            </Text>
            {me!.memberships.map((m) => (
              <Stack
                key={m.org_id}
                gap="xs"
                p="xs"
                style={{ border: "1px solid #232b35", borderRadius: 6 }}
              >
                <Group justify="space-between" align="center">
                  <Group gap="xs">
                    <Text fw={600} size="sm">
                      {m.organization?.name ?? m.org_id}
                    </Text>
                    <Badge size="xs" variant="light" color="cyan">
                      {m.organization?.type === "solo_coach" ? "Coach" : "Club"}
                    </Badge>
                    <Badge size="xs" variant="outline">
                      {ROLE_LABELS[m.role] ?? m.role}
                    </Badge>
                    {activeMembership?.org_id === m.org_id && (
                      <Badge size="xs" color="teal">
                        Actif
                      </Badge>
                    )}
                  </Group>
                  <Group gap="xs">
                    {activeMembership?.org_id !== m.org_id && (
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => setActiveOrgId(m.org_id)}
                      >
                        Basculer
                      </Button>
                    )}
                    {m.role !== "owner" && (
                      <Button
                        size="xs"
                        variant="subtle"
                        color="red"
                        loading={leaveOrgMutation.isPending}
                        onClick={() => leaveOrgMutation.mutate(m.org_id)}
                      >
                        Quitter
                      </Button>
                    )}
                  </Group>
                </Group>
                {m.coaches && m.coaches.length > 0 && (
                  <CoachInfoList coaches={m.coaches} />
                )}
              </Stack>
            ))}
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
