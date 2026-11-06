import { Alert, Badge, Button, Code, Group, Image, List, Modal, Paper, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDisclosure, useInterval } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { IconPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useMe } from "../auth";
import type { Device } from "../types";
import { PageHeader, fmtDateTime, notifyError, useSpace } from "../ui";

type Pairing = { name: string; code: string; expires: string };

function useQr(text: string | null) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!text) { setSrc(null); return; }
    QRCode.toDataURL(text, { margin: 2, width: 320, errorCorrectionLevel: "M" }).then(setSrc);
  }, [text]);
  return src;
}

function Countdown({ until }: { until: string }) {
  const [now, setNow] = useState(Date.now());
  const iv = useInterval(() => setNow(Date.now()), 1000);
  useEffect(() => { iv.start(); return iv.stop; }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const left = Math.max(0, Math.floor((new Date(until).getTime() - now) / 1000));
  if (!left) return <Text c="red" size="sm">Expired. Close this and make a new one.</Text>;
  return <Text c="dimmed" size="sm">Works for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} more.</Text>;
}

function PairingModal({ pairing, onClose }: { pairing: Pairing | null; onClose: () => void }) {
  const { me } = useMe();
  const [server, setServer] = useState("");
  useEffect(() => { if (pairing) setServer(me.public_url || window.location.origin); }, [pairing, me.public_url]);
  const isLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)/.test(server);
  const qr = useQr(pairing ? `FXP1:${JSON.stringify({ u: server.replace(/\/+$/, ""), c: pairing.code })}` : null);

  return (
    <Modal opened={!!pairing} onClose={onClose} title={`Set up "${pairing?.name}"`} size="lg">
      {pairing && (
        <Stack>
          <List type="ordered" spacing={4} size="sm">
            <List.Item>Start the kiosk app on the kiosk computer. Its screen says <b>Set up this kiosk</b>.</List.Item>
            <List.Item>Choose the camera there.</List.Item>
            <List.Item>Hold this QR code in front of the kiosk camera. It connects by itself.</List.Item>
          </List>
          <Group justify="center">{qr && <Image src={qr} w={300} h={300} alt="Pairing QR code" />}</Group>
          <Countdown until={pairing.expires} />
          <TextInput label="Server address the kiosk will use" value={server}
            onChange={(e) => setServer(e.currentTarget.value)}
            description="Must be reachable from the kiosk. The QR updates when you change it." />
          {isLocalhost && (
            <Alert color="orange" variant="light">
              "localhost" only works if the kiosk runs on this same computer. For a kiosk on another device,
              use this computer's network address, for example http://192.168.1.20.
            </Alert>
          )}
          <Text size="sm" c="dimmed">
            No camera working yet? On the kiosk, open <b>Type the code instead</b> and enter the address above
            and the code <Code fz="sm">{pairing.code}</Code>.
          </Text>
        </Stack>
      )}
    </Modal>
  );
}

function SettingsQrModal({ data, onClose }: { data: { name: string; token: string; expires: string } | null; onClose: () => void }) {
  const qr = useQr(data?.token ?? null);
  return (
    <Modal opened={!!data} onClose={onClose} title={`Open settings on "${data?.name}"`}>
      {data && (
        <Stack align="center">
          <Text size="sm">Show this to the kiosk camera while it is waiting for people. Its settings screen opens
            so you can change the camera or pair it again. It only works on this kiosk.</Text>
          {qr && <Image src={qr} w={280} h={280} alt="Settings QR code" />}
          <Countdown until={data.expires} />
        </Stack>
      )}
    </Modal>
  );
}

export function Devices() {
  const { spaceId, canManage } = useSpace();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["devices", spaceId], queryFn: () => api<Device[]>(`/spaces/${spaceId}/devices`), refetchInterval: 5_000 });
  const [addOpen, addCtl] = useDisclosure();
  const [name, setName] = useState("");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [settingsQr, setSettingsQr] = useState<{ name: string; token: string; expires: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["devices", spaceId] });

  // Close the pairing window once the kiosk has connected.
  const paired = pairing && q.data?.some((d) => d.name === pairing.name && d.online && !d.enroll_pending);
  useEffect(() => { if (paired) setPairing(null); }, [paired]);

  const add = async () => {
    try {
      const r = await api<Device & { enroll_code: string; enroll_expires_at: string }>(`/spaces/${spaceId}/devices`, { json: { name } });
      addCtl.close(); setName(""); setPairing({ name: r.name, code: r.enroll_code, expires: r.enroll_expires_at }); refresh();
    } catch (e) { notifyError(e); }
  };
  const newCode = async (d: Device) => {
    try {
      const r = await api<{ enroll_code: string; enroll_expires_at: string }>(`/spaces/${spaceId}/devices/${d.id}/enroll-code`, { method: "POST" });
      setPairing({ name: d.name, code: r.enroll_code, expires: r.enroll_expires_at }); refresh();
    } catch (e) { notifyError(e); }
  };
  const openSettings = async (d: Device) => {
    try {
      const r = await api<{ token: string; expires_at: string }>(`/spaces/${spaceId}/devices/${d.id}/settings-token`, { method: "POST" });
      setSettingsQr({ name: d.name, token: r.token, expires: r.expires_at });
    } catch (e) { notifyError(e); }
  };
  const revoke = (d: Device) => modals.openConfirmModal({
    title: `Disconnect "${d.name}"?`,
    children: <Text size="sm">The kiosk stops working immediately. Check-ins it stored while offline and has not uploaded yet are lost.</Text>,
    labels: { confirm: "Disconnect", cancel: "Cancel" }, confirmProps: { color: "red" },
    onConfirm: async () => { try { await api(`/spaces/${spaceId}/devices/${d.id}`, { method: "DELETE" }); refresh(); } catch (e) { notifyError(e); } },
  });

  return (
    <>
      <PageHeader title="Kiosks"
        description="A kiosk is any computer with a camera: a Raspberry Pi, a laptop or a PC. It checks faces on the device and keeps working when the network is down."
        actions={canManage && <Button leftSection={<IconPlus size={16} />} onClick={addCtl.open}>Add kiosk</Button>} />
      <Paper withBorder>
        <Table.ScrollContainer minWidth={760}>
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
                    {d.info?.camera_ok === false && d.online && <Badge ml={6} color="red" variant="light" tt="none">Camera problem</Badge>}
                  </Table.Td>
                  <Table.Td>{fmtDateTime(d.last_seen_at)}</Table.Td>
                  <Table.Td>{d.info?.outbox ?? "–"}</Table.Td>
                  <Table.Td><Text size="xs" c="dimmed">{[d.info?.machine, d.info?.version && `v${d.info.version}`].filter(Boolean).join(", ")}</Text></Table.Td>
                  <Table.Td ta="right">
                    {canManage && <Group gap={6} justify="flex-end" wrap="nowrap">
                      {d.enrolled_at && !d.revoked_at && <Button size="xs" variant="default" onClick={() => openSettings(d)}>Settings QR</Button>}
                      <Button size="xs" variant="default" onClick={() => newCode(d)}>{d.enrolled_at ? "Set up again" : "Show pairing QR"}</Button>
                      {!d.revoked_at && <Button size="xs" variant="subtle" color="red" onClick={() => revoke(d)}>Disconnect</Button>}
                    </Group>}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {q.data?.length === 0 && (
          <Stack p="lg" align="flex-start" gap="xs">
            <Text fw={600}>No kiosks yet</Text>
            <Text c="dimmed" size="sm">Install the kiosk app on a computer with a camera, then click Add kiosk and show the QR code to its camera.</Text>
            {canManage && <Button mt="xs" onClick={addCtl.open}>Add kiosk</Button>}
          </Stack>
        )}
      </Paper>

      <Modal opened={addOpen} onClose={addCtl.close} title="Add kiosk">
        <Stack>
          <TextInput label="Name" placeholder="Front gate, Lobby, Floor 2" value={name} onChange={(e) => setName(e.currentTarget.value)} data-autofocus
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) add(); }} />
          <Button onClick={add} disabled={!name.trim()}>Continue</Button>
        </Stack>
      </Modal>
      <PairingModal pairing={pairing} onClose={() => setPairing(null)} />
      <SettingsQrModal data={settingsQr} onClose={() => setSettingsQr(null)} />
    </>
  );
}
