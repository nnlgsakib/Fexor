import {
  Alert, Badge, Button, Checkbox, Code, CopyButton, Group, Image, Modal, Paper, PasswordInput, PinInput, Select, Stack,
  Table, Text, TextInput, Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useState } from "react";
import { api } from "../api";
import { useMe } from "../auth";
import type { Role, Space } from "../types";
import { PageHeader, QueryState, confirmAction, fmtDateTime, notifyError, notifyOk } from "../ui";
import { ROLE_LABEL } from "./Spaces";

interface AdminUser {
  id: string; email: string; name: string; active: boolean; is_superadmin: boolean; totp_enabled: boolean;
  last_login_at: string | null; roles: { space_id: string; role: Role }[];
}

export function Admins() {
  const { me } = useMe();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<AdminUser[]>("/users") });
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [addOpen, addCtl] = useDisclosure();
  const [form, setForm] = useState({ email: "", name: "", password: "", is_superadmin: false });
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [roles, setRoles] = useState<Record<string, Role | "">>({});
  const [resetting, setResetting] = useState<AdminUser | null>(null);
  const [newPw, setNewPw] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const spaceName = (id: string) => spaces.data?.find((s) => s.id === id)?.name ?? "removed space";

  const openRoles = (u: AdminUser) => { setEditing(u); setRoles(Object.fromEntries(u.roles.map((r) => [r.space_id, r.role]))); };
  const create = useMutation({
    mutationFn: () => api<AdminUser>("/users", { json: { ...form, email: form.email.trim(), name: form.name.trim() } }),
    onSuccess: (u) => {
      notifyOk("Admin added");
      addCtl.close();
      setForm({ email: "", name: "", password: "", is_superadmin: false });
      refresh();
      if (!u.is_superadmin) openRoles(u);  // without spaces they can't see anything yet
    },
    onError: notifyError,
  });
  const saveRoles = useMutation({
    mutationFn: () => api(`/users/${editing!.id}/roles`, { method: "PUT", json: Object.entries(roles).filter(([, r]) => r).map(([space_id, role]) => ({ space_id, role })) }),
    onSuccess: () => { notifyOk("Access updated"); setEditing(null); refresh(); },
    onError: notifyError,
  });
  const resetPw = useMutation({
    mutationFn: () => api(`/users/${resetting!.id}`, { method: "PATCH", json: { password: newPw } }),
    onSuccess: () => { notifyOk("Password set. They were signed out everywhere."); setResetting(null); setNewPw(""); refresh(); },
    onError: notifyError,
  });
  const patch = async (u: AdminUser, body: object, msg: string) => {
    try { await api(`/users/${u.id}`, { method: "PATCH", json: body }); notifyOk(msg); refresh(); } catch (e) { notifyError(e); }
  };

  return (
    <>
      <PageHeader title="Admins" description="Super admins manage everything. Other admins see only the spaces you give them."
        actions={<Button onClick={addCtl.open}>Add admin</Button>} />
      <QueryState q={users}>
        <Paper withBorder>
          <Table.ScrollContainer minWidth={820}>
            <Table verticalSpacing="sm">
              <Table.Thead><Table.Tr><Table.Th>Name</Table.Th><Table.Th>Access</Table.Th><Table.Th>Two-step sign-in</Table.Th><Table.Th>Last sign-in</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>
                {users.data?.map((u) => (
                  <Table.Tr key={u.id} opacity={u.active ? 1 : 0.5}>
                    <Table.Td><Text fw={500}>{u.name}{u.id === me.id && <Text span c="dimmed" size="sm"> (you)</Text>}</Text><Text size="xs" c="dimmed">{u.email}</Text></Table.Td>
                    <Table.Td>
                      {u.is_superadmin ? <Badge tt="none">Super admin</Badge> : u.roles.length === 0 ? <Text size="sm" c="orange.8">No spaces yet</Text>
                        : <Text size="sm">{u.roles.map((r) => `${spaceName(r.space_id)} (${ROLE_LABEL[r.role]})`).join(", ")}</Text>}
                    </Table.Td>
                    <Table.Td>{u.totp_enabled ? "On" : <Text span c="orange.8" size="sm">Off</Text>}</Table.Td>
                    <Table.Td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(u.last_login_at)}</Table.Td>
                    <Table.Td ta="right">
                      <Group gap={6} justify="flex-end" wrap="nowrap">
                        {!u.is_superadmin && <Button size="xs" variant="default" onClick={() => openRoles(u)}>Spaces</Button>}
                        {u.id !== me.id && <Button size="xs" variant="subtle" onClick={() => setResetting(u)}>Set password</Button>}
                        {u.totp_enabled && u.id !== me.id && (
                          <Button size="xs" variant="subtle" onClick={() => confirmAction({
                            title: `Turn off two-step sign-in for ${u.name}?`,
                            text: "Use this when they lost their phone. They are signed out and can sign in with just their password, then set it up again.",
                            confirm: "Turn off", onConfirm: () => patch(u, { reset_2fa: true }, "Two-step sign-in turned off"),
                          })}>Reset 2FA</Button>
                        )}
                        {u.id !== me.id && (u.active ? (
                          <Button size="xs" variant="subtle" color="red" onClick={() => confirmAction({
                            title: `Deactivate ${u.name}?`, text: "They are signed out at once and can't sign in again until reactivated.",
                            confirm: "Deactivate", onConfirm: () => patch(u, { active: false }, "Admin deactivated"),
                          })}>Deactivate</Button>
                        ) : (
                          <Button size="xs" variant="subtle" color="teal" onClick={() => patch(u, { active: true }, "Admin reactivated")}>Reactivate</Button>
                        ))}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      </QueryState>
      <Modal opened={addOpen} onClose={addCtl.close} title="Add admin">
        <Stack>
          <TextInput label="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
          <TextInput label="Email" required type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.currentTarget.value })} />
          <PasswordInput label="Temporary password" required description="At least 10 characters. Ask them to change it after signing in."
            value={form.password} onChange={(e) => setForm({ ...form, password: e.currentTarget.value })} />
          <Checkbox label="Super admin (full access to every space and setting)" checked={form.is_superadmin}
            onChange={(e) => setForm({ ...form, is_superadmin: e.currentTarget.checked })} />
          <Button onClick={() => create.mutate()} loading={create.isPending}
            disabled={!form.email.includes("@") || !form.name.trim() || form.password.length < 10}>Add admin</Button>
        </Stack>
      </Modal>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={`Spaces for ${editing?.name}`}>
        <Stack>
          {spaces.data?.length === 0 && <Text c="dimmed" size="sm">No spaces yet. Create one first.</Text>}
          {spaces.data?.map((s) => (
            <Group key={s.id} justify="space-between" wrap="nowrap">
              <Text truncate>{s.name}</Text>
              <Select w={150} value={roles[s.id] || ""} aria-label={`Access to ${s.name}`} allowDeselect={false}
                onChange={(v) => setRoles({ ...roles, [s.id]: (v ?? "") as Role | "" })}
                data={[{ value: "", label: "No access" }, { value: "viewer", label: "Viewer" }, { value: "manager", label: "Manager" }, { value: "owner", label: "Owner" }]} />
            </Group>
          ))}
          <Text size="xs" c="dimmed">Viewers see reports. Managers also edit people, schedules and kiosks. Owners also change space settings.</Text>
          <Button onClick={() => saveRoles.mutate()} loading={saveRoles.isPending}>Save access</Button>
        </Stack>
      </Modal>
      <Modal opened={!!resetting} onClose={() => { setResetting(null); setNewPw(""); }} title={`Set a new password for ${resetting?.name}`}>
        <Stack>
          <PasswordInput label="New password" description="At least 10 characters. Give it to them in person; they are signed out everywhere."
            value={newPw} onChange={(e) => setNewPw(e.currentTarget.value)} data-autofocus />
          <Button onClick={() => resetPw.mutate()} loading={resetPw.isPending} disabled={newPw.length < 10}>Set password</Button>
        </Stack>
      </Modal>
    </>
  );
}

type Key = { id: string; name: string; prefix: string; space_id: string | null; last_used_at: string | null; revoked_at: string | null };
type Hook = { id: string; url: string; space_id: string | null; events: string[]; active: boolean };

export function Integrations() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api<Key[]>("/api-keys") });
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: () => api<Hook[]>("/webhooks") });
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keySpace, setKeySpace] = useState<string | null>(null);
  const [hookUrl, setHookUrl] = useState("");
  const [hookSpace, setHookSpace] = useState<string | null>(null);
  const spaceOptions = (spaces.data ?? []).map((s) => ({ value: s.id, label: s.name }));
  const scope = (id: string | null) => (id ? spaces.data?.find((s) => s.id === id)?.name ?? "removed space" : "All spaces");

  const addKey = useMutation({
    mutationFn: () => api<{ token: string }>("/api-keys", { json: { name: keyName.trim(), space_id: keySpace } }),
    onSuccess: (r) => { setSecret({ title: "New API key", value: r.token }); setKeyName(""); qc.invalidateQueries({ queryKey: ["keys"] }); },
    onError: notifyError,
  });
  const addHook = useMutation({
    mutationFn: () => api<{ secret: string }>("/webhooks", { json: { url: hookUrl.trim(), space_id: hookSpace } }),
    onSuccess: (r) => { setSecret({ title: "Webhook signing secret", value: r.secret }); setHookUrl(""); qc.invalidateQueries({ queryKey: ["hooks"] }); },
    onError: notifyError,
  });
  const call = async (path: string, method: string, msg: string, key: string) => {
    try { await api(path, { method }); notifyOk(msg); qc.invalidateQueries({ queryKey: [key] }); } catch (e) { notifyError(e); }
  };

  return (
    <>
      <PageHeader title="Integrations" description="Connect payroll, HR or school systems. Reports also download as Excel or CSV from the Register page." />
      <Stack gap="xl" maw={900}>
        <section>
          <Title order={3} fz={20} mb={4}>Read-only API keys</Title>
          <Text size="sm" c="dimmed" mb="sm">Send as <Code>Authorization: Bearer fxk_…</Code>. Full API reference: <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>.</Text>
          <Paper withBorder p="md">
            <Group align="flex-end" mb="md" wrap="wrap">
              <TextInput label="Name" placeholder="Payroll export" value={keyName} maxLength={200} onChange={(e) => setKeyName(e.currentTarget.value)} />
              <Select label="Limit to space" placeholder="All spaces" data={spaceOptions} value={keySpace} onChange={setKeySpace} clearable />
              <Button onClick={() => addKey.mutate()} loading={addKey.isPending} disabled={!keyName.trim()}>Create key</Button>
            </Group>
            <QueryState q={keys}>
              <Table.ScrollContainer minWidth={560}>
                <Table>
                  <Table.Tbody>
                    {keys.data?.map((k) => (
                      <Table.Tr key={k.id} opacity={k.revoked_at ? 0.5 : 1}>
                        <Table.Td>{k.name}<Text size="xs" c="dimmed">{scope(k.space_id)}</Text></Table.Td>
                        <Table.Td><Code>{k.prefix}…</Code></Table.Td>
                        <Table.Td><Text size="sm" c="dimmed">Last used {fmtDateTime(k.last_used_at)}</Text></Table.Td>
                        <Table.Td ta="right">{!k.revoked_at ? (
                          <Button size="xs" variant="subtle" color="red" onClick={() => confirmAction({
                            title: `Revoke "${k.name}"?`, text: "Anything using this key stops working at once. This can't be undone.",
                            confirm: "Revoke key", onConfirm: () => call(`/api-keys/${k.id}`, "DELETE", "Key revoked", "keys"),
                          })}>Revoke</Button>
                        ) : "Revoked"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
              {keys.data?.length === 0 && <Text size="sm" c="dimmed">No keys yet.</Text>}
            </QueryState>
          </Paper>
        </section>
        <section>
          <Title order={3} fz={20} mb={4}>Webhooks</Title>
          <Text size="sm" c="dimmed" mb="sm">Each check-in is sent to your URL as JSON within seconds, signed in the <Code>X-Fexor-Signature</Code> header (HMAC-SHA256 of <Code>timestamp.body</Code>). Failed deliveries are retried; a retry keeps the same <Code>id</Code>.</Text>
          <Paper withBorder p="md">
            <Group align="flex-end" mb="md" wrap="wrap">
              <TextInput label="URL" placeholder="https://example.com/fexor" value={hookUrl} onChange={(e) => setHookUrl(e.currentTarget.value)} style={{ flex: "1 1 260px" }} />
              <Select label="Space" placeholder="All spaces" data={spaceOptions} value={hookSpace} onChange={setHookSpace} clearable />
              <Button onClick={() => addHook.mutate()} loading={addHook.isPending} disabled={!/^https?:\/\/\S+$/.test(hookUrl.trim())}>Add webhook</Button>
            </Group>
            <QueryState q={hooks}>
              <Table>
                <Table.Tbody>
                  {hooks.data?.map((h) => (
                    <Table.Tr key={h.id}>
                      <Table.Td style={{ wordBreak: "break-all" }}>{h.url}<Text size="xs" c="dimmed">{scope(h.space_id)}</Text></Table.Td>
                      <Table.Td ta="right">
                        <Group gap={6} justify="flex-end" wrap="nowrap">
                          <Button size="xs" variant="default" onClick={() => call(`/webhooks/${h.id}/test`, "POST", "Test queued: it arrives within a few seconds", "hooks")}>Send test</Button>
                          <Button size="xs" variant="subtle" color="red" onClick={() => confirmAction({
                            title: "Remove this webhook?", text: "Check-ins stop being sent to this URL.",
                            confirm: "Remove", onConfirm: () => call(`/webhooks/${h.id}`, "DELETE", "Webhook removed", "hooks"),
                          })}>Remove</Button>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {hooks.data?.length === 0 && <Text size="sm" c="dimmed">No webhooks yet.</Text>}
            </QueryState>
          </Paper>
        </section>
      </Stack>
      <Modal opened={!!secret} onClose={() => setSecret(null)} title={secret?.title}>
        <Stack>
          <Alert color="orange" variant="light">Copy it now. It is not shown again.</Alert>
          <Code block style={{ wordBreak: "break-all" }}>{secret?.value}</Code>
          <CopyButton value={secret?.value ?? ""}>{({ copied, copy }) => <Button onClick={copy}>{copied ? "Copied" : "Copy"}</Button>}</CopyButton>
        </Stack>
      </Modal>
    </>
  );
}

const ACTIONS: Record<string, string> = {
  login: "Signed in", "login.failed": "Failed sign-in", "password.changed": "Changed own password",
  "totp.enabled": "Turned on two-step sign-in", "totp.disabled": "Turned off two-step sign-in",
  "user.created": "Added admin", "user.updated": "Changed admin", "user.roles": "Changed admin access",
  "space.created": "Created space", "space.updated": "Changed space settings", "space.deleted": "Deleted space",
  "member.created": "Added person", "member.updated": "Edited person", "member.deactivated": "Deactivated person",
  "member.erased": "Erased person's data", "member.imported": "Imported people",
  "face.enrolled": "Added face photos", "face.bulk_enrolled": "Uploaded photo zip", "face.deleted": "Deleted face photo",
  "face.photo_viewed": "Viewed face photo", "qr.issued": "Issued new QR code",
  "shift.created": "Added shift", "shift.updated": "Edited shift", "shift.deactivated": "Removed shift",
  "shift.assigned": "Assigned shift", "shift.unassigned": "Removed shift change",
  "holiday.added": "Added holiday", "holiday.preset": "Added public holidays", "holiday.deleted": "Removed holiday",
  "leave.added": "Added leave", "leave.deleted": "Removed leave",
  "device.created": "Added kiosk", "device.enrolled": "Kiosk connected", "device.enroll_code": "New pairing code",
  "device.revoked": "Disconnected kiosk", "device.removed": "Removed kiosk", "device.renamed": "Renamed kiosk",
  "device.settings_token": "Opened kiosk settings",
  "event.manual": "Added a time by hand", "event.deleted": "Removed a time added by hand",
  "event.approve": "Approved check-in", "event.reject": "Rejected check-in", "event.snapshot_viewed": "Viewed kiosk snapshot",
  "days.recomputed": "Recalculated days",
  "api_key.created": "Created API key", "api_key.revoked": "Revoked API key",
  "webhook.created": "Added webhook", "webhook.deleted": "Removed webhook", "setup.first_admin": "First-run setup",
};
const HIDDEN_DETAIL = /(^|_)id$|^target$/;

type AuditRow = { id: number; at: string; user: string | null; action: string; target: string; detail: Record<string, unknown>; ip: string | null };

export function Audit() {
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [space, setSpace] = useState<string | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["audit", space],
    queryFn: ({ pageParam }) => api<AuditRow[]>("/audit", { query: { space_id: space, limit: 200, before: pageParam } }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === 200 ? last[last.length - 1].id : undefined),
  });
  const rows = q.data?.pages.flat() ?? [];
  return (
    <>
      <PageHeader title="Audit log" description="Every change made by an admin, and every sign-in."
        actions={<Select placeholder="All spaces" aria-label="Space" data={(spaces.data ?? []).map((s) => ({ value: s.id, label: s.name }))} value={space} onChange={setSpace} clearable />} />
      <QueryState q={q}>
        <Paper withBorder>
          <Table.ScrollContainer minWidth={800}>
            <Table verticalSpacing={6} fz="sm">
              <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Who</Table.Th><Table.Th>What</Table.Th><Table.Th>Details</Table.Th><Table.Th>IP</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {rows.map((a) => (
                  <Table.Tr key={a.id}>
                    <Table.Td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(a.at)}</Table.Td>
                    <Table.Td>{a.user ?? (a.action.startsWith("device.") ? "kiosk" : "system")}</Table.Td>
                    <Table.Td>{ACTIONS[a.action] ?? a.action}</Table.Td>
                    <Table.Td><Text size="xs" c="dimmed" lineClamp={2}>
                      {Object.entries(a.detail).filter(([k, v]) => !HIDDEN_DETAIL.test(k) && v !== null && v !== "")
                        .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(", ")}
                    </Text></Table.Td>
                    <Table.Td>{a.ip}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {rows.length === 0 && <Text p="md" c="dimmed">Nothing recorded yet.</Text>}
        </Paper>
        {q.hasNextPage && <Button mt="md" variant="default" onClick={() => q.fetchNextPage()} loading={q.isFetchingNextPage}>Load older entries</Button>}
      </QueryState>
    </>
  );
}

export function Account() {
  const { me, refresh } = useMe();
  const [pw, setPw] = useState({ current_password: "", new_password: "", repeat: "" });
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const mismatch = pw.repeat.length > 0 && pw.repeat !== pw.new_password;

  const changePw = useMutation({
    mutationFn: () => api("/auth/password", { json: { current_password: pw.current_password, new_password: pw.new_password } }),
    onSuccess: () => { notifyOk("Password changed. Other sessions were signed out."); setPw({ current_password: "", new_password: "", repeat: "" }); },
    onError: notifyError,
  });
  const startTotp = useMutation({
    mutationFn: async () => {
      const r = await api<{ secret: string; uri: string }>("/auth/totp/setup", { method: "POST" });
      return { secret: r.secret, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 220 }) };
    },
    onSuccess: setSetup,
    onError: notifyError,
  });
  const toggle = useMutation({
    mutationFn: (on: boolean) => api(on ? "/auth/totp/enable" : "/auth/totp/disable", { json: { code } }),
    onSuccess: (_, on) => { notifyOk(on ? "Two-step sign-in is on" : "Two-step sign-in is off"); setSetup(null); setCode(""); refresh(); },
    onError: (e) => { setCode(""); notifyError(e); },
  });

  return (
    <>
      <PageHeader title="My account" description={`${me.name}, ${me.email}`} />
      <Stack maw={560} gap="lg">
        <Paper withBorder p="lg">
          <Title order={4} mb="sm">Two-step sign-in</Title>
          {me.totp_enabled ? (
            <Stack>
              <Text size="sm">On. You need a code from your authenticator app to sign in.</Text>
              <Text size="sm" fw={500}>Enter a code to turn it off</Text>
              <Group align="flex-end"><PinInput length={6} type="number" value={code} onChange={setCode} aria-label="6-digit code" />
                <Button variant="default" color="red" onClick={() => toggle.mutate(false)} loading={toggle.isPending} disabled={code.length !== 6}>Turn off</Button></Group>
            </Stack>
          ) : !setup ? (
            <Stack align="flex-start">
              <Text size="sm">Protect your account with a code from an authenticator app such as Google Authenticator or 1Password.
                If you lose your phone, another super admin can turn it off for you.</Text>
              <Button onClick={() => startTotp.mutate()} loading={startTotp.isPending}>Set up two-step sign-in</Button>
            </Stack>
          ) : (
            <Stack>
              <Text size="sm">Scan this with your authenticator app, then enter the 6-digit code it shows.</Text>
              <Image src={setup.qr} w={220} h={220} alt="Authenticator QR code" />
              <Text size="xs" c="dimmed">Can't scan? Enter this key: <Code>{setup.secret}</Code></Text>
              <Group align="flex-end"><PinInput length={6} type="number" value={code} onChange={setCode} aria-label="6-digit code" />
                <Button onClick={() => toggle.mutate(true)} loading={toggle.isPending} disabled={code.length !== 6}>Turn on</Button></Group>
            </Stack>
          )}
        </Paper>
        <Paper withBorder p="lg">
          <Title order={4} mb="sm">Change password</Title>
          <Stack>
            <PasswordInput label="Current password" autoComplete="current-password" value={pw.current_password}
              onChange={(e) => setPw({ ...pw, current_password: e.currentTarget.value })} />
            <PasswordInput label="New password" autoComplete="new-password" description="At least 10 characters" value={pw.new_password}
              onChange={(e) => setPw({ ...pw, new_password: e.currentTarget.value })} />
            <PasswordInput label="Repeat new password" autoComplete="new-password" value={pw.repeat}
              error={mismatch ? "The two passwords are different" : undefined}
              onChange={(e) => setPw({ ...pw, repeat: e.currentTarget.value })} />
            <Button onClick={() => changePw.mutate()} loading={changePw.isPending} w="fit-content"
              disabled={!pw.current_password || pw.new_password.length < 10 || pw.repeat !== pw.new_password}>Change password</Button>
          </Stack>
        </Paper>
      </Stack>
    </>
  );
}
