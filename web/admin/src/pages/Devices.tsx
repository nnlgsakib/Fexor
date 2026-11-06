import { Alert, Badge, Button, Code, CopyButton, Group, Modal, Paper, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { IconPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import type { Device } from "../types";
import { PageHeader, fmtDateTime, notifyError, useSpace } from "../ui";

export function Devices() {
  const { spaceId, canManage } = useSpace();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["devices", spaceId], queryFn: () => api<Device[]>(`/spaces/${spaceId}/devices`), refetchInterval: 15_000 });
  const [addOpen, addCtl] = useDisclosure();
  const [name, setName] = useState("");
  const [code, setCode] = useState<{ name: string; code: string; expires: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["devices", spaceId] });

  const add = async () => {
    try {
      const r = await api<Device & { enroll_code: string; enroll_expires_at: string }>(`/spaces/${spaceId}/devices`, { json: { name } });
      addCtl.close(); setName(""); setCode({ name: r.name, code: r.enroll_code, expires: r.enroll_expires_at }); refresh();
    } catch (e) { notifyError(e); }
  };
  const newCode = async (d: Device) => {
    try {
      const r = await api<{ enroll_code: string; enroll_expires_at: string }>(`/spaces/${spaceId}/devices/${d.id}/enroll-code`, { method: "POST" });
      setCode({ name: d.name, code: r.enroll_code, expires: r.enroll_expires_at }); refresh();
    } catch (e) { notifyError(e); }
  };
  const revoke = (d: Device) => modals.openConfirmModal({
    title: `Disconnect "${d.name}"?`,
    children: <Text size="sm">The kiosk stops syncing immediately. Check-ins it stored while offline and has not uploaded yet are lost.</Text>,
    labels: { confirm: "Disconnect", cancel: "Cancel" }, confirmProps: { color: "red" },
    onConfirm: async () => { try { await api(`/spaces/${spaceId}/devices/${d.id}`, { method: "DELETE" }); refresh(); } catch (e) { notifyError(e); } },
  });

  return (
    <>
      <PageHeader title="Kiosks"
        description="A kiosk is any computer with a camera: a Raspberry Pi, a laptop or a PC. It checks faces on the device and keeps working when the network is down."
        actions={canManage && <Button leftSection={<IconPlus size={16} />} onClick={addCtl.open}>Add kiosk</Button>} />
      <Paper withBorder>
        <Table.ScrollContainer minWidth={700}>
          <Table verticalSpacing="sm">
            <Table.Thead><Table.Tr><Table.Th>Name</Table.Th><Table.Th>Status</Table.Th><Table.Th>Last seen</Table.Th><Table.Th>Waiting to upload</Table.Th><Table.Th>System</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>
              {q.data?.map((d) => (
                <Table.Tr key={d.id}>
                  <Table.Td fw={500}>{d.name}</Table.Td>
                  <Table.Td>
                    {d.revoked_at ? <Badge color="gray" variant="light" tt="none">Disconnected</Badge>
                      : !d.enrolled_at ? <Badge color="yellow" variant="light" tt="none">Waiting for setup</Badge>
                      : d.online ? <Badge color="teal" variant="light" tt="none">Online</Badge>
                      : <Badge color="orange" variant="light" tt="none">Offline</Badge>}
                    {d.info?.camera_ok === false && <Badge ml={6} color="red" variant="light" tt="none">Camera problem</Badge>}
                  </Table.Td>
                  <Table.Td>{fmtDateTime(d.last_seen_at)}</Table.Td>
                  <Table.Td>{d.info?.outbox ?? "–"}</Table.Td>
                  <Table.Td><Text size="xs" c="dimmed">{[d.info?.machine, d.info?.version && `v${d.info.version}`].filter(Boolean).join(", ")}</Text></Table.Td>
                  <Table.Td ta="right">
                    {canManage && <Group gap={6} justify="flex-end">
                      <Button size="xs" variant="default" onClick={() => newCode(d)}>{d.enrolled_at ? "Set up again" : "New code"}</Button>
                      {!d.revoked_at && <Button size="xs" variant="subtle" color="red" onClick={() => revoke(d)}>Disconnect</Button>}
                    </Group>}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {q.data?.length === 0 && <Text p="lg" c="dimmed">No kiosks yet. Add one, then enter its code on the kiosk screen.</Text>}
      </Paper>

      <Modal opened={addOpen} onClose={addCtl.close} title="Add kiosk">
        <Stack>
          <TextInput label="Name" placeholder="Front gate, Lobby, Floor 2" value={name} onChange={(e) => setName(e.currentTarget.value)} data-autofocus />
          <Button onClick={add} disabled={!name.trim()}>Create setup code</Button>
        </Stack>
      </Modal>
      <Modal opened={!!code} onClose={() => setCode(null)} title={`Set up ${code?.name}`}>
        {code && (
          <Stack>
            <Text size="sm">On the kiosk, open <Code>http://127.0.0.1:8300</Code> and enter this server's address and the code below. Or run:</Text>
            <Code block>fexor-kiosk enroll --server {window.location.origin} --code {code.code}</Code>
            <Group justify="center" py="sm"><Text fz={36} fw={700} style={{ letterSpacing: "0.08em" }}>{code.code}</Text></Group>
            <CopyButton value={code.code}>{({ copied, copy }) => <Button variant="default" onClick={copy}>{copied ? "Copied" : "Copy code"}</Button>}</CopyButton>
            <Alert variant="light" color="gray">The code works once and expires {fmtDateTime(code.expires)}.</Alert>
          </Stack>
        )}
      </Modal>
    </>
  );
}
