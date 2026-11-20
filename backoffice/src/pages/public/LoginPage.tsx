import { useState } from "react";
import {
  Alert,
  Anchor,
  Button,
  Container,
  Divider,
  Group,
  Image,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { signInWithEmailAndPassword, signInWithPopup } from "firebase/auth";
import { Link, useNavigate } from "react-router-dom";
import { auth, googleProvider } from "../../lib/firebase";

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await signInWithEmailAndPassword(auth, email.trim(), password);
      navigate("/app", { replace: true });
    } catch {
      setError("Identifiants incorrects ou compte introuvable.");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    setError(null);
    setLoading(true);
    try {
      await signInWithPopup(auth, googleProvider);
      navigate("/app", { replace: true });
    } catch {
      setError("La connexion Google a échoué ou a été annulée.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container size={420} my={60}>
      <Group justify="center" gap="sm" mb={4}>
        <Image src="/logo.png" alt="VideoLog" h={38} w="auto" />
        <Title className="vl-display" order={1} fz={32} c="#eef2f6">
          VIDEOLOG
        </Title>
      </Group>
      <Text c="#98a3b0" size="sm" ta="center" mt={4}>
        Espace clubs, coachs et athlètes
      </Text>

      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <Button variant="default" fullWidth onClick={handleGoogle} loading={loading}>
            Continuer avec Google
          </Button>

          <Divider label="ou par e-mail" labelPosition="center" />

          <form onSubmit={handleSubmit}>
            <Stack>
              <TextInput
                label="Adresse e-mail"
                placeholder="coach@club.fr"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
              />
              <PasswordInput
                label="Mot de passe"
                required
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
              />
              <Group justify="flex-end">
                <Anchor component={Link} to="/forgot-password" size="xs">
                  Mot de passe oublié ?
                </Anchor>
              </Group>
              <Button type="submit" fullWidth loading={loading}>
                Se connecter
              </Button>
            </Stack>
          </form>
        </Stack>
      </Paper>

      <Text c="dimmed" size="sm" ta="center" mt="md">
        Vous représentez un club ou êtes coach privé ?{" "}
        <Anchor component={Link} to="/signup" fw={500}>
          Créer un compte
        </Anchor>
      </Text>
    </Container>
  );
}
