import {
  ActionIcon, Alert, Anchor, Badge, Button, Checkbox, Drawer, Group, Image, List, Menu, Modal, NumberInput, Pagination,
  Paper, SegmentedControl, Select, SimpleGrid, Stack, Table, Tabs, Text, TextInput,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { Dropzone } from "@mantine/dropzone";
import { useDisclosure } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import {
  IconChevronDown, IconFileSpreadsheet, IconPhotoPlus, IconPlus, IconPrinter, IconSearch, IconTrash, IconUpload,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, url } from "../api";
import type { FieldDef, Member, Shift, Space } from "../types";
import { PageHeader, notifyError, notifyOk, today, useSpace } from "../ui";

const PAGE = 50;
type ImportReport = { total: number; created: number; updated: number; errors: { row: number; error: string }[]; dry_run: boolean };
type FaceResult = { file: string; ok: boolean; error?: string };

const FACE_ERRORS: Record<string, string> = {
  no_face: "No face found",
  multiple_faces: "More than one face",
  face_too_small: "Face too small, use a closer photo",
  blurry: "Photo is blurry",
  too_dark: "Photo is too dark",
  too_bright: "Photo is too bright",
  not_frontal: "Face is turned, use a front-facing photo",
  not_an_image: "Not an image",
  too_many_photos: "Already has 10 photos",
  no_member_with_this_id: "No person with this ID",
  file_too_large: "File too large",
};
const faceError = (e?: string) => (e ?? "").split(",").map((x) => FACE_ERRORS[x] ?? x).join("; ");

export function People() {
  const { spaceId, space, canManage } = useSpace();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<Member | "new" | null>(null);
  const [importOpen, importCtl] = useDisclosure();
  const [zipOpen, zipCtl] = useDisclosure();
  const [assignOpen, assignCtl] = useDisclosure();

  const q = useQuery({
    queryKey: ["members", spaceId, search, active, page],
    queryFn: () => api<{ items: Member[]; total: number }>(`/spaces/${spaceId}/members`,
      { query: { q: search, active: active === "all" ? undefined : active, page, size: PAGE } }),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members", spaceId] });
  const fields = (space?.field_schema ?? []).slice(0, 2);
  const items = q.data?.items ?? [];
  const label = space?.member_label ?? "Person";

  return (
    <>
      <PageHeader
        title="People"
        description={`${q.data?.total ?? 0} ${label.toLowerCase()}s. Each person needs at least one clear face photo and a QR card to use the kiosk.`}
        actions={canManage && (
          <>
            <Menu position="bottom-end">
              <Menu.Target><Button variant="default" rightSection={<IconChevronDown size={14} />}>Bulk actions</Button></Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconFileSpreadsheet size={16} />} onClick={importCtl.open}>Import from Excel or CSV</Menu.Item>
                <Menu.Item leftSection={<IconPhotoPlus size={16} />} onClick={zipCtl.open}>Upload photos (zip)</Menu.Item>
                <Menu.Item leftSection={<IconPrinter size={16} />} disabled={!selected.length}
                  onClick={() => nav(`/spaces/${spaceId}/cards?ids=${selected.join(",")}`)}>Print QR cards for selected</Menu.Item>
                <Menu.Item leftSection={<IconPrinter size={16} />} onClick={() => nav(`/spaces/${spaceId}/cards`)}>Print all QR cards</Menu.Item>
                <Menu.Item disabled={!selected.length} onClick={assignCtl.open}>Assign shift to selected</Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing("new")}>Add {label.toLowerCase()}</Button>
          </>
        )}
      />
      <Group mb="md">
        <TextInput placeholder="Search name, ID or title" leftSection={<IconSearch size={16} />} value={search} w={300}
          onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} />
        <SegmentedControl value={active} onChange={(v) => { setActive(v); setPage(1); }}
          data={[{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }, { value: "all", label: "All" }]} />
      </Group>
      <Paper withBorder>
        <Table.ScrollContainer minWidth={760}>
          <Table highlightOnHover verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={40}>
                  <Checkbox aria-label="Select all" checked={items.length > 0 && items.every((m) => selected.includes(m.id))}
                    onChange={(e) => setSelected(e.currentTarget.checked ? items.map((m) => m.id) : [])} />
                </Table.Th>
                <Table.Th>Name</Table.Th><Table.Th>ID</Table.Th><Table.Th>Title</Table.Th>
                {fields.map((f) => <Table.Th key={f.key}>{f.label}</Table.Th>)}
                <Table.Th>Face photos</Table.Th><Table.Th>QR</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((m) => (
                <Table.Tr key={m.id} style={{ cursor: "pointer" }} onClick={() => setEditing(m)} opacity={m.active ? 1 : 0.55}>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <Checkbox aria-label={`Select ${m.full_name}`} checked={selected.includes(m.id)}
                      onChange={(e) => setSelected(e.currentTarget.checked ? [...selected, m.id] : selected.filter((x) => x !== m.id))} />
                  </Table.Td>
                  <Table.Td fw={500}>{m.full_name}</Table.Td>
                  <Table.Td>{m.external_id}</Table.Td>
                  <Table.Td>{m.title}</Table.Td>
                  {fields.map((f) => <Table.Td key={f.key}>{String(m.fields?.[f.key] ?? "")}</Table.Td>)}
                  <Table.Td>{m.face_count ? m.face_count : <Badge color="red" variant="light" tt="none">Missing</Badge>}</Table.Td>
                  <Table.Td>{m.has_qr ? "Issued" : <Text c="dimmed" size="sm">Not yet</Text>}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {items.length === 0 && !q.isLoading && (
          <Stack p="xl" align="flex-start">
            <Text c="dimmed">{search ? "Nobody matches that search." : "No people here yet."}</Text>
            {canManage && !search && <Group><Button onClick={() => setEditing("new")}>Add one person</Button><Button variant="default" onClick={importCtl.open}>Import a spreadsheet</Button></Group>}
          </Stack>
        )}
      </Paper>
      {(q.data?.total ?? 0) > PAGE && <Pagination mt="md" total={Math.ceil((q.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} />}

      {space && <MemberDrawer space={space} member={editing} onClose={() => setEditing(null)} onChanged={refresh} canManage={canManage} />}
      <ImportModal opened={importOpen} onClose={() => { importCtl.close(); refresh(); }} spaceId={spaceId} />
      <ZipModal opened={zipOpen} onClose={() => { zipCtl.close(); refresh(); }} spaceId={spaceId} />
      <AssignModal opened={assignOpen} onClose={assignCtl.close} spaceId={spaceId} memberIds={selected} />
    </>
  );
}

function FieldInput({ f, value, onChange }: { f: FieldDef; value: unknown; onChange: (v: unknown) => void }) {
  if (f.type === "select") return <Select label={f.label} required={f.required} data={f.options} value={(value as string) ?? null} onChange={onChange} clearable />;
  if (f.type === "number") return <NumberInput label={f.label} required={f.required} value={(value as number) ?? ""} onChange={onChange} />;
  if (f.type === "date") return <DateInput label={f.label} required={f.required} value={(value as string) ?? null} onChange={onChange} clearable />;
  return <TextInput label={f.label} required={f.required} value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />;
}

function MemberDrawer({ space, member, onClose, onChanged, canManage }: {
  space: Space; member: Member | "new" | null; onClose: () => void; onChanged: () => void; canManage: boolean;
}) {
  const isNew = member === "new";
  const m = isNew || !member ? null : member;
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [tab, setTab] = useState<string | null>("details");
  useEffect(() => {
    setTab("details");
    setForm(m ? {
      full_name: m.full_name, external_id: m.external_id, title: m.title, email: m.email ?? "", phone: m.phone ?? "",
      fields: { ...m.fields }, active: m.active, start_date: m.start_date, end_date: m.end_date, consent: !!m.consent_at,
    } : { full_name: "", external_id: "", title: "", email: "", phone: "", fields: {}, active: true, start_date: today(), consent: false });
  }, [member]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () => m
      ? api<Member>(`/spaces/${space.id}/members/${m.id}`, { method: "PATCH", json: form })
      : api<Member>(`/spaces/${space.id}/members`, { json: form }),
    onSuccess: () => { notifyOk(m ? "Changes saved" : "Person added"); onChanged(); if (!m) onClose(); },
    onError: notifyError,
  });
  const remove = (erase: boolean) => modals.openConfirmModal({
    title: erase ? "Erase this person's data?" : "Deactivate this person?",
    children: <Text size="sm">{erase
      ? "Their face photos, QR code and attendance in this space are deleted permanently. This cannot be undone."
      : "They can no longer check in. Their attendance history is kept, and you can reactivate them later."}</Text>,
    labels: { confirm: erase ? "Erase permanently" : "Deactivate", cancel: "Cancel" },
    confirmProps: { color: "red" },
    onConfirm: async () => {
      try {
        await api(`/spaces/${space.id}/members/${m!.id}`, { method: "DELETE", query: { erase } });
        notifyOk(erase ? "Data erased" : "Deactivated");
        onChanged();
        onClose();
      } catch (e) { notifyError(e); }
    },
  });
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const setField = (k: string, v: unknown) => setForm((f) => ({ ...f, fields: { ...(f.fields as object), [k]: v } }));

  return (
    <Drawer opened={member !== null} onClose={onClose} position="right" size="lg"
      title={<Text fw={650} fz="lg">{m ? m.full_name : `Add ${space.member_label.toLowerCase()}`}</Text>}>
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List mb="md">
          <Tabs.Tab value="details">Details</Tabs.Tab>
          {m && <Tabs.Tab value="photos">Face photos</Tabs.Tab>}
          {m && <Tabs.Tab value="qr">QR card</Tabs.Tab>}
          {m && <Tabs.Tab value="schedule">Shift and leave</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="details">
          <Stack>
            <Group grow>
              <TextInput label="Full name" required value={(form.full_name as string) ?? ""} onChange={(e) => set("full_name", e.currentTarget.value)} disabled={!canManage} />
              <TextInput label={space.mode === "single" ? "Roll / student ID" : "Employee ID"} required value={(form.external_id as string) ?? ""}
                onChange={(e) => set("external_id", e.currentTarget.value)} disabled={!canManage} />
            </Group>
            <TextInput label={space.mode === "single" ? "Class or grade" : "Designation"} value={(form.title as string) ?? ""} onChange={(e) => set("title", e.currentTarget.value)} disabled={!canManage} />
            <Group grow>
              <TextInput label="Email" value={(form.email as string) ?? ""} onChange={(e) => set("email", e.currentTarget.value)} disabled={!canManage} />
              <TextInput label="Phone" value={(form.phone as string) ?? ""} onChange={(e) => set("phone", e.currentTarget.value)} disabled={!canManage} />
            </Group>
            {space.field_schema.length > 0 && (
              <SimpleGrid cols={2}>
                {space.field_schema.map((f) => (
                  <FieldInput key={f.key} f={f} value={(form.fields as Record<string, unknown>)?.[f.key]} onChange={(v) => setField(f.key, v)} />
                ))}
              </SimpleGrid>
            )}
            <Group grow>
              <DateInput label="Starts on" value={(form.start_date as string) ?? null} onChange={(v) => set("start_date", v)} clearable disabled={!canManage} />
              <DateInput label="Leaves on" value={(form.end_date as string) ?? null} onChange={(v) => set("end_date", v)} clearable disabled={!canManage} />
            </Group>
            <Checkbox label="Consent to face recognition was collected" checked={!!form.consent} onChange={(e) => set("consent", e.currentTarget.checked)} disabled={!canManage} />
            {m && <Checkbox label="Active (can check in)" checked={!!form.active} onChange={(e) => set("active", e.currentTarget.checked)} disabled={!canManage} />}
            {canManage && (
              <Group justify="space-between" mt="sm">
                <Group gap="xs">
                  {m?.active && <Button variant="subtle" color="red" onClick={() => remove(false)}>Deactivate</Button>}
                  {m && space.role === "owner" && <Button variant="subtle" color="red" onClick={() => remove(true)}>Erase data</Button>}
                </Group>
                <Button onClick={() => save.mutate()} loading={save.isPending}>{m ? "Save changes" : "Add person"}</Button>
              </Group>
            )}
          </Stack>
        </Tabs.Panel>
        {m && <Tabs.Panel value="photos"><Photos space={space} member={m} canManage={canManage} onChanged={onChanged} /></Tabs.Panel>}
        {m && <Tabs.Panel value="qr"><QrPanel space={space} member={m} canManage={canManage} onChanged={onChanged} /></Tabs.Panel>}
        {m && <Tabs.Panel value="schedule"><MemberSchedule space={space} member={m} canManage={canManage} /></Tabs.Panel>}
      </Tabs>
    </Drawer>
  );
}

function Photos({ space, member, canManage, onChanged }: { space: Space; member: Member; canManage: boolean; onChanged: () => void }) {
  const qc = useQueryClient();
  const base = `/spaces/${space.id}/members/${member.id}/faces`;
  const faces = useQuery({ queryKey: ["faces", member.id], queryFn: () => api<{ id: string; has_photo: boolean; created_at: string }[]>(base) });
  const [results, setResults] = useState<FaceResult[]>([]);
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      return api<FaceResult[]>(base, { form: fd });
    },
    onSuccess: (r) => { setResults(r); qc.invalidateQueries({ queryKey: ["faces", member.id] }); onChanged(); },
    onError: notifyError,
  });
  const del = async (id: string) => {
    try { await api(`${base}/${id}`, { method: "DELETE" }); qc.invalidateQueries({ queryKey: ["faces", member.id] }); onChanged(); }
    catch (e) { notifyError(e); }
  };
  return (
    <Stack>
      <Text size="sm" c="dimmed">Use 2 or 3 clear, front-facing photos in normal light. Each photo is checked for quality before it is saved.</Text>
      {canManage && (
        <Dropzone onDrop={(f) => upload.mutate(f)} accept={["image/jpeg", "image/png", "image/webp"]} loading={upload.isPending} maxSize={15 * 1024 ** 2}>
          <Group justify="center" mih={90}><IconUpload size={22} /><Text>Drop photos here or click to choose</Text></Group>
        </Dropzone>
      )}
      {results.filter((r) => !r.ok).map((r) => <Alert key={r.file} color="orange" variant="light">{r.file}: {faceError(r.error)}</Alert>)}
      <SimpleGrid cols={3}>
        {faces.data?.map((f) => (
          <Paper key={f.id} withBorder p={4} pos="relative">
            {f.has_photo ? <Image src={url(`${base}/${f.id}/photo`)} radius="sm" h={140} fit="cover" alt="Enrolled face" />
              : <Stack h={140} justify="center" align="center"><Text size="xs" c="dimmed">Photo not kept</Text></Stack>}
            {canManage && (
              <ActionIcon color="red" variant="filled" size="sm" pos="absolute" top={8} right={8} aria-label="Delete photo" onClick={() => del(f.id)}>
                <IconTrash size={14} />
              </ActionIcon>
            )}
          </Paper>
        ))}
      </SimpleGrid>
      {faces.data?.length === 0 && <Text c="dimmed">No face photos yet. This person can't use the kiosk until one is added.</Text>}
    </Stack>
  );
}

function QrPanel({ space, member, canManage, onChanged }: { space: Space; member: Member; canManage: boolean; onChanged: () => void }) {
  const [v, setV] = useState(0);
  const base = `/spaces/${space.id}/members/${member.id}`;
  const reissue = () => modals.openConfirmModal({
    title: "Issue a new QR code?",
    children: <Text size="sm">The old card stops working on every kiosk within a minute. Use this if a card was lost or shared.</Text>,
    labels: { confirm: "Issue new code", cancel: "Cancel" },
    onConfirm: async () => {
      try { await api(`${base}/qr`, { method: "POST" }); setV((x) => x + 1); onChanged(); notifyOk("New QR code issued"); }
      catch (e) { notifyError(e); }
    },
  });
  if (!canManage) return <Text c="dimmed">Only managers can view QR codes.</Text>;
  return (
    <Stack align="flex-start">
      <Image src={url(`${base}/qr.svg`, { v })} w={240} h={240} alt="QR code" />
      <Text size="sm" c="dimmed">The code is signed by the server. It only works together with this person's face.</Text>
      <Group>
        <Button variant="default" leftSection={<IconPrinter size={16} />} component="a" href={`/spaces/${space.id}/cards?ids=${member.id}`} target="_blank">Print card</Button>
        <Button variant="subtle" color="red" onClick={reissue}>Issue new code</Button>
      </Group>
    </Stack>
  );
}

function MemberSchedule({ space, member, canManage }: { space: Space; member: Member; canManage: boolean }) {
  const qc = useQueryClient();
  const shifts = useQuery({ queryKey: ["shifts", space.id], queryFn: () => api<Shift[]>(`/spaces/${space.id}/shifts`) });
  const assignments = useQuery({
    queryKey: ["assign", member.id],
    queryFn: () => api<{ id: string; shift: string; valid_from: string }[]>(`/spaces/${space.id}/members/${member.id}/shift-assignments`),
  });
  const leaves = useQuery({
    queryKey: ["leaves", space.id],
    queryFn: () => api<{ id: string; member_id: string; start_date: string; end_date: string; kind: string; note: string }[]>(`/spaces/${space.id}/leaves`),
  });
  const [shift, setShift] = useState<string | null>(null);
  const [from, setFrom] = useState<string | null>(today());
  const [leave, setLeave] = useState<{ start: string | null; end: string | null; kind: string }>({ start: today(), end: today(), kind: "leave" });
  const defaultShift = shifts.data?.find((s) => s.id === space.default_shift_id);
  const assign = async () => {
    try {
      await api(`/spaces/${space.id}/shift-assignments`, { json: { member_ids: [member.id], shift_id: shift, valid_from: from } });
      qc.invalidateQueries({ queryKey: ["assign", member.id] }); notifyOk("Shift assigned");
    } catch (e) { notifyError(e); }
  };
  const addLeave = async () => {
    try {
      await api(`/spaces/${space.id}/leaves`, { json: { member_id: member.id, start_date: leave.start, end_date: leave.end, kind: leave.kind } });
      qc.invalidateQueries({ queryKey: ["leaves", space.id] }); notifyOk("Leave added");
    } catch (e) { notifyError(e); }
  };
  const myLeaves = (leaves.data ?? []).filter((l) => l.member_id === member.id);
  return (
    <Stack>
      <Text fw={600}>Shift</Text>
      {assignments.data?.length ? (
        <List size="sm">{assignments.data.map((a) => <List.Item key={a.id}>{a.shift} from {a.valid_from}</List.Item>)}</List>
      ) : <Text size="sm" c="dimmed">Uses the space default{defaultShift ? `: ${defaultShift.name} (${defaultShift.start_time}–${defaultShift.end_time})` : " (none set)"}.</Text>}
      {canManage && (
        <Group align="flex-end">
          <Select label="Change to" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: `${s.name} ${s.start_time}–${s.end_time}` }))} value={shift} onChange={setShift} w={240} />
          <DateInput label="Starting" value={from} onChange={setFrom} w={150} />
          <Button variant="default" onClick={assign} disabled={!shift || !from}>Assign</Button>
        </Group>
      )}
      <Text fw={600} mt="md">Leave</Text>
      {myLeaves.length ? (
        <List size="sm">{myLeaves.map((l) => <List.Item key={l.id}>{l.kind}: {l.start_date} to {l.end_date}</List.Item>)}</List>
      ) : <Text size="sm" c="dimmed">No leave recorded.</Text>}
      {canManage && (
        <Group align="flex-end">
          <DateInput label="From" value={leave.start} onChange={(v) => setLeave({ ...leave, start: v })} w={140} />
          <DateInput label="To" value={leave.end} onChange={(v) => setLeave({ ...leave, end: v })} w={140} />
          <Select label="Type" data={["leave", "sick", "vacation", "duty", "excused"]} value={leave.kind} onChange={(v) => setLeave({ ...leave, kind: v ?? "leave" })} w={130} />
          <Button variant="default" onClick={addLeave} disabled={!leave.start || !leave.end}>Add leave</Button>
        </Group>
      )}
    </Stack>
  );
}

function ImportModal({ opened, onClose, spaceId }: { opened: boolean; onClose: () => void; spaceId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const run = useMutation({
    mutationFn: (dry: boolean) => {
      const fd = new FormData();
      fd.append("file", file!);
      return api<ImportReport>(`/spaces/${spaceId}/import`, { form: fd, query: { dry_run: dry } });
    },
    onSuccess: (r) => { setReport(r); if (!r.dry_run) notifyOk(`Imported: ${r.created} added, ${r.updated} updated`); },
    onError: notifyError,
  });
  const close = () => { setFile(null); setReport(null); onClose(); };
  return (
    <Modal opened={opened} onClose={close} title="Import people from a spreadsheet" size="lg">
      <Stack>
        <Text size="sm">
          Columns can be named like <b>Name</b>, <b>Employee ID</b> or <b>Roll no</b>, <b>Designation</b>, <b>Email</b>, <b>Phone</b> plus any custom field.
          People with an existing ID are updated. <Anchor href={url(`/spaces/${spaceId}/import/template.xlsx`)}>Download a template</Anchor>
        </Text>
        <Dropzone onDrop={(f) => { setFile(f[0]); setReport(null); }} multiple={false}
          accept={[".xlsx", ".csv", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]}>
          <Group justify="center" mih={70}><IconFileSpreadsheet size={22} /><Text>{file ? file.name : "Drop an .xlsx or .csv file"}</Text></Group>
        </Dropzone>
        {report && (
          <Alert color={report.errors.length ? "orange" : "teal"} variant="light" title={report.dry_run ? "Check before importing" : "Import finished"}>
            <Text size="sm">{report.total} rows: {report.created} new, {report.updated} updated, {report.errors.length} with problems.</Text>
            {report.errors.slice(0, 12).map((e) => <Text key={e.row} size="sm">Row {e.row}: {e.error}</Text>)}
            {report.errors.length > 12 && <Text size="sm">…and {report.errors.length - 12} more</Text>}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={close}>Close</Button>
          {(!report || !report.dry_run) ? (
            <Button disabled={!file} loading={run.isPending} onClick={() => run.mutate(true)}>Check file</Button>
          ) : (
            <Button loading={run.isPending} disabled={report.created + report.updated === 0} onClick={() => run.mutate(false)}>
              Import {report.created + report.updated} rows
            </Button>
          )}
        </Group>
      </Stack>
    </Modal>
  );
}

function ZipModal({ opened, onClose, spaceId }: { opened: boolean; onClose: () => void; spaceId: string }) {
  const [results, setResults] = useState<FaceResult[] | null>(null);
  const run = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api<FaceResult[]>(`/spaces/${spaceId}/faces/bulk`, { form: fd }); },
    onSuccess: setResults,
    onError: notifyError,
  });
  const ok = results?.filter((r) => r.ok).length ?? 0;
  return (
    <Modal opened={opened} onClose={() => { setResults(null); onClose(); }} title="Upload face photos in bulk" size="lg">
      <Stack>
        <Text size="sm">Put photos in a zip file, named by each person's ID: <b>1001.jpg</b>, <b>1001_2.jpg</b>, <b>1002.png</b>.</Text>
        <Dropzone onDrop={(f) => run.mutate(f[0])} multiple={false} loading={run.isPending} accept={["application/zip", ".zip", "application/x-zip-compressed"]}>
          <Group justify="center" mih={70}><IconUpload size={22} /><Text>Drop the zip file here</Text></Group>
        </Dropzone>
        {results && (
          <Alert color={ok === results.length ? "teal" : "orange"} variant="light" title={`${ok} of ${results.length} photos added`}>
            {results.filter((r) => !r.ok).slice(0, 20).map((r) => <Text key={r.file} size="sm">{r.file}: {faceError(r.error)}</Text>)}
          </Alert>
        )}
      </Stack>
    </Modal>
  );
}

function AssignModal({ opened, onClose, spaceId, memberIds }: { opened: boolean; onClose: () => void; spaceId: string; memberIds: string[] }) {
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`), enabled: opened });
  const [shift, setShift] = useState<string | null>(null);
  const [from, setFrom] = useState<string | null>(today());
  const run = useMutation({
    mutationFn: () => api<{ assigned: number }>(`/spaces/${spaceId}/shift-assignments`, { json: { member_ids: memberIds, shift_id: shift, valid_from: from } }),
    onSuccess: (r) => { notifyOk(`Shift assigned to ${r.assigned} people`); onClose(); },
    onError: notifyError,
  });
  return (
    <Modal opened={opened} onClose={onClose} title={`Assign a shift to ${memberIds.length} people`}>
      <Stack>
        <Select label="Shift" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: `${s.name} ${s.start_time}–${s.end_time}` }))} value={shift} onChange={setShift} />
        <DateInput label="Starting on" value={from} onChange={setFrom} />
        <Button onClick={() => run.mutate()} disabled={!shift || !from} loading={run.isPending}>Assign shift</Button>
      </Stack>
    </Modal>
  );
}
