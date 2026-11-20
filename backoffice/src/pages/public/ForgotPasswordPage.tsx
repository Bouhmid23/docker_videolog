import { useState } from "react";
import {
  Alert,
  Anchor,
  Button,
  Container,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { sendPasswordResetEmail } from "firebase/auth";
import { Link } from "react-router-dom";
import { auth } from "../../lib/firebase";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await sendPasswordResetEmail(auth, email.trim());
      setSent(true);
    } catch {
      setError("Impossible d'envoyer l'e-mail de réinitialisation.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container size={420} my={60}>
      <Title ta="center" fw={700}>
        Mot de passe oublié
      </Title>
      <Text c="dimmed" size="sm" ta="center" mt={4}>
        Recevez un lien pour choisir un nouveau mot de passe
      </Text>

      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        {sent ? (
          <Stack>
            <Alert color="green" variant="light">
              Si un compte correspond à {email}, un e-mail de réinitialisation vient d'être envoyé.
            </Alert>
            <Button component={Link} to="/login" fullWidth>
              Retour à la connexion
            </Button>
          </Stack>
        ) : (
          <form onSubmit={handleSubmit}>
            <Stack>
              {error && (
                <Alert color="red" variant="light">
                  {error}
                </Alert>
              )}
              <TextInput
                label="Adresse e-mail"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
              />
              <Button type="submit" fullWidth loading={loading}>
                Envoyer le lien
              </Button>
              <Anchor component={Link} to="/login" size="sm" ta="center">
                Retour à la connexion
              </Anchor>
            </Stack>
          </form>
        )}
      </Paper>
    </Container>
  );
}
