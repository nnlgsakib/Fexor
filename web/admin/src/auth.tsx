import { Alert, Anchor, Box, Button, Paper, PasswordInput, PinInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, OFFLINE, setCsrf } from "./api";
import type { Me } from "./types";

const AuthContext = createContext<{ me: Me; refresh: () => void; logout: () => Promise<void> } | null>(null);

export function useMe() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useMe outside AuthProvider");
  return ctx;
}

function takeExpiredFlag(): boolean {
  try {
    const v = sessionStorage.getItem("fexor:expired") === "1";
    sessionStorage.removeItem("fexor:expired");
    return v;
  } catch {
    return false;
  }
}

function Centered({ children, width = 400 }: { children: ReactNode; width?: number }) {
  return (
    <Box mih="100vh" display="grid" style={{ placeItems: "center", background: "var(--mantine-color-gray-0)" }} p="md">
      <Paper withBorder p="xl" w={width} maw="100%">{children}</Paper>
    </Box>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        const me = await api<Me>("/auth/me");
        setCsrf(me.csrf);
        return me;
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return null;  // not signed in
        throw e;  // server unreachable: shown below, not as a login page
      }
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
    enabled: q.data === null,
    retry: false,
  });

  if (q.isPending || (q.data === null && setup.isPending)) return null;
  if (q.isError || setup.isError) {
    return (
      <Centered>
        <Stack>
          <Title order={2} fz={22}>Can't reach the server</Title>
          <Text size="sm" c="dimmed">{OFFLINE} If you run the server yourself, make sure it is started.</Text>
          <Button onClick={() => { q.refetch(); setup.refetch(); }}>Try again</Button>
        </Stack>
      </Centered>
    );
  }
  const done = () => { qc.clear(); q.refetch(); };
  if (!q.data && setup.data?.needs_setup) {
    return <FirstRun codeRequired={setup.data.code_required} onDone={done} onAlreadyDone={() => setup.refetch()} />;
  }
  if (!q.data) return <Login onDone={done} />;

  const logout = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // A fresh page load: nothing of this admin's data stays in memory, and the sign-in page
    // shows reliably (clearing the query cache under a mounted page left it on screen).
    window.location.assign("/");
  };
  return <AuthContext.Provider value={{ me: q.data, refresh: () => q.refetch(), logout }}>{children}</AuthContext.Provider>;
}

function FirstRun({ codeRequired, onDone, onAlreadyDone }: { codeRequired: boolean; onDone: () => void; onAlreadyDone: () => void }) {
  const fromUrl = new URLSearchParams(window.location.search).get("setup") ?? "";
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "", code: fromUrl });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.currentTarget.value });
  const mismatch = form.confirm.length > 0 && form.password !== form.confirm;
  const short = form.password.length > 0 && form.password.length < 10;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || mismatch || form.password.length < 10) return;
    setBusy(true);
    setError("");
    try {
      await api("/setup", { json: { name: form.name, email: form.email, password: form.password, code: form.code } });
      window.history.replaceState(null, "", "/");
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) { onAlreadyDone(); return; }  // someone finished setup already
      setError(err instanceof Error ? err.message : OFFLINE);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centered width={460}>
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
            value={form.password} onChange={set("password")} error={short ? "Use at least 10 characters" : undefined} />
          <PasswordInput label="Repeat password" autoComplete="new-password" required value={form.confirm} onChange={set("confirm")}
            error={mismatch ? "The two passwords are different" : undefined} />
          {codeRequired && (
            <TextInput label="Setup code" description="Shown in the window where you started the server."
              required value={form.code} onChange={set("code")} />
          )}
          <Button type="submit" loading={busy} fullWidth disabled={form.password.length < 10 || mismatch}>Create account</Button>
        </Stack>
      </form>
    </Centered>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [expired] = useState(takeExpiredFlag);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfa, setMfa] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);  // the code auto-submits; Enter must not send it twice

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (sending.current) return;
    sending.current = true;
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
      setError(err instanceof Error ? err.message : OFFLINE);
      if (mfa) setCode("");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Centered>
      <form onSubmit={submit}>
        <Stack>
          <div>
            <Title order={2} fz={24}>Sign in to Fexor</Title>
            <Text c="dimmed" size="sm">Attendance administration</Text>
          </div>
          {expired && !error && <Alert color="blue" variant="light">Your session ended. Please sign in again.</Alert>}
          {error && <Alert color="red" variant="light">{error}</Alert>}
          {!mfa ? (
            <>
              <TextInput label="Email" type="email" autoComplete="username" required value={email}
                onChange={(e) => setEmail(e.currentTarget.value)} autoFocus />
              <PasswordInput label="Password" autoComplete="current-password" required value={password}
                onChange={(e) => setPassword(e.currentTarget.value)} />
              <Text size="xs" c="dimmed">Forgot your password? Ask a super admin to set a new one for you.</Text>
            </>
          ) : (
            <Stack gap="xs">
              <Text size="sm">Enter the 6-digit code from your authenticator app.</Text>
              <PinInput length={6} type="number" oneTimeCode autoFocus value={code} onChange={setCode}
                aria-label="6-digit code" onComplete={() => setTimeout(() => submit(), 0)} />
              <Text size="xs" c="dimmed">Lost your phone? A super admin can turn off two-step sign-in for your account.</Text>
            </Stack>
          )}
          <Button type="submit" loading={busy} fullWidth disabled={mfa && code.length !== 6}>{mfa ? "Verify code" : "Sign in"}</Button>
          {mfa && (
            <Anchor component="button" type="button" size="sm" onClick={() => { setMfa(false); setCode(""); setError(""); }}>
              Back
            </Anchor>
          )}
        </Stack>
      </form>
    </Centered>
  );
}
