import { useState } from "react";
import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Container,
  Divider,
  Group,
  Image,
  Paper,
  PasswordInput,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  createUserWithEmailAndPassword,
  sendEmailVerification,
  signInWithPopup,
} from "firebase/auth";
import { Link, useNavigate } from "react-router-dom";
import { auth, googleProvider } from "../../lib/firebase";
import type { OrgType } from "../../types";

export function SignupPage() {
  const navigate = useNavigate();
  const [accountType, setAccountType] = useState<OrgType>("club");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [cguAccepted, setCguAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const validateChecks = () => {
    if (!ageConfirmed) {
      setError("L'inscription est réservée aux personnes âgées de 15 ans et plus.");
      return false;
    }
    if (!cguAccepted) {
      setError("Veuillez accepter les conditions générales d'utilisation.");
      return false;
    }
    return true;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!validateChecks()) return;
    if (password.length < 12) {
      setError("Le mot de passe doit contenir au moins 12 caractères.");
      return;
    }

    setLoading(true);
    try {
      localStorage.setItem("videolog_signup_type", accountType);
      const cred = await createUserWithEmailAndPassword(auth, email.trim(), password);
      await sendEmailVerification(cred.user);
      navigate("/verify-email", { replace: true });
    } catch (err: unknown) {
      const code = (err as { code?: string })?.code;
      if (code === "auth/email-already-in-use") {
        setError("Un compte existe déjà avec cette adresse e-mail.");
      } else if (code === "auth/weak-password") {
        setError("Mot de passe trop faible (12 caractères minimum).");
      } else {
        setError("Impossible de créer le compte.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    setError(null);
    if (!validateChecks()) return;
    setLoading(true);
    try {
      localStorage.setItem("videolog_signup_type", accountType);
      await signInWithPopup(auth, googleProvider);
      navigate("/onboarding", { replace: true });
    } catch {
      setError("La connexion Google a échoué.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container size={460} my={50}>
      <Group justify="center" gap="sm" mb={4}>
        <Image src="/logo.png" alt="VideoLog" h={36} w="auto" />
        <Title className="vl-display" order={1} fz={28} c="#eef2f6">
          CRÉER UN ESPACE
        </Title>
      </Group>
      <Text c="#98a3b0" size="sm" ta="center" mt={4}>
        Inscription réservée aux clubs sportifs et coachs privés (athlètes sur invitation)
      </Text>

      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <div>
            <Text size="sm" fw={500} mb={6}>
              Type de structure
            </Text>
            <SegmentedControl
              fullWidth
              value={accountType}
              onChange={(v) => setAccountType(v as OrgType)}
              data={[
                { label: "Club sportif", value: "club" },
                { label: "Coach privé", value: "solo_coach" },
              ]}
            />
          </div>

          <Checkbox
            label="Je certifie avoir au moins 15 ans révolus"
            checked={ageConfirmed}
            onChange={(e) => setAgeConfirmed(e.currentTarget.checked)}
          />
          <Checkbox
            label="J'accepte les conditions générales d'utilisation et la politique de confidentialité"
            checked={cguAccepted}
            onChange={(e) => setCguAccepted(e.currentTarget.checked)}
          />

          <Button variant="default" fullWidth onClick={handleGoogle} loading={loading}>
            S'inscrire avec Google
          </Button>

          <Divider label="ou par e-mail" labelPosition="center" />

          <form onSubmit={handleSubmit}>
            <Stack>
              <TextInput
                label="Adresse e-mail"
                placeholder="contact@club.fr"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
              />
              <PasswordInput
                label="Mot de passe (12 caractères min.)"
                required
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
              />
              <Button type="submit" fullWidth loading={loading}>
                Créer mon compte
              </Button>
            </Stack>
          </form>
        </Stack>
      </Paper>

      <Text c="dimmed" size="sm" ta="center" mt="md">
        Déjà inscrit ?{" "}
        <Anchor component={Link} to="/login" fw={500}>
          Se connecter
        </Anchor>
      </Text>
    </Container>
  );
}
