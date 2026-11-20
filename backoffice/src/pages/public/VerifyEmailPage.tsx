import { useEffect, useState } from "react";
import { Alert, Button, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { sendEmailVerification } from "firebase/auth";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";

export function VerifyEmailPage() {
  const { firebaseUser, refreshMe, logout } = useAuth();
  const navigate = useNavigate();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!firebaseUser) {
      navigate("/login", { replace: true });
      return;
    }
    if (firebaseUser.emailVerified) {
      void firebaseUser.getIdToken(true).then(() => refreshMe()).finally(() => {
        navigate("/onboarding", { replace: true });
      });
      return;
    }

    const timer = window.setInterval(async () => {
      await firebaseUser.reload();
      if (firebaseUser.emailVerified) {
        await firebaseUser.getIdToken(true);
        await refreshMe();
        navigate("/onboarding", { replace: true });
      }
    }, 3000);

    return () => window.clearInterval(timer);
  }, [firebaseUser, navigate, refreshMe]);

  const handleResend = async () => {
    if (!firebaseUser) return;
    setError(null);
    setMessage(null);
    setSending(true);
    try {
      await sendEmailVerification(firebaseUser);
      setMessage("Un nouvel e-mail de vérification a été envoyé.");
    } catch {
      setError("Veuillez patienter quelques instants avant de renvoyer l'e-mail.");
    } finally {
      setSending(false);
    }
  };

  const handleCheckNow = async () => {
    if (!firebaseUser) return;
    await firebaseUser.reload();
    if (firebaseUser.emailVerified) {
      await firebaseUser.getIdToken(true);
      await refreshMe();
      navigate("/onboarding", { replace: true });
    } else {
      setError("L'adresse e-mail n'est pas encore vérifiée.");
    }
  };

  return (
    <Container size={460} my={60}>
      <Title ta="center" fw={700}>
        Vérifiez votre adresse e-mail
      </Title>
      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        <Stack>
          <Text size="sm">
            Un lien de confirmation a été envoyé à <strong>{firebaseUser?.email}</strong>. Cliquez
            dessus puis revenez ici.
          </Text>
          {message && (
            <Alert color="green" variant="light">
              {message}
            </Alert>
          )}
          {error && (
            <Alert color="orange" variant="light">
              {error}
            </Alert>
          )}
          <Group grow>
            <Button onClick={handleCheckNow}>J'ai vérifié mon e-mail</Button>
            <Button variant="light" onClick={handleResend} loading={sending}>
              Renvoyer le lien
            </Button>
          </Group>
          <Button variant="subtle" color="gray" onClick={logout}>
            Se déconnecter
          </Button>
        </Stack>
      </Paper>
    </Container>
  );
}
