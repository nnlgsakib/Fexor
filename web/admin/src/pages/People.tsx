import {
  ActionIcon, Alert, Anchor, Badge, Button, Checkbox, Drawer, Group, Image, Menu, Modal, NumberInput, Pagination,
  Paper, SegmentedControl, Select, SimpleGrid, Stack, Table, Tabs, Text, TextInput,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { Dropzone, type FileRejection } from "@mantine/dropzone";
import { useDebouncedValue, useDisclosure } from "@mantine/hooks";
import {
  IconChevronDown, IconDownload, IconFileSpreadsheet, IconPhotoPlus, IconPlus, IconPrinter, IconSearch, IconTrash, IconUpload,
} from "@tabler/icons-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, url } from "../api";
import type { FieldDef, Member, Shift, Space } from "../types";
import { PageHeader, QueryState, confirmAction, fmtDate, notifyError, notifyOk, plural, useSpace } from "../ui";

const PAGE = 50;
type ImportReport = { total: number; created: number; updated: number; errors: { row: number; error: string }[]; dry_run: boolean };
type FaceResult = { file: string; ok: boolean; error?: string };
type Leave = { id: string; member_id: string; start_date: string; end_date: string; kind: string; note: string };

const FACE_ERRORS: Record<string, string> = {
  no_face: "No face found",
  multiple_faces: "More than one face",
  face_too_small: "Face too small, use a closer photo",
  blurry: "Photo is blurry",
  too_dark: "Photo is too dark",
  too_bright: "Photo is too bright",
  not_frontal: "Face is turned, use a front-facing photo",
  not_an_image: "Not an image, or the image is damaged",
  too_many_photos: "Already has 10 photos",
  no_member_with_this_id: "No person with this ID",
  no_consent: "Consent not recorded for this person",
  file_too_large: "File too large",
};
const faceError = (e?: string) => (e ?? "").split(",").map((x) => FACE_ERRORS[x] ?? x).join("; ");

/** Why the drop zone refused a file, in plain words (iPhone photos are HEIC). */
function rejectionText(r: FileRejection[]) {
  return r.map(({ file, errors }) => {
    const code = errors[0]?.code;
    const why = code === "file-too-large" ? "is larger than 15 MB"
      : code === "file-invalid-type" ? "is not a JPG, PNG or WebP image (iPhone photos: export them as JPG)"
      : errors[0]?.message ?? "can't be used";
    return `${file.name} ${why}`;
  }).join(". ");
}

export function People() {
  const { spaceId, space, spaceQuery, canManage } = useSpace();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 300);
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ member: Member | "new"; tab?: string } | null>(null);
  const [importOpen, importCtl] = useDisclosure();
  const [zipOpen, zipCtl] = useDisclosure();
  const [assignOpen, assignCtl] = useDisclosure();

  const q = useQuery({
    queryKey: ["members", spaceId, debounced, active, page],
    queryFn: () => api<{ items: Member[]; total: number }>(`/spaces/${spaceId}/members`,
      { query: { q: debounced, active: active === "all" ? undefined : active, page, size: PAGE } }),
    placeholderData: keepPreviousData,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members", spaceId] });
  const fields = (space?.field_schema ?? []).slice(0, 2);
  const items = q.data?.items ?? [];
  const label = space?.member_label ?? "Person";
  const pageSelected = items.length > 0 && items.every((m) => selected.includes(m.id));

  return (
    <>
      <PageHeader
        title="People"
        description={`${plural(q.data?.total ?? 0, label.toLowerCase())}. Each person needs at least one clear face photo and a QR card to use the kiosk.`}
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
            <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing({ member: "new" })}>Add {label.toLowerCase()}</Button>
          </>
        )}
      />
      <Group mb="md" wrap="wrap">
        <TextInput placeholder="Search name, ID or title" aria-label="Search people" leftSection={<IconSearch size={16} />}
          value={search} w={300} maw="100%" onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} />
        <SegmentedControl value={active} onChange={(v) => { setActive(v); setPage(1); }} aria-label="Show"
          data={[{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }, { value: "all", label: "All" }]} />
        {selected.length > 0 && (
          <Group gap="xs">
            <Text size="sm">{selected.length} selected</Text>
            <Anchor component="button" size="sm" onClick={() => setSelected([])}>Clear</Anchor>
          </Group>
        )}
      </Group>
      <QueryState q={q}>
        <Paper withBorder>
          <Table.ScrollContainer minWidth={760}>
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={40}>
                    <Checkbox aria-label="Select everyone on this page" checked={pageSelected}
                      onChange={(e) => {
                        const ids = items.map((m) => m.id);
                        setSelected(e.currentTarget.checked ? [...new Set([...selected, ...ids])] : selected.filter((x) => !ids.includes(x)));
                      }} />
                  </Table.Th>
                  <Table.Th>Name</Table.Th><Table.Th>ID</Table.Th><Table.Th>Title</Table.Th>
                  {fields.map((f) => <Table.Th key={f.key}>{f.label}</Table.Th>)}
                  <Table.Th>Face photos</Table.Th><Table.Th>QR</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {items.map((m) => (
                  <Table.Tr key={m.id} style={{ cursor: "pointer" }} onClick={() => setEditing({ member: m })} opacity={m.active ? 1 : 0.55}
                    tabIndex={0} aria-label={`Open ${m.full_name}`}
                    onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) setEditing({ member: m }); }}>
                    <Table.Td onClick={(e) => e.stopPropagation()}>
                      <Checkbox aria-label={`Select ${m.full_name}`} checked={selected.includes(m.id)}
                        onChange={(e) => setSelected(e.currentTarget.checked ? [...selected, m.id] : selected.filter((x) => x !== m.id))} />
                    </Table.Td>
                    <Table.Td fw={500}>{m.full_name}{!m.active && <Badge ml={6} color="gray" variant="light" tt="none">Inactive</Badge>}</Table.Td>
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
          {items.length === 0 && (
            <Stack p="xl" align="flex-start">
              <Text c="dimmed">{debounced ? "Nobody matches that search."
                : active === "false" ? "No inactive people." : "No people here yet."}</Text>
              {canManage && !debounced && active !== "false" && (
                <Group><Button onClick={() => setEditing({ member: "new" })}>Add one person</Button>
                  <Button variant="default" onClick={importCtl.open}>Import a spreadsheet</Button></Group>
              )}
            </Stack>
          )}
        </Paper>
        {(q.data?.total ?? 0) > PAGE && <Pagination mt="md" total={Math.ceil((q.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} />}
      </QueryState>
      {spaceQuery.isError && <Text c="red" mt="md">This space could not be loaded.</Text>}

      {space && editing && (
        <MemberDrawer key={editing.member === "new" ? "new" : editing.member.id} space={space} member={editing.member}
          initialTab={editing.tab} onClose={() => setEditing(null)} canManage={canManage}
          onChanged={refresh} onCreated={(m) => { refresh(); setEditing({ member: m, tab: "photos" }); }} />
      )}
      <ImportModal opened={importOpen} onClose={() => { importCtl.close(); refresh(); }} spaceId={spaceId} />
      <ZipModal opened={zipOpen} onClose={() => { zipCtl.close(); refresh(); }} spaceId={spaceId} />
      <AssignModal opened={assignOpen} onClose={assignCtl.close} spaceId={spaceId} memberIds={selected}
        onDone={() => { setSelected([]); qc.invalidateQueries({ queryKey: ["assign"] }); }} />
    </>
  );
}

function FieldInput({ f, value, onChange, disabled }: { f: FieldDef; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  if (f.type === "select") return <Select label={f.label} required={f.required} data={f.options} value={(value as string) ?? null} onChange={onChange} clearable disabled={disabled} />;
  if (f.type === "number") return <NumberInput label={f.label} required={f.required} value={(value as number) ?? ""} onChange={onChange} disabled={disabled} />;
  if (f.type === "date") return <DateInput label={f.label} required={f.required} value={(value as string) ?? null} onChange={onChange} clearable disabled={disabled} />;
  return <TextInput label={f.label} required={f.required} value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} disabled={disabled} />;
}

export type Form = Record<string, unknown> & { fields: Record<string, unknown> };

function formFor(m: Member | null, today: string): Form {
  return m ? {
    full_name: m.full_name, external_id: m.external_id, title: m.title, email: m.email ?? "", phone: m.phone ?? "",
    fields: { ...m.fields }, start_date: m.start_date, end_date: m.end_date, consent: !!m.consent_at,
  } : { full_name: "", external_id: "", title: "", email: "", phone: "", fields: {}, start_date: today, consent: false };
}

/** Only what was changed: re-sending untouched values could fail newer rules or move dates. */
export function changes(before: Form, after: Form): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(after)) {
    if (k === "fields") continue;
    if (JSON.stringify(v ?? null) !== JSON.stringify(before[k] ?? null)) out[k] = v;
  }
  const fields: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(after.fields)) {
    if (JSON.stringify(v ?? null) !== JSON.stringify(before.fields[k] ?? null)) fields[k] = v;
  }
  if (Object.keys(fields).length) out.fields = fields;
  return out;
}

function MemberDrawer({ space, member, initialTab, onClose, onChanged, onCreated, canManage }: {
  space: Space; member: Member | "new"; initialTab?: string; onClose: () => void; onChanged: () => void;
  onCreated: (m: Member) => void; canManage: boolean;
}) {
  const { today } = useSpace();
  const [m, setM] = useState<Member | null>(member === "new" ? null : member);
  const [initial, setInitial] = useState<Form>(() => formFor(m, today));
  const [form, setForm] = useState<Form>(initial);
  const [tab, setTab] = useState<string | null>(initialTab ?? "details");
  const diff = changes(initial, form);
  const ready = String(form.full_name ?? "").trim() && String(form.external_id ?? "").trim();

  const save = useMutation({
    mutationFn: () => m
      ? api<Member>(`/spaces/${space.id}/members/${m.id}`, { method: "PATCH", json: diff })
      : api<Member>(`/spaces/${space.id}/members`, { json: { ...form, email: form.email || null, phone: form.phone || null } }),
    onSuccess: (saved) => {
      if (!m) { notifyOk("Person added. Next: add a face photo."); onCreated(saved); return; }
      notifyOk("Changes saved");
      setM(saved);
      const f = formFor(saved, today);
      setInitial(f);
      setForm(f);
      onChanged();
    },
    onError: notifyError,
  });
  const setActive = (active: boolean, erase = false) => confirmAction({
    title: erase ? "Erase this person's data?" : active ? "Reactivate this person?" : "Deactivate this person?",
    text: erase
      ? "Their face photos, QR code and attendance in this space are deleted permanently. This cannot be undone."
      : active ? "They can check in again from today."
      : "They can no longer check in, and they are not marked absent from tomorrow. Their attendance history is kept, and you can reactivate them later.",
    confirm: erase ? "Erase permanently" : active ? "Reactivate" : "Deactivate",
    danger: !active,
    onConfirm: async () => {
      try {
        if (active) {
          const saved = await api<Member>(`/spaces/${space.id}/members/${m!.id}`, { method: "PATCH", json: { active: true } });
          setM(saved);
          notifyOk("Reactivated");
          onChanged();
          return;
        }
        await api(`/spaces/${space.id}/members/${m!.id}`, { method: "DELETE", query: { erase } });
        notifyOk(erase ? "Data erased" : "Deactivated");
        onChanged();
        onClose();
      } catch (e) { notifyError(e); }
    },
  });
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const setField = (k: string, v: unknown) => setForm((f) => ({ ...f, fields: { ...f.fields, [k]: v } }));

  return (
    <Drawer opened onClose={onClose} position="right" size="lg"
      title={<Text fw={650} fz="lg">{m ? m.full_name : `Add ${space.member_label.toLowerCase()}`}</Text>}>
      {m && !m.active && <Alert color="gray" variant="light" mb="md">Inactive{m.end_date ? ` since ${fmtDate(m.end_date)}` : ""}. This person can't check in.</Alert>}
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List mb="md">
          <Tabs.Tab value="details">Details</Tabs.Tab>
          {m && <Tabs.Tab value="photos">Face photos{m.face_count ? "" : " ●"}</Tabs.Tab>}
          {m && <Tabs.Tab value="qr">QR card</Tabs.Tab>}
          {m && <Tabs.Tab value="schedule">Shift and leave</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="details">
          <Stack>
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <TextInput label="Full name" required value={(form.full_name as string) ?? ""} onChange={(e) => set("full_name", e.currentTarget.value)} disabled={!canManage} />
              <TextInput label={space.mode === "single" ? "Roll / student ID" : "Employee ID"} required value={(form.external_id as string) ?? ""}
                onChange={(e) => set("external_id", e.currentTarget.value)} disabled={!canManage} />
            </SimpleGrid>
            <TextInput label={space.mode === "single" ? "Class or grade" : "Designation"} value={(form.title as string) ?? ""} onChange={(e) => set("title", e.currentTarget.value)} disabled={!canManage} />
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <TextInput label="Email" type="email" value={(form.email as string) ?? ""} onChange={(e) => set("email", e.currentTarget.value)} disabled={!canManage} />
              <TextInput label="Phone" type="tel" value={(form.phone as string) ?? ""} onChange={(e) => set("phone", e.currentTarget.value)} disabled={!canManage} />
            </SimpleGrid>
            {space.field_schema.length > 0 && (
              <SimpleGrid cols={{ base: 1, xs: 2 }}>
                {space.field_schema.map((f) => (
                  <FieldInput key={f.key} f={f} value={form.fields[f.key]} onChange={(v) => setField(f.key, v)} disabled={!canManage} />
                ))}
              </SimpleGrid>
            )}
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <DateInput label="Starts on" description="No absences before this day" value={(form.start_date as string) ?? null}
                onChange={(v) => set("start_date", v)} clearable disabled={!canManage} valueFormat="D MMM YYYY" />
              <DateInput label="Leaves on" description="Last day they are expected" value={(form.end_date as string) ?? null}
                onChange={(v) => set("end_date", v)} clearable disabled={!canManage} valueFormat="D MMM YYYY"
                minDate={(form.start_date as string) || undefined} />
            </SimpleGrid>
            <Checkbox label="This person agreed to face recognition (needed before adding face photos)"
              description={m?.consent_at ? `Recorded on ${fmtDate(m.consent_at)}` : undefined}
              checked={!!form.consent} onChange={(e) => set("consent", e.currentTarget.checked)} disabled={!canManage} />
            {canManage && (
              <Group justify="space-between" mt="sm">
                <Group gap="xs">
                  {m?.active && <Button variant="subtle" color="red" onClick={() => setActive(false)}>Deactivate</Button>}
                  {m && !m.active && <Button variant="light" onClick={() => setActive(true)}>Reactivate</Button>}
                  {m && space.role === "owner" && <Button variant="subtle" color="red" onClick={() => setActive(false, true)}>Erase data</Button>}
                </Group>
                <Button onClick={() => save.mutate()} loading={save.isPending}
                  disabled={!ready || (!!m && Object.keys(diff).length === 0)}>{m ? "Save changes" : "Add person"}</Button>
              </Group>
            )}
          </Stack>
        </Tabs.Panel>
        {m && <Tabs.Panel value="photos"><Photos space={space} member={m} canManage={canManage}
          onChanged={(saved) => { if (saved) { setM(saved); const f = formFor(saved, today); setInitial(f); setForm(f); } onChanged(); }} /></Tabs.Panel>}
        {m && <Tabs.Panel value="qr"><QrPanel space={space} member={m} canManage={canManage} onChanged={onChanged} /></Tabs.Panel>}
        {m && <Tabs.Panel value="schedule"><MemberSchedule space={space} member={m} canManage={canManage} /></Tabs.Panel>}
      </Tabs>
    </Drawer>
  );
}

function Photos({ space, member, canManage, onChanged }: {
  space: Space; member: Member; canManage: boolean; onChanged: (saved?: Member) => void;
}) {
  const qc = useQueryClient();
  const base = `/spaces/${space.id}/members/${member.id}/faces`;
  const faces = useQuery({ queryKey: ["faces", member.id], queryFn: () => api<{ id: string; has_photo: boolean; created_at: string }[]>(base) });
  const [results, setResults] = useState<FaceResult[]>([]);
  const [rejected, setRejected] = useState("");
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      return api<FaceResult[]>(base, { form: fd });
    },
    onSuccess: (r) => {
      setResults(r);
      const ok = r.filter((x) => x.ok).length;
      if (ok) notifyOk(`${plural(ok, "photo")} added`);
      qc.invalidateQueries({ queryKey: ["faces", member.id] });
      onChanged();
    },
    onError: notifyError,
  });
  const consent = useMutation({
    mutationFn: () => api<Member>(`/spaces/${space.id}/members/${member.id}`, { method: "PATCH", json: { consent: true } }),
    onSuccess: (saved) => { notifyOk("Consent recorded"); onChanged(saved); },
    onError: notifyError,
  });
  const del = (id: string) => confirmAction({
    title: "Delete this photo?", text: "The kiosk stops using it at its next sync.", confirm: "Delete photo",
    onConfirm: async () => {
      try { await api(`${base}/${id}`, { method: "DELETE" }); notifyOk("Photo deleted"); qc.invalidateQueries({ queryKey: ["faces", member.id] }); onChanged(); }
      catch (e) { notifyError(e); }
    },
  });
  if (!member.consent_at) {
    return (
      <Alert color="orange" variant="light" title="Consent needed first">
        <Stack gap="sm" align="flex-start">
          <Text size="sm">Face photos can only be added after this person agreed to face recognition. Record it once you have their
            consent (for a child: a parent's or guardian's).</Text>
          {canManage && <Button size="xs" loading={consent.isPending} onClick={() => consent.mutate()}>Consent was given</Button>}
        </Stack>
      </Alert>
    );
  }
  return (
    <Stack>
      <Text size="sm" c="dimmed">Use 2 or 3 clear, front-facing photos in normal light. Each photo is checked for quality before it is saved.</Text>
      {canManage && (
        <Dropzone onDrop={(f) => { setRejected(""); upload.mutate(f); }} onReject={(r) => setRejected(rejectionText(r))}
          accept={["image/jpeg", "image/png", "image/webp"]} loading={upload.isPending} maxSize={15 * 1024 ** 2}>
          <Group justify="center" mih={90}><IconUpload size={22} /><Text>Drop photos here or click to choose</Text></Group>
        </Dropzone>
      )}
      {rejected && <Alert color="orange" variant="light">{rejected}</Alert>}
      {results.filter((r) => !r.ok).map((r) => <Alert key={r.file} color="orange" variant="light">{r.file}: {faceError(r.error)}</Alert>)}
      <QueryState q={faces}>
        <SimpleGrid cols={{ base: 2, xs: 3 }}>
          {faces.data?.map((f) => (
            <Paper key={f.id} withBorder p={4} pos="relative">
              {f.has_photo && canManage ? <Image src={url(`${base}/${f.id}/photo`)} radius="sm" h={140} fit="cover" alt="Enrolled face" />
                : <Stack h={140} justify="center" align="center"><Text size="xs" c="dimmed">{f.has_photo ? "Photo on file" : "Photo not kept"}</Text></Stack>}
              {canManage && (
                <ActionIcon color="red" variant="filled" size="md" pos="absolute" top={8} right={8} aria-label="Delete photo" onClick={() => del(f.id)}>
                  <IconTrash size={16} />
                </ActionIcon>
              )}
            </Paper>
          ))}
        </SimpleGrid>
        {faces.data?.length === 0 && <Text c="dimmed">No face photos yet. This person can't use the kiosk until one is added.</Text>}
      </QueryState>
    </Stack>
  );
}

function QrPanel({ space, member, canManage, onChanged }: { space: Space; member: Member; canManage: boolean; onChanged: () => void }) {
  const [v, setV] = useState(0);
  const base = `/spaces/${space.id}/members/${member.id}`;
  const reissue = () => confirmAction({
    title: "Issue a new QR code?",
    text: "The old card stops working on every kiosk within a minute. Use this if a card was lost or shared.",
    confirm: "Issue new code",
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
        <Button variant="default" leftSection={<IconDownload size={16} />} component="a" href={url(`${base}/qr.png`, { v })}>Download PNG</Button>
        <Button variant="subtle" color="red" onClick={reissue}>Issue new code</Button>
      </Group>
    </Stack>
  );
}

function MemberSchedule({ space, member, canManage }: { space: Space; member: Member; canManage: boolean }) {
  const { today } = useSpace();
  const qc = useQueryClient();
  const shifts = useQuery({ queryKey: ["shifts", space.id], queryFn: () => api<Shift[]>(`/spaces/${space.id}/shifts`) });
  const assignments = useQuery({
    queryKey: ["assign", member.id],
    queryFn: () => api<{ id: string; shift: string; valid_from: string }[]>(`/spaces/${space.id}/members/${member.id}/shift-assignments`),
  });
  const leaves = useQuery({
    queryKey: ["leaves", space.id, member.id],
    queryFn: () => api<Leave[]>(`/spaces/${space.id}/leaves`, { query: { member_id: member.id } }),
  });
  const [shift, setShift] = useState<string | null>(null);
  const [from, setFrom] = useState<string | null>(today);
  const [leave, setLeave] = useState<{ start: string | null; end: string | null; kind: string }>({ start: today, end: today, kind: "leave" });
  const defaultShift = shifts.data?.find((s) => s.id === space.default_shift_id);
  const assign = useMutation({
    mutationFn: () => api(`/spaces/${space.id}/shift-assignments`, { json: { member_ids: [member.id], shift_id: shift, valid_from: from } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["assign", member.id] }); notifyOk("Shift assigned"); setShift(null); },
    onError: notifyError,
  });
  const addLeave = useMutation({
    mutationFn: () => api(`/spaces/${space.id}/leaves`, { json: { member_id: member.id, start_date: leave.start, end_date: leave.end, kind: leave.kind } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["leaves", space.id, member.id] }); notifyOk("Leave added"); },
    onError: notifyError,
  });
  const removeLeave = (l: Leave) => confirmAction({
    title: "Remove this leave?", text: `${l.kind}, ${fmtDate(l.start_date)} to ${fmtDate(l.end_date)}. Those days are recounted.`,
    confirm: "Remove leave",
    onConfirm: async () => {
      try { await api(`/spaces/${space.id}/leaves/${l.id}`, { method: "DELETE" }); notifyOk("Leave removed"); qc.invalidateQueries({ queryKey: ["leaves", space.id, member.id] }); }
      catch (e) { notifyError(e); }
    },
  });
  const removeAssignment = (a: { id: string; shift: string; valid_from: string }) => confirmAction({
    title: "Remove this shift change?", text: `${a.shift} from ${fmtDate(a.valid_from)}. Days from then on use the previous shift again.`,
    confirm: "Remove",
    onConfirm: async () => {
      try { await api(`/spaces/${space.id}/shift-assignments/${a.id}`, { method: "DELETE" }); notifyOk("Shift change removed"); qc.invalidateQueries({ queryKey: ["assign", member.id] }); }
      catch (e) { notifyError(e); }
    },
  });
  return (
    <Stack>
      <Text fw={600}>Shift</Text>
      <Text size="sm" c="dimmed">Space default{defaultShift ? `: ${defaultShift.name} (${defaultShift.start_time}–${defaultShift.end_time})` : ": none set"}.</Text>
      {assignments.data?.map((a) => (
        <Group key={a.id} justify="space-between" wrap="nowrap">
          <Text size="sm">{a.shift} from {fmtDate(a.valid_from)}</Text>
          {canManage && <ActionIcon variant="subtle" color="red" aria-label={`Remove ${a.shift} from ${a.valid_from}`} onClick={() => removeAssignment(a)}><IconTrash size={16} /></ActionIcon>}
        </Group>
      ))}
      {canManage && (
        <Group align="flex-end" wrap="wrap">
          <Select label="Change to" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: `${s.name} ${s.start_time}–${s.end_time}` }))} value={shift} onChange={setShift} w={240} />
          <DateInput label="Starting" value={from} onChange={setFrom} w={150} valueFormat="D MMM YYYY" />
          <Button variant="default" onClick={() => assign.mutate()} loading={assign.isPending} disabled={!shift || !from}>Assign</Button>
        </Group>
      )}
      <Text fw={600} mt="md">Leave</Text>
      {leaves.data?.length ? leaves.data.map((l) => (
        <Group key={l.id} justify="space-between" wrap="nowrap">
          <Text size="sm">{l.kind}: {fmtDate(l.start_date)}{l.end_date !== l.start_date ? ` to ${fmtDate(l.end_date)}` : ""}</Text>
          {canManage && <ActionIcon variant="subtle" color="red" aria-label={`Remove leave from ${l.start_date}`} onClick={() => removeLeave(l)}><IconTrash size={16} /></ActionIcon>}
        </Group>
      )) : <Text size="sm" c="dimmed">No leave recorded.</Text>}
      {canManage && (
        <Group align="flex-end" wrap="wrap">
          <DateInput label="From" value={leave.start} valueFormat="D MMM YYYY" w={140}
            onChange={(v) => setLeave({ ...leave, start: v, end: leave.end && v && leave.end < v ? v : leave.end })} />
          <DateInput label="To" value={leave.end} onChange={(v) => setLeave({ ...leave, end: v })} w={140} valueFormat="D MMM YYYY"
            minDate={leave.start ?? undefined} />
          <Select label="Type" data={["leave", "sick", "vacation", "duty", "excused"]} value={leave.kind} onChange={(v) => setLeave({ ...leave, kind: v ?? "leave" })} w={130} />
          <Button variant="default" onClick={() => addLeave.mutate()} loading={addLeave.isPending} disabled={!leave.start || !leave.end}>Add leave</Button>
        </Group>
      )}
    </Stack>
  );
}

function ImportModal({ opened, onClose, spaceId }: { opened: boolean; onClose: () => void; spaceId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [rejected, setRejected] = useState("");
  const run = useMutation({
    mutationFn: (dry: boolean) => {
      const fd = new FormData();
      fd.append("file", file!);
      return api<ImportReport>(`/spaces/${spaceId}/import`, { form: fd, query: { dry_run: dry } });
    },
    onSuccess: (r) => { setReport(r); if (!r.dry_run) notifyOk(`Imported: ${r.created} added, ${r.updated} updated`); },
    onError: notifyError,
  });
  const close = () => { setFile(null); setReport(null); setRejected(""); onClose(); };
  return (
    <Modal opened={opened} onClose={close} title="Import people from a spreadsheet" size="lg">
      <Stack>
        <Text size="sm">
          Columns can be named like <b>Name</b>, <b>Employee ID</b> or <b>Roll no</b>, <b>Designation</b>, <b>Email</b>, <b>Phone</b> plus any custom field.
          People with an existing ID are updated. <Anchor href={url(`/spaces/${spaceId}/import/template.xlsx`)}>Download a template</Anchor>
        </Text>
        <Dropzone onDrop={(f) => { setFile(f[0]); setReport(null); setRejected(""); }} multiple={false}
          onReject={() => setRejected("Use an .xlsx or .csv file. Old .xls files: open them in Excel and save as .xlsx.")}
          accept={[".xlsx", ".csv", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]}>
          <Group justify="center" mih={70}><IconFileSpreadsheet size={22} /><Text>{file ? file.name : "Drop an .xlsx or .csv file"}</Text></Group>
        </Dropzone>
        {rejected && <Alert color="orange" variant="light">{rejected}</Alert>}
        {report && (
          <Alert color={report.errors.length ? "orange" : "teal"} variant="light" title={report.dry_run ? "Check before importing" : "Import finished"}>
            <Text size="sm">{report.total} rows: {report.created} new, {report.updated} updated, {report.errors.length} with problems.</Text>
            {report.dry_run && report.errors.length > 0 && <Text size="sm" mt={4}>Rows with problems are skipped. Fix them in the file and import again.</Text>}
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
              Import {plural(report.created + report.updated, "row")}
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
  const failed = results?.filter((r) => !r.ok) ?? [];
  return (
    <Modal opened={opened} onClose={() => { setResults(null); onClose(); }} title="Upload face photos in bulk" size="lg">
      <Stack>
        <Text size="sm">Put photos in a zip file, named by each person's ID: <b>1001.jpg</b>, <b>1001_2.jpg</b>, <b>1002.png</b>.
          Up to 500 photos per zip. People need recorded consent first.</Text>
        <Dropzone onDrop={(f) => run.mutate(f[0])} multiple={false} loading={run.isPending} accept={["application/zip", ".zip", "application/x-zip-compressed"]}>
          <Group justify="center" mih={70}><IconUpload size={22} /><Text>{run.isPending ? "Checking photos, this can take a few minutes…" : "Drop the zip file here"}</Text></Group>
        </Dropzone>
        {results && (
          <Alert color={ok === results.length ? "teal" : "orange"} variant="light" title={`${ok} of ${plural(results.length, "photo")} added`}>
            {failed.slice(0, 20).map((r) => <Text key={r.file} size="sm">{r.file}: {faceError(r.error)}</Text>)}
            {failed.length > 20 && <Text size="sm">…and {failed.length - 20} more</Text>}
          </Alert>
        )}
      </Stack>
    </Modal>
  );
}

function AssignModal({ opened, onClose, onDone, spaceId, memberIds }: {
  opened: boolean; onClose: () => void; onDone: () => void; spaceId: string; memberIds: string[];
}) {
  const { today } = useSpace();
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`), enabled: opened });
  const [shift, setShift] = useState<string | null>(null);
  const [from, setFrom] = useState<string | null>(today);
  useEffect(() => { if (opened) setFrom(today); }, [opened, today]);
  const run = useMutation({
    mutationFn: () => api<{ assigned: number }>(`/spaces/${spaceId}/shift-assignments`, { json: { member_ids: memberIds, shift_id: shift, valid_from: from } }),
    onSuccess: (r) => { notifyOk(`Shift assigned to ${plural(r.assigned, "person", "people")}`); onDone(); onClose(); },
    onError: notifyError,
  });
  return (
    <Modal opened={opened} onClose={onClose} title={`Assign a shift to ${plural(memberIds.length, "person", "people")}`}>
      <Stack>
        <Select label="Shift" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: `${s.name} ${s.start_time}–${s.end_time}` }))} value={shift} onChange={setShift} />
        <DateInput label="Starting on" value={from} onChange={setFrom} valueFormat="D MMM YYYY" />
        <Button onClick={() => run.mutate()} disabled={!shift || !from} loading={run.isPending}>Assign shift</Button>
      </Stack>
    </Modal>
  );
}
