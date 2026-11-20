import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Center,
  Checkbox,
  Container,
  Divider,
  Group,
  Loader,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signInWithPopup,
} from "firebase/auth";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError, apiFetch } from "../../lib/api";
import { ROLE_LABELS } from "../../lib/constants";
import { auth, googleProvider } from "../../lib/firebase";

interface InvitationPreview {
  org_name: string;
  org_type: string;
  role: string;
  masked_email: string;
  expires_at: string;
}

export function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const { firebaseUser, refreshMe, logout } = useAuth();

  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [mode, setMode] = useState<"register" | "login">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) return;
    setLoadingPreview(true);
    apiFetch<InvitationPreview>(`/v1/invitations/${token}`, { skipAuth: true })
      .then((data) => {
        setPreview(data);
        setPreviewError(null);
      })
      .catch((err) => {
        setPreviewError(
          err instanceof ApiError ? err.detail : "Invitation invalide ou expirée."
        );
      })
      .finally(() => setLoadingPreview(false));
  }, [token]);

  const acceptInvitation = async () => {
    if (!token) return;
    await apiFetch(`/v1/invitations/${token}/accept`, {
      method: "POST",
      body: JSON.stringify({
        first_name: firstName.trim() || undefined,
        last_name: lastName.trim() || undefined,
        age_confirmed: true,
      }),
    });
    await refreshMe();
    navigate("/app/profile", { replace: true });
  };

  const handleAcceptLoggedIn = async () => {
    if (!ageConfirmed) {
      setError("L'accès est réservé aux personnes âgées de 15 ans et plus.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await acceptInvitation();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Impossible d'accepter l'invitation.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!ageConfirmed) {
      setError("L'accès est réservé aux personnes âgées de 15 ans et plus.");
      return;
    }
    if (mode === "register" && password.length < 12) {
      setError("Le mot de passe doit contenir au moins 12 caractères.");
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "register") {
        await createUserWithEmailAndPassword(auth, email.trim(), password);
      } else {
        await signInWithEmailAndPassword(auth, email.trim(), password);
      }
      await acceptInvitation();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        const code = (err as { code?: string })?.code;
        if (code === "auth/email-already-in-use") {
          setError("Un compte existe déjà avec cet e-mail. Passez en mode Connexion.");
          setMode("login");
        } else {
          setError("Échec de l'authentification ou de l'acceptation.");
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleGoogle = async () => {
    if (!ageConfirmed) {
      setError("L'accès est réservé aux personnes âgées de 15 ans et plus.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await signInWithPopup(auth, googleProvider);
      await acceptInvitation();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "La connexion Google ou l'acceptation a échoué."
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loadingPreview) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }

  if (previewError || !preview) {
    return (
      <Container size={460} my={60}>
        <Paper withBorder p={30} radius="md">
          <Alert color="red" title="Invitation indisponible">
            {previewError ?? "Ce lien n'est plus valide."}
          </Alert>
        </Paper>
      </Container>
    );
  }

  return (
    <Container size={480} my={50}>
      <Title ta="center" fw={700}>
        Invitation Videolog
      </Title>
      <Text c="dimmed" size="sm" ta="center" mt={4}>
        Rejoindre <strong>{preview.org_name}</strong> en tant que{" "}
        <Badge size="sm">{ROLE_LABELS[preview.role] ?? preview.role}</Badge>
      </Text>

      <Paper withBorder shadow="sm" p={30} mt={24} radius="md">
        <Stack>
          <Text size="xs" c="dimmed">
            Adresse invitée : <strong>{preview.masked_email}</strong>
          </Text>

          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <Checkbox
            label="Je certifie avoir au moins 15 ans révolus"
            checked={ageConfirmed}
            onChange={(e) => setAgeConfirmed(e.currentTarget.checked)}
          />

          {firebaseUser ? (
            <Stack>
              <Alert color="blue" variant="light">
                Connecté en tant que <strong>{firebaseUser.email}</strong>.
              </Alert>
              <Button onClick={handleAcceptLoggedIn} loading={submitting}>
                Accepter l'invitation
              </Button>
              <Button variant="subtle" color="gray" onClick={logout}>
                Changer de compte
              </Button>
            </Stack>
          ) : (
            <>
              <Button variant="default" fullWidth onClick={handleGoogle} loading={submitting}>
                Continuer avec Google
              </Button>

              <Divider label="ou par e-mail" labelPosition="center" />

              <Group grow>
                <Button
                  variant={mode === "register" ? "filled" : "light"}
                  size="xs"
                  onClick={() => setMode("register")}
                >
                  Créer mon compte
                </Button>
                <Button
                  variant={mode === "login" ? "filled" : "light"}
                  size="xs"
                  onClick={() => setMode("login")}
                >
                  J'ai déjà un compte
                </Button>
              </Group>

              <form onSubmit={handleAuthSubmit}>
                <Stack>
                  {mode === "register" && (
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
                  )}
                  <TextInput
                    label="Adresse e-mail invitée"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.currentTarget.value)}
                  />
                  <PasswordInput
                    label={
                      mode === "register"
                        ? "Mot de passe (12 caractères min.)"
                        : "Mot de passe"
                    }
                    required
                    value={password}
                    onChange={(e) => setPassword(e.currentTarget.value)}
                  />
                  <Button type="submit" fullWidth loading={submitting}>
                    {mode === "register" ? "Créer mon compte et rejoindre" : "Me connecter et rejoindre"}
                  </Button>
                </Stack>
              </form>
            </>
          )}
        </Stack>
      </Paper>
    </Container>
  );
}
