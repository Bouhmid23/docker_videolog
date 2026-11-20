import { Center, Loader } from "@mantine/core";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import type { MemberRole } from "../types";

export function RequireAuth() {
  const { firebaseUser, authLoading, me, meLoading } = useAuth();
  const location = useLocation();

  if (authLoading || (firebaseUser && meLoading)) {
    return (
      <Center h="100vh">
        <Loader size="md" />
      </Center>
    );
  }

  if (!firebaseUser) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (!firebaseUser.emailVerified && location.pathname !== "/verify-email") {
    return <Navigate to="/verify-email" replace />;
  }

  if (
    firebaseUser.emailVerified &&
    me?.needs_onboarding &&
    location.pathname !== "/onboarding"
  ) {
    return <Navigate to="/onboarding" replace />;
  }

  return <Outlet />;
}

export function RequireRole({
  roles,
  children,
}: {
  roles: MemberRole[];
  children: JSX.Element;
}) {
  const { activeMembership, me } = useAuth();
  if (me?.platform_admin) return children;
  if (!activeMembership || !roles.includes(activeMembership.role)) {
    return <Navigate to="/app" replace />;
  }
  return children;
}
