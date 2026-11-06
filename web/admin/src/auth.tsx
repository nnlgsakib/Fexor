import { Alert, Box, Button, Paper, PasswordInput, PinInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, setCsrf } from "./api";
import type { Me } from "./types";

const AuthContext = createContext<{ me: Me; refresh: () => void; logout: () => Promise<void> } | null>(null);

export function useMe() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useMe outside AuthProvider");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await api<Me>("/auth/me");
      setCsrf(me.csrf);
      return me;
    },
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    const onLogout = () => qc.setQueryData(["me"], null);
    window.addEventListener("fexor:logout", onLogout);
    return () => window.removeEventListener("fexor:logout", onLogout);
  }, [qc]);

  if (q.isLoading) return null;
  if (!q.data) return <Login onDone={() => { qc.clear(); q.refetch(); }} />;

  const logout = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    qc.setQueryData(["me"], null);
  };
  return <AuthContext.Provider value={{ me: q.data, refresh: () => q.refetch(), logout }}>{children}</AuthContext.Provider>;
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfa, setMfa] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!mfa) {
        const r = await api<{ mfa_required: boolean }>("/auth/login", { json: { email, password } });
        if (r.mfa_required) { setMfa(true); return; }
      } else {
        await api("/auth/mfa", { json: { code } });
      }
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box mih="100vh" display="grid" style={{ placeItems: "center", background: "var(--mantine-color-gray-0)" }} p="md">
      <Paper withBorder p="xl" w={400} maw="100%">
        <form onSubmit={submit}>
          <Stack>
            <div>
              <Title order={2} fz={24}>Sign in to Fexor</Title>
              <Text c="dimmed" size="sm">Attendance administration</Text>
            </div>
            {error && <Alert color="red" variant="light">{error}</Alert>}
            {!mfa ? (
              <>
                <TextInput label="Email" type="email" autoComplete="username" required value={email}
                  onChange={(e) => setEmail(e.currentTarget.value)} autoFocus />
                <PasswordInput label="Password" autoComplete="current-password" required value={password}
                  onChange={(e) => setPassword(e.currentTarget.value)} />
              </>
            ) : (
              <Stack gap="xs">
                <Text size="sm">Enter the 6-digit code from your authenticator app.</Text>
                <PinInput length={6} type="number" oneTimeCode autoFocus value={code} onChange={setCode}
                  onComplete={() => setTimeout(() => submit(), 0)} />
              </Stack>
            )}
            <Button type="submit" loading={busy} fullWidth>{mfa ? "Verify code" : "Sign in"}</Button>
          </Stack>
        </form>
      </Paper>
    </Box>
  );
}
