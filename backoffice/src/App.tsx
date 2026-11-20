import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./styles/videolog.css";
import { Suspense, lazy } from "react";
import { Center, Loader, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { videologTheme } from "./theme";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth, RequireRole } from "./auth/Guards";
import { AppLayout } from "./components/AppLayout";
import { LoginPage } from "./pages/public/LoginPage";
import { SignupPage } from "./pages/public/SignupPage";
import { ForgotPasswordPage } from "./pages/public/ForgotPasswordPage";
import { VerifyEmailPage } from "./pages/public/VerifyEmailPage";
import { InvitePage } from "./pages/public/InvitePage";
import { OnboardingPage } from "./pages/onboarding/OnboardingPage";
import { DashboardPage } from "./pages/app/DashboardPage";
import { MembersPage } from "./pages/app/MembersPage";
import { InvitationsPage } from "./pages/app/InvitationsPage";
import { GroupsPage } from "./pages/app/GroupsPage";
import { AthleteDetailPage } from "./pages/app/AthleteDetailPage";
import { SessionsPage } from "./pages/app/SessionsPage";
import { ActivitiesPage } from "./pages/app/ActivitiesPage";
import { WorkoutsLibraryPage } from "./pages/app/WorkoutsLibraryPage";
import { TrainingPlansPage } from "./pages/app/TrainingPlansPage";
import { OrgSettingsPage } from "./pages/app/OrgSettingsPage";
import { MyProfilePage } from "./pages/app/MyProfilePage";

const LiveRoomPage = lazy(() =>
  import("./components/live/components/LiveRoomPage").then((m) => ({
    default: m.LiveRoomPage,
  }))
);
const SessionDetailPage = lazy(() =>
  import("./pages/app/SessionDetailPage").then((m) => ({
    default: m.SessionDetailPage,
  }))
);
const ActivityDetailPage = lazy(() =>
  import("./pages/app/ActivityDetailPage").then((m) => ({
    default: m.ActivityDetailPage,
  }))
);
const CalendarPage = lazy(() =>
  import("./pages/app/CalendarPage").then((m) => ({
    default: m.CalendarPage,
  }))
);
const PerformancePage = lazy(() =>
  import("./pages/app/PerformancePage").then((m) => ({
    default: m.PerformancePage,
  }))
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000 },
  },
});

function LegacyLiveRedirect() {
  const [params] = useSearchParams();
  const room = params.get("room");
  const rest = new URLSearchParams(params);
  rest.delete("room");
  const qs = rest.toString() ? `?${rest.toString()}` : "";
  const target = room
    ? `/app/live/${encodeURIComponent(room)}${qs}`
    : `/app/live${qs}`;
  return <Navigate to={target} replace />;
}

export default function App() {
  return (
    <MantineProvider theme={videologTheme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Suspense
              fallback={
                <Center py="xl">
                  <Loader />
                </Center>
              }
            >
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route path="/signup" element={<SignupPage />} />
                <Route path="/forgot-password" element={<ForgotPasswordPage />} />
                <Route path="/invite/:token" element={<InvitePage />} />

                <Route element={<RequireAuth />}>
                  <Route path="/verify-email" element={<VerifyEmailPage />} />
                  <Route path="/onboarding" element={<OnboardingPage />} />
                  <Route path="/app/live" element={<LiveRoomPage />} />
                  <Route path="/app/live/:roomName" element={<LiveRoomPage />} />
                  <Route path="/live/*" element={<LegacyLiveRedirect />} />
                  <Route path="/app" element={<AppLayout />}>
                    <Route index element={<DashboardPage />} />
                    <Route
                      path="members"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <MembersPage />
                        </RequireRole>
                      }
                    />
                    <Route
                      path="invitations"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <InvitationsPage />
                        </RequireRole>
                      }
                    />
                    <Route
                      path="groups"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <GroupsPage />
                        </RequireRole>
                      }
                    />
                    <Route
                      path="athletes/:uid"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <AthleteDetailPage />
                        </RequireRole>
                      }
                    />
                    <Route path="sessions" element={<SessionsPage />} />
                    <Route
                      path="sessions/:roomName"
                      element={<SessionDetailPage />}
                    />
                    <Route path="activities" element={<ActivitiesPage />} />
                    <Route
                      path="activities/:activityId"
                      element={<ActivityDetailPage />}
                    />
                    <Route path="calendar" element={<CalendarPage />} />
                    <Route
                      path="workouts"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <WorkoutsLibraryPage />
                        </RequireRole>
                      }
                    />
                    <Route
                      path="plans"
                      element={
                        <RequireRole roles={["owner", "admin", "coach"]}>
                          <TrainingPlansPage />
                        </RequireRole>
                      }
                    />
                    <Route path="performance" element={<PerformancePage />} />
                    <Route
                      path="settings"
                      element={
                        <RequireRole roles={["owner", "admin"]}>
                          <OrgSettingsPage />
                        </RequireRole>
                      }
                    />
                    <Route path="profile" element={<MyProfilePage />} />
                  </Route>
                </Route>

                <Route path="*" element={<Navigate to="/app" replace />} />
              </Routes>
            </Suspense>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}
