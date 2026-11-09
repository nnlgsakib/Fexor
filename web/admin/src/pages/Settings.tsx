import {
  ActionIcon, Alert, Badge, Button, Checkbox, Group, Modal, NumberInput, Paper, SegmentedControl, Select, SimpleGrid, Slider, Stack,
  TagsInput, Text, TextInput, Textarea, Title,
} from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api";
import { useMe } from "../auth";
import type { FieldDef, Shift, Space } from "../types";
import { PageHeader, QueryState, notifyError, notifyOk, useSpace } from "../ui";
import { timezones } from "./Spaces";

/** Field key from its label; labels in other scripts (Hindi, Arabic…) fall back to field_N. */
export function fieldKey(label: string, n: number) {
  const auto = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "f_$1").slice(0, 40);
  return auto || `field_${n}`;
}

const RULES: (keyof Space)[] = ["timezone", "mode", "default_shift_id", "min_rescan_minutes"];

export function Settings() {
  const { spaceId, space, spaceQuery, isOwner, today } = useSpace();
  const { me, refresh } = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState<Space | null>(null);
  const [deleting, setDeleting] = useState(false);
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`) });
  useEffect(() => { if (space) setF(structuredClone(space)); }, [space]);
  const dirty = !!f && !!space && JSON.stringify(f) !== JSON.stringify(space);
  const rulesChanged = !!f && !!space && RULES.some((k) => f[k] !== space[k]);

  const save = useMutation({
    mutationFn: () => {
      const { id: _id, role: _role, ...body } = f!;
      return api(`/spaces/${spaceId}`, { method: "PATCH", json: { ...body, country: body.country || null } });
    },
    onSuccess: () => {
      notifyOk(rulesChanged ? "Settings saved. New rules apply from today." : "Settings saved");
      qc.invalidateQueries({ queryKey: ["space", spaceId] });
      qc.invalidateQueries({ queryKey: ["spaces"] });
      refresh();
    },
    onError: notifyError,
  });

  if (!f) return <QueryState q={spaceQuery}>{null}</QueryState>;
  const set = <K extends keyof Space>(k: K, v: Space[K]) => setF({ ...f, [k]: v });
  const fields = f.field_schema;
  const setFieldDef = (i: number, patch: Partial<FieldDef>) => set("field_schema", fields.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const ro = !isOwner;
  const removedFields = (space?.field_schema ?? []).filter((s) => !fields.some((x) => x.key === s.key));
  const emptySelect = fields.some((x) => x.type === "select" && x.options.length === 0);
  const canSave = dirty && f.name.trim() && f.member_label.trim() && !emptySelect && fields.every((x) => x.label.trim());
  const saveButton = !ro && (
    <Group gap="sm">
      {dirty && <Badge color="yellow" variant="light" tt="none">Unsaved changes</Badge>}
      <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!canSave}>Save settings</Button>
    </Group>
  );

  return (
    <>
      <PageHeader title="Settings" description={ro ? "Only space owners can change these settings." : undefined} actions={saveButton} />
      <Stack gap="lg" maw={760}>
        <Paper withBorder p="lg">
          <Title order={4} mb="md">General</Title>
          <Stack>
            <TextInput label="Name" required value={f.name} onChange={(e) => set("name", e.currentTarget.value)} disabled={ro} maxLength={200} />
            <Textarea label="Description" value={f.description} onChange={(e) => set("description", e.currentTarget.value)} disabled={ro} autosize />
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <Select label="Time zone" data={timezones(f.timezone)} searchable value={f.timezone} onChange={(v) => v && set("timezone", v)} disabled={ro} />
              <TextInput label="What to call people" placeholder="Employee, Student" value={f.member_label} maxLength={40}
                onChange={(e) => set("member_label", e.currentTarget.value)} disabled={ro} />
            </SimpleGrid>
            <TextInput label="Country code for public holidays" placeholder="IN, US, GB" w={220} maxLength={3} value={f.country ?? ""}
              onChange={(e) => set("country", e.currentTarget.value.toUpperCase().trim() || null)} disabled={ro} />
            <div>
              <Text size="sm" fw={500} mb={4}>Attendance type</Text>
              <SegmentedControl value={f.mode} onChange={(v) => set("mode", v as Space["mode"])} disabled={ro}
                data={[{ value: "in_out", label: "Check in and out" }, { value: "single", label: "One check per day" }]} />
            </div>
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <Select label="Default shift" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: s.name }))}
                value={f.default_shift_id} onChange={(v) => set("default_shift_id", v)} clearable disabled={ro}
                placeholder="None: nobody is marked late or absent" />
              <NumberInput label="Ignore repeat scans within (minutes)" min={0} max={240} value={f.min_rescan_minutes}
                onChange={(v) => set("min_rescan_minutes", Number(v) || 0)} disabled={ro} />
            </SimpleGrid>
            {rulesChanged && (
              <Alert color="yellow" variant="light">
                Changes to the time zone, attendance type, default shift or repeat scans apply from today ({dayjs(today).format("D MMM")}).
                Past days keep how they were counted. To recount past days too, use <b>Recalculate past days</b> below after saving.
              </Alert>
            )}
          </Stack>
        </Paper>

        <Paper withBorder p="lg">
          <Title order={4} mb={4}>Verification at the kiosk</Title>
          <Text size="sm" c="dimmed" mb="md">Stricter settings stop more impostors but may ask honest people to try again more often.</Text>
          <Stack gap="lg">
            <div>
              <Text size="sm" fw={500}>Face match strictness: {f.match_threshold.toFixed(2)}</Text>
              <Slider min={0.36} max={0.6} step={0.01} value={f.match_threshold} onChange={(v) => set("match_threshold", v)} disabled={ro}
                label={(v) => v.toFixed(2)} marks={[{ value: 0.36, label: "Lenient" }, { value: 0.42, label: "Default" }, { value: 0.6, label: "Strict" }]} mb="md" />
            </div>
            <div>
              <Text size="sm" fw={500}>Photo and screen detection: {f.liveness_threshold.toFixed(2)}</Text>
              <Slider min={0.3} max={0.9} step={0.05} value={f.liveness_threshold} onChange={(v) => set("liveness_threshold", v)} disabled={ro}
                label={(v) => v.toFixed(2)} marks={[{ value: 0.3, label: "Lenient" }, { value: 0.6, label: "Default" }, { value: 0.9, label: "Strict" }]} mb="md" />
            </div>
            <NumberInput label="Movements to ask for (blink, turn, smile…)" description="0 turns the movement check off. 2 is a good balance."
              min={0} max={4} value={f.challenge_actions} onChange={(v) => set("challenge_actions", Number(v) || 0)} disabled={ro} w={320} maw="100%" />
            <Checkbox label="Keep a small face snapshot when a check fails, for review" checked={f.store_failure_snapshots}
              onChange={(e) => set("store_failure_snapshots", e.currentTarget.checked)} disabled={ro} />
            <Checkbox label="Keep enrollment photos" description="Needed to show photos here and to move to a newer face model later"
              checked={f.keep_enrollment_photos} onChange={(e) => set("keep_enrollment_photos", e.currentTarget.checked)} disabled={ro} />
          </Stack>
        </Paper>

        <Paper withBorder p="lg">
          <Group justify="space-between" mb="md">
            <Title order={4}>Extra fields for people</Title>
            {!ro && <Button size="xs" variant="default" leftSection={<IconPlus size={14} />}
              onClick={() => set("field_schema", [...fields, { key: "", label: "", type: "text", required: false, options: [] }])}>Add field</Button>}
          </Group>
          <Stack>
            {fields.length === 0 && <Text size="sm" c="dimmed">None. Add fields like department, class or blood group.</Text>}
            {fields.map((fd, i) => (
              <Group key={i} align="flex-end" wrap="wrap" gap="sm">
                <TextInput label="Label" value={fd.label} disabled={ro} style={{ flex: "1 1 180px" }} maxLength={100}
                  onChange={(e) => {
                    const label = e.currentTarget.value;
                    const saved = space?.field_schema.some((s) => s.key === fd.key);  // never rename a saved key
                    setFieldDef(i, { label, key: saved ? fd.key : fieldKey(label, i + 1) });
                  }} />
                <Select label="Type" w={120} data={["text", "number", "date", "select"]} value={fd.type} disabled={ro}
                  onChange={(v) => setFieldDef(i, { type: (v ?? "text") as FieldDef["type"] })} />
                {fd.type === "select" && <TagsInput label="Options" w={220} value={fd.options} onChange={(v) => setFieldDef(i, { options: v })} disabled={ro}
                  error={fd.options.length === 0 ? "Add at least one option" : undefined} />}
                <Checkbox label="Required" mb={8} checked={fd.required} onChange={(e) => setFieldDef(i, { required: e.currentTarget.checked })} disabled={ro} />
                {!ro && <ActionIcon variant="subtle" color="red" mb={4} aria-label={`Remove field ${fd.label}`} onClick={() => set("field_schema", fields.filter((_, j) => j !== i))}><IconTrash size={16} /></ActionIcon>}
              </Group>
            ))}
            {removedFields.length > 0 && (
              <Alert color="yellow" variant="light">
                Removing {removedFields.map((x) => `"${x.label}"`).join(", ")} hides it for everyone. Values already saved are dropped the next time each person is edited.
              </Alert>
            )}
          </Stack>
        </Paper>

        {isOwner && <Recalculate spaceId={spaceId} today={today} />}

        {!ro && <Group justify="flex-end">{saveButton}</Group>}

        {me.is_superadmin && (
          <Alert color="red" variant="light" title="Delete this space">
            <Group justify="space-between" wrap="wrap">
              <Text size="sm">Kiosks stop working and the space is hidden. Attendance data stays in the database.</Text>
              <Button color="red" variant="outline" onClick={() => setDeleting(true)}>Delete space</Button>
            </Group>
          </Alert>
        )}
      </Stack>
      <DeleteSpace opened={deleting} space={space!} onClose={() => setDeleting(false)}
        onDeleted={() => { qc.invalidateQueries({ queryKey: ["spaces"] }); refresh(); nav("/spaces"); }} />
    </>
  );
}

function Recalculate({ spaceId, today }: { spaceId: string; today: string }) {
  const [range, setRange] = useState<[string | null, string | null]>([dayjs(today).subtract(30, "day").format("YYYY-MM-DD"), today]);
  const run = useMutation({
    mutationFn: () => api(`/spaces/${spaceId}/recompute`, { json: { start: range[0], end: range[1] ?? range[0] } }),
    onSuccess: () => notifyOk("Recalculating in the background. The register updates within a few minutes."),
    onError: notifyError,
  });
  return (
    <Paper withBorder p="lg">
      <Title order={4} mb={4}>Recalculate past days</Title>
      <Text size="sm" c="dimmed" mb="md">Recount attendance with today's shifts, holidays and settings. Use it after fixing a mistake in the
        schedule; days you pick are counted again from the recorded check-ins.</Text>
      <Group align="flex-end" wrap="wrap">
        <DatePickerInput type="range" label="Days" value={range} onChange={setRange} maxDate={today} valueFormat="D MMM YYYY" w={280} maw="100%" />
        <Button variant="default" onClick={() => run.mutate()} loading={run.isPending} disabled={!range[0]}>Recalculate</Button>
      </Group>
    </Paper>
  );
}

function DeleteSpace({ opened, space, onClose, onDeleted }: { opened: boolean; space: Space; onClose: () => void; onDeleted: () => void }) {
  const [typed, setTyped] = useState("");
  const del = useMutation({
    mutationFn: () => api(`/spaces/${space.id}`, { method: "DELETE", query: { confirm: typed } }),
    onSuccess: () => { notifyOk("Space deleted"); onClose(); onDeleted(); },
    onError: notifyError,
  });
  return (
    <Modal opened={opened} onClose={() => { setTyped(""); onClose(); }} title={`Delete ${space?.name}?`}>
      <Stack>
        <Text size="sm">Its kiosks stop working and it disappears from the admin panel.</Text>
        <TextInput label={`Type ${space?.name} to confirm`} value={typed} onChange={(e) => setTyped(e.currentTarget.value)} data-autofocus />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button color="red" onClick={() => del.mutate()} loading={del.isPending} disabled={typed !== space?.name}>Delete space</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
