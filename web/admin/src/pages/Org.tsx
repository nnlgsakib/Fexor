import {
  Alert, Badge, Button, Checkbox, Code, CopyButton, Group, Image, Modal, Paper, PasswordInput, PinInput, Select, Stack,
  Table, Text, TextInput, Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useState } from "react";
import { api } from "../api";
import { useMe } from "../auth";
import type { Role, Space } from "../types";
import { PageHeader, fmtDateTime, notifyError, notifyOk } from "../ui";

interface AdminUser {
  id: string; email: string; name: string; active: boolean; is_superadmin: boolean; totp_enabled: boolean;
  last_login_at: string | null; roles: { space_id: string; role: Role }[];
}

export function Admins() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<AdminUser[]>("/users") });
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [addOpen, addCtl] = useDisclosure();
  const [form, setForm] = useState({ email: "", name: "", password: "", is_superadmin: false });
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [roles, setRoles] = useState<Record<string, Role | "">>({});
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const spaceName = (id: string) => spaces.data?.find((s) => s.id === id)?.name ?? "removed space";

  const create = async () => {
    try { await api("/users", { json: form }); notifyOk("Admin added"); addCtl.close(); setForm({ email: "", name: "", password: "", is_superadmin: false }); refresh(); }
    catch (e) { notifyError(e); }
  };
  const openRoles = (u: AdminUser) => { setEditing(u); setRoles(Object.fromEntries(u.roles.map((r) => [r.space_id, r.role]))); };
  const saveRoles = async () => {
    try {
      await api(`/users/${editing!.id}/roles`, { method: "PUT", json: Object.entries(roles).filter(([, r]) => r).map(([space_id, role]) => ({ space_id, role })) });
      notifyOk("Access updated"); setEditing(null); refresh();
    } catch (e) { notifyError(e); }
  };
  const patch = async (u: AdminUser, body: object, msg: string) => {
    try { await api(`/users/${u.id}`, { method: "PATCH", json: body }); notifyOk(msg); refresh(); } catch (e) { notifyError(e); }
  };

  return (
    <>
      <PageHeader title="Admins" description="Super admins manage everything. Other admins see only the spaces you give them."
        actions={<Button onClick={addCtl.open}>Add admin</Button>} />
      <Paper withBorder>
        <Table.ScrollContainer minWidth={760}>
          <Table verticalSpacing="sm">
            <Table.Thead><Table.Tr><Table.Th>Name</Table.Th><Table.Th>Access</Table.Th><Table.Th>Two-step sign-in</Table.Th><Table.Th>Last sign-in</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>
              {users.data?.map((u) => (
                <Table.Tr key={u.id} opacity={u.active ? 1 : 0.5}>
                  <Table.Td><Text fw={500}>{u.name}</Text><Text size="xs" c="dimmed">{u.email}</Text></Table.Td>
                  <Table.Td>
                    {u.is_superadmin ? <Badge tt="none">Super admin</Badge> : u.roles.length === 0 ? <Text size="sm" c="dimmed">No spaces</Text>
                      : <Text size="sm">{u.roles.map((r) => `${spaceName(r.space_id)} (${r.role})`).join(", ")}</Text>}
                  </Table.Td>
                  <Table.Td>{u.totp_enabled ? "On" : <Text span c="orange.8" size="sm">Off</Text>}</Table.Td>
                  <Table.Td>{fmtDateTime(u.last_login_at)}</Table.Td>
                  <Table.Td ta="right">
                    <Group gap={6} justify="flex-end" wrap="nowrap">
                      {!u.is_superadmin && <Button size="xs" variant="default" onClick={() => openRoles(u)}>Spaces</Button>}
                      {u.totp_enabled && <Button size="xs" variant="subtle" onClick={() => patch(u, { reset_2fa: true }, "Two-step sign-in reset")}>Reset 2FA</Button>}
                      <Button size="xs" variant="subtle" color={u.active ? "red" : "teal"} onClick={() => patch(u, { active: !u.active }, u.active ? "Admin deactivated" : "Admin reactivated")}>
                        {u.active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Modal opened={addOpen} onClose={addCtl.close} title="Add admin">
        <Stack>
          <TextInput label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
          <TextInput label="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.currentTarget.value })} />
          <PasswordInput label="Temporary password" description="At least 10 characters. Ask them to change it after signing in."
            value={form.password} onChange={(e) => setForm({ ...form, password: e.currentTarget.value })} />
          <Checkbox label="Super admin (full access to every space and setting)" checked={form.is_superadmin}
            onChange={(e) => setForm({ ...form, is_superadmin: e.currentTarget.checked })} />
          <Button onClick={create} disabled={!form.email || !form.name || form.password.length < 10}>Add admin</Button>
        </Stack>
      </Modal>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={`Spaces for ${editing?.name}`}>
        <Stack>
          {spaces.data?.map((s) => (
            <Group key={s.id} justify="space-between">
              <Text>{s.name}</Text>
              <Select w={150} value={roles[s.id] || ""} onChange={(v) => setRoles({ ...roles, [s.id]: (v ?? "") as Role | "" })}
                data={[{ value: "", label: "No access" }, { value: "viewer", label: "Viewer" }, { value: "manager", label: "Manager" }, { value: "owner", label: "Owner" }]} />
            </Group>
          ))}
          <Text size="xs" c="dimmed">Viewers see reports. Managers also edit people, schedules and kiosks. Owners also change space settings.</Text>
          <Button onClick={saveRoles}>Save access</Button>
        </Stack>
      </Modal>
    </>
  );
}

export function Integrations() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api<{ id: string; name: string; prefix: string; space_id: string | null; last_used_at: string | null; revoked_at: string | null }[]>("/api-keys") });
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: () => api<{ id: string; url: string; space_id: string | null; events: string[]; active: boolean }[]>("/webhooks") });
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keySpace, setKeySpace] = useState<string | null>(null);
  const [hookUrl, setHookUrl] = useState("");
  const [hookSpace, setHookSpace] = useState<string | null>(null);
  const spaceOptions = (spaces.data ?? []).map((s) => ({ value: s.id, label: s.name }));

  const addKey = async () => {
    try { const r = await api<{ token: string }>("/api-keys", { json: { name: keyName, space_id: keySpace } }); setSecret({ title: "New API key", value: r.token }); setKeyName(""); qc.invalidateQueries({ queryKey: ["keys"] }); }
    catch (e) { notifyError(e); }
  };
  const addHook = async () => {
    try { const r = await api<{ secret: string }>("/webhooks", { json: { url: hookUrl, space_id: hookSpace } }); setSecret({ title: "Webhook signing secret", value: r.secret }); setHookUrl(""); qc.invalidateQueries({ queryKey: ["hooks"] }); }
    catch (e) { notifyError(e); }
  };
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
            <Group align="flex-end" mb="md">
              <TextInput label="Name" placeholder="Payroll export" value={keyName} onChange={(e) => setKeyName(e.currentTarget.value)} />
              <Select label="Limit to space" placeholder="All spaces" data={spaceOptions} value={keySpace} onChange={setKeySpace} clearable />
              <Button onClick={addKey} disabled={!keyName.trim()}>Create key</Button>
            </Group>
            <Table>
              <Table.Tbody>
                {keys.data?.map((k) => (
                  <Table.Tr key={k.id} opacity={k.revoked_at ? 0.5 : 1}>
                    <Table.Td>{k.name}</Table.Td><Table.Td><Code>{k.prefix}…</Code></Table.Td>
                    <Table.Td><Text size="sm" c="dimmed">Last used {fmtDateTime(k.last_used_at)}</Text></Table.Td>
                    <Table.Td ta="right">{!k.revoked_at ? <Button size="xs" variant="subtle" color="red" onClick={() => call(`/api-keys/${k.id}`, "DELETE", "Key revoked", "keys")}>Revoke</Button> : "Revoked"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
        </section>
        <section>
          <Title order={3} fz={20} mb={4}>Webhooks</Title>
          <Text size="sm" c="dimmed" mb="sm">Each check-in is sent to your URL as JSON within seconds, signed in the <Code>X-Fexor-Signature</Code> header (HMAC-SHA256 of <Code>timestamp.body</Code>). Failed deliveries are retried.</Text>
          <Paper withBorder p="md">
            <Group align="flex-end" mb="md">
              <TextInput label="URL" placeholder="https://example.com/fexor" value={hookUrl} onChange={(e) => setHookUrl(e.currentTarget.value)} style={{ flex: 1 }} />
              <Select label="Space" placeholder="All spaces" data={spaceOptions} value={hookSpace} onChange={setHookSpace} clearable />
              <Button onClick={addHook} disabled={!hookUrl.startsWith("http")}>Add webhook</Button>
            </Group>
            <Table>
              <Table.Tbody>
                {hooks.data?.map((h) => (
                  <Table.Tr key={h.id}>
                    <Table.Td style={{ wordBreak: "break-all" }}>{h.url}</Table.Td>
                    <Table.Td ta="right">
                      <Group gap={6} justify="flex-end" wrap="nowrap">
                        <Button size="xs" variant="default" onClick={() => call(`/webhooks/${h.id}/test`, "POST", "Test sent", "hooks")}>Send test</Button>
                        <Button size="xs" variant="subtle" color="red" onClick={() => call(`/webhooks/${h.id}`, "DELETE", "Webhook removed", "hooks")}>Remove</Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
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

export function Audit() {
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [space, setSpace] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["audit", space],
    queryFn: () => api<{ id: number; at: string; user: string | null; action: string; target: string; detail: Record<string, unknown>; ip: string | null }[]>("/audit", { query: { space_id: space, limit: 300 } }),
  });
  return (
    <>
      <PageHeader title="Audit log" description="Every change made by an admin, and every sign-in."
        actions={<Select placeholder="All spaces" data={(spaces.data ?? []).map((s) => ({ value: s.id, label: s.name }))} value={space} onChange={setSpace} clearable />} />
      <Paper withBorder>
        <Table.ScrollContainer minWidth={800}>
          <Table verticalSpacing={6} fz="sm">
            <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Who</Table.Th><Table.Th>What</Table.Th><Table.Th>Details</Table.Th><Table.Th>IP</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {q.data?.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(a.at)}</Table.Td>
                  <Table.Td>{a.user ?? "system"}</Table.Td>
                  <Table.Td><Code>{a.action}</Code></Table.Td>
                  <Table.Td><Text size="xs" c="dimmed" lineClamp={2}>{Object.entries(a.detail).map(([k, v]) => `${k}=${v}`).join(", ")}</Text></Table.Td>
                  <Table.Td>{a.ip}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
    </>
  );
}

export function Account() {
  const { me, refresh } = useMe();
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [code, setCode] = useState("");

  const changePw = async () => {
    try { await api("/auth/password", { json: pw }); notifyOk("Password changed. Other sessions were signed out."); setPw({ current_password: "", new_password: "" }); }
    catch (e) { notifyError(e); }
  };
  const startTotp = async () => {
    try { const r = await api<{ secret: string; uri: string }>("/auth/totp/setup", { method: "POST" }); setSetup({ secret: r.secret, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 220 }) }); }
    catch (e) { notifyError(e); }
  };
  const enable = async () => {
    try { await api("/auth/totp/enable", { json: { code } }); notifyOk("Two-step sign-in is on"); setSetup(null); setCode(""); refresh(); }
    catch (e) { notifyError(e); }
  };
  const disable = async () => {
    try { await api("/auth/totp/disable", { json: { code } }); notifyOk("Two-step sign-in is off"); setCode(""); refresh(); }
    catch (e) { notifyError(e); }
  };

  return (
    <>
      <PageHeader title="My account" description={`${me.name}, ${me.email}`} />
      <Stack maw={560} gap="lg">
        <Paper withBorder p="lg">
          <Title order={4} mb="sm">Two-step sign-in</Title>
          {me.totp_enabled ? (
            <Stack>
              <Text size="sm">On. You need a code from your authenticator app to sign in.</Text>
              <Group align="flex-end"><PinInput length={6} type="number" value={code} onChange={setCode} /><Button variant="default" color="red" onClick={disable} disabled={code.length !== 6}>Turn off</Button></Group>
            </Stack>
          ) : !setup ? (
            <Stack align="flex-start">
              <Text size="sm">Protect your account with a code from an authenticator app such as Google Authenticator or 1Password.</Text>
              <Button onClick={startTotp}>Set up two-step sign-in</Button>
            </Stack>
          ) : (
            <Stack>
              <Text size="sm">Scan this with your authenticator app, then enter the 6-digit code it shows.</Text>
              <Image src={setup.qr} w={220} h={220} alt="Authenticator QR code" />
              <Text size="xs" c="dimmed">Can't scan? Enter this key: <Code>{setup.secret}</Code></Text>
              <Group align="flex-end"><PinInput length={6} type="number" value={code} onChange={setCode} /><Button onClick={enable} disabled={code.length !== 6}>Turn on</Button></Group>
            </Stack>
          )}
        </Paper>
        <Paper withBorder p="lg">
          <Title order={4} mb="sm">Change password</Title>
          <Stack>
            <PasswordInput label="Current password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.currentTarget.value })} />
            <PasswordInput label="New password" description="At least 10 characters" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.currentTarget.value })} />
            <Button onClick={changePw} disabled={!pw.current_password || pw.new_password.length < 10} w="fit-content">Change password</Button>
          </Stack>
        </Paper>
      </Stack>
    </>
  );
}
