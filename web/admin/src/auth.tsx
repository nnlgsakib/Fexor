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

  const setup = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api<{ needs_setup: boolean; code_required: boolean }>("/setup/status"),
    enabled: !q.isLoading && !q.data,
    retry: false,
  });

  if (q.isLoading || (!q.data && setup.isLoading)) return null;
  if (!q.data && setup.data?.needs_setup) {
    return <FirstRun codeRequired={setup.data.code_required} onDone={() => { qc.clear(); q.refetch(); }} />;
  }
  if (!q.data) return <Login onDone={() => { qc.clear(); q.refetch(); }} />;

  const logout = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    qc.setQueryData(["me"], null);
  };
  return <AuthContext.Provider value={{ me: q.data, refresh: () => q.refetch(), logout }}>{children}</AuthContext.Provider>;
}

function FirstRun({ codeRequired, onDone }: { codeRequired: boolean; onDone: () => void }) {
  const fromUrl = new URLSearchParams(window.location.search).get("setup") ?? "";
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "", code: fromUrl });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.currentTarget.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.password !== form.confirm) { setError("The two passwords are different."); return; }
    setBusy(true);
    setError("");
    try {
      await api("/setup", { json: { name: form.name, email: form.email, password: form.password, code: form.code } });
      window.history.replaceState(null, "", "/");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box mih="100vh" display="grid" style={{ placeItems: "center", background: "var(--mantine-color-gray-0)" }} p="md">
      <Paper withBorder p="xl" w={460} maw="100%">
        <form onSubmit={submit}>
          <Stack>
            <div>
              <Title order={2} fz={24}>Welcome to Fexor</Title>
              <Text c="dimmed" size="sm">Create the main admin account. You can add more admins later.</Text>
            </div>
            {error && <Alert color="red" variant="light">{error}</Alert>}
            <TextInput label="Your name" required value={form.name} onChange={set("name")} autoFocus />
            <TextInput label="Email" type="email" autoComplete="username" required value={form.email} onChange={set("email")} />
            <PasswordInput label="Password" description="At least 10 characters" autoComplete="new-password" required
              value={form.password} onChange={set("password")} />
            <PasswordInput label="Repeat password" autoComplete="new-password" required value={form.confirm} onChange={set("confirm")} />
            {codeRequired && (
              <TextInput label="Setup code" description="Shown in the window where you started the server."
                required value={form.code} onChange={set("code")} />
            )}
            <Button type="submit" loading={busy} fullWidth disabled={form.password.length < 10}>Create account</Button>
          </Stack>
        </form>
      </Paper>
    </Box>
  );
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
