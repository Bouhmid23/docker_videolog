import { useState } from "react";
import {
  Alert,
  Button,
  Container,
  Group,
  MultiSelect,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { SPORTS } from "../../lib/constants";
import { auth } from "../../lib/firebase";
import type { OrgType } from "../../types";

export function OnboardingPage() {
  const { me, refreshMe } = useAuth();
  const navigate = useNavigate();

  const [accountType, setAccountType] = useState<OrgType>(() => {
    const saved = localStorage.getItem("videolog_signup_type");
    return saved === "solo_coach" ? "solo_coach" : "club";
  });
  const [organizationName, setOrganizationName] = useState("");
  const [sports, setSports] = useState<string[]>([]);
  const [city, setCity] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (me && !me.needs_onboarding) {
    return <Navigate to="/app" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const submitPayload = {
      account_type: accountType,
      organization_name: organizationName.trim(),
      sports,
      city: city.trim() || null,
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      phone: phone.trim() || null,
      age_confirmed: true,
    };
    const postOnboarding = () =>
      apiFetch("/v1/me/onboarding", {
        method: "POST",
        body: JSON.stringify(submitPayload),
      });

    try {
      try {
        await postOnboarding();
      } catch (err) {
        if (err instanceof ApiError && err.status === 403 && auth.currentUser) {
          // Force le renouvellement du JWT Firebase si l'e-mail vient d'être vérifié
          await auth.currentUser.reload();
          await auth.currentUser.getIdToken(true);
          await postOnboarding();
        } else {
          throw err;
        }
      }
      localStorage.removeItem("videolog_signup_type");
      await refreshMe();
      navigate("/app", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Impossible de finaliser la création."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container size={520} my={50}>
      <Title ta="center" fw={700}>
        Configurer votre organisation
      </Title>
      <Text c="dimmed" size="sm" ta="center" mt={4}>
        Dernière étape avant d'accéder à votre back-office
      </Text>

      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        <form onSubmit={handleSubmit}>
          <Stack>
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}

            <SegmentedControl
              fullWidth
              value={accountType}
              onChange={(v) => setAccountType(v as OrgType)}
              data={[
                { label: "Club sportif", value: "club" },
                { label: "Coach privé", value: "solo_coach" },
              ]}
            />

            <TextInput
              label={
                accountType === "club" ? "Nom du club" : "Nom de votre activité / structure"
              }
              placeholder={
                accountType === "club" ? "Ex : Vélo Club Lyon" : "Ex : Coaching Marc Lenoir"
              }
              required
              value={organizationName}
              onChange={(e) => setOrganizationName(e.currentTarget.value)}
            />

            <MultiSelect
              label="Sports pratiqués"
              placeholder="Sélectionnez un ou plusieurs sports"
              data={SPORTS.map((s) => ({ value: s.value, label: s.label }))}
              value={sports}
              onChange={setSports}
            />

            <TextInput
              label="Ville"
              placeholder="Lyon"
              value={city}
              onChange={(e) => setCity(e.currentTarget.value)}
            />

            <Group grow>
              <TextInput
                label="Votre prénom"
                required
                value={firstName}
                onChange={(e) => setFirstName(e.currentTarget.value)}
              />
              <TextInput
                label="Votre nom"
                required
                value={lastName}
                onChange={(e) => setLastName(e.currentTarget.value)}
              />
            </Group>

            <TextInput
              label="Téléphone (optionnel)"
              value={phone}
              onChange={(e) => setPhone(e.currentTarget.value)}
            />

            <Button type="submit" fullWidth loading={loading} mt="sm">
              Accéder au back-office
            </Button>
          </Stack>
        </form>
      </Paper>
    </Container>
  );
}
