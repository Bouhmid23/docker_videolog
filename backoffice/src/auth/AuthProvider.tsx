import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { onIdTokenChanged, signOut, type User } from "firebase/auth";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { auth } from "../lib/firebase";
import { apiFetch } from "../lib/api";
import type { Membership, MeResponse } from "../types";

interface AuthContextValue {
  firebaseUser: User | null;
  authLoading: boolean;
  me: MeResponse | null;
  meLoading: boolean;
  activeMembership: Membership | null;
  setActiveOrgId: (orgId: string) => void;
  refreshMe: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const ACTIVE_ORG_STORAGE_KEY = "videolog_active_org_id";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [firebaseUser, setFirebaseUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(() =>
    localStorage.getItem(ACTIVE_ORG_STORAGE_KEY)
  );
  const queryClient = useQueryClient();

  useEffect(() => {
    const unsub = onIdTokenChanged(auth, (user) => {
      setFirebaseUser(user);
      setAuthLoading(false);
      if (!user) {
        queryClient.clear();
      }
    });
    return unsub;
  }, [queryClient]);

  const {
    data: me = null,
    isLoading: meLoading,
    refetch,
  } = useQuery<MeResponse>({
    queryKey: ["me", firebaseUser?.uid],
    queryFn: () => apiFetch<MeResponse>("/v1/me"),
    enabled: Boolean(firebaseUser),
    retry: false,
  });

  const activeMembership =
    me?.memberships.find((m) => m.org_id === selectedOrgId) ??
    me?.memberships[0] ??
    null;

  const setActiveOrgId = useCallback((orgId: string) => {
    localStorage.setItem(ACTIVE_ORG_STORAGE_KEY, orgId);
    setSelectedOrgId(orgId);
  }, []);

  const refreshMe = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["me"] });
    await refetch();
  }, [queryClient, refetch]);

  const logout = useCallback(async () => {
    localStorage.removeItem(ACTIVE_ORG_STORAGE_KEY);
    await signOut(auth);
    queryClient.clear();
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{
        firebaseUser,
        authLoading,
        me,
        meLoading,
        activeMembership,
        setActiveOrgId,
        refreshMe,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth doit être utilisé dans AuthProvider");
  }
  return ctx;
}
