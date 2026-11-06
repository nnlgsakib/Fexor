import {
  ActionIcon, Alert, Button, Checkbox, Group, NumberInput, Paper, SegmentedControl, Select, Slider, Stack, TagsInput,
  Text, TextInput, Textarea, Title,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api";
import { useMe } from "../auth";
import type { FieldDef, Shift, Space } from "../types";
import { PageHeader, notifyError, notifyOk, useSpace } from "../ui";
import { timezones } from "./Spaces";

export function Settings() {
  const { spaceId, space, isOwner } = useSpace();
  const { me, refresh } = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState<Space | null>(null);
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`) });
  useEffect(() => { if (space) setF(structuredClone(space)); }, [space]);
  if (!f) return null;
  const set = <K extends keyof Space>(k: K, v: Space[K]) => setF({ ...f, [k]: v });

  const save = async () => {
    try {
      const { id: _id, role: _role, ...body } = f;
      await api(`/spaces/${spaceId}`, { method: "PATCH", json: body });
      notifyOk("Settings saved");
      qc.invalidateQueries({ queryKey: ["space", spaceId] });
      refresh();
    } catch (e) { notifyError(e); }
  };
  const del = () => {
    let typed = "";
    modals.openConfirmModal({
      title: `Delete ${f.name}?`,
      children: (
        <Stack gap="xs">
          <Text size="sm">Its kiosks stop working and it disappears from the admin panel. Type the space name to confirm.</Text>
          <TextInput onChange={(e) => { typed = e.currentTarget.value; }} />
        </Stack>
      ),
      labels: { confirm: "Delete space", cancel: "Cancel" }, confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`/spaces/${spaceId}`, { method: "DELETE", query: { confirm: typed } });
          refresh(); nav("/spaces");
        } catch (e) { notifyError(e); }
      },
    });
  };
  const fields = f.field_schema;
  const setFieldDef = (i: number, patch: Partial<FieldDef>) => set("field_schema", fields.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const ro = !isOwner;

  return (
    <>
      <PageHeader title="Settings" description={ro ? "Only space owners can change these settings." : undefined}
        actions={!ro && <Button onClick={save}>Save settings</Button>} />
      <Stack gap="lg" maw={760}>
        <Paper withBorder p="lg">
          <Title order={4} mb="md">General</Title>
          <Stack>
            <TextInput label="Name" value={f.name} onChange={(e) => set("name", e.currentTarget.value)} disabled={ro} />
            <Textarea label="Description" value={f.description} onChange={(e) => set("description", e.currentTarget.value)} disabled={ro} autosize />
            <Group grow>
              <Select label="Time zone" data={timezones(f.timezone)} searchable value={f.timezone} onChange={(v) => v && set("timezone", v)} disabled={ro} />
              <TextInput label="What to call people" placeholder="Employee, Student" value={f.member_label} onChange={(e) => set("member_label", e.currentTarget.value)} disabled={ro} />
            </Group>
            <div>
              <Text size="sm" fw={500} mb={4}>Attendance type</Text>
              <SegmentedControl value={f.mode} onChange={(v) => set("mode", v as Space["mode"])} disabled={ro}
                data={[{ value: "in_out", label: "Check in and out" }, { value: "single", label: "One check per day" }]} />
            </div>
            <Group grow>
              <Select label="Default shift" data={(shifts.data ?? []).filter((s) => s.active).map((s) => ({ value: s.id, label: s.name }))}
                value={f.default_shift_id} onChange={(v) => set("default_shift_id", v)} clearable disabled={ro} />
              <NumberInput label="Ignore repeat scans within (minutes)" min={0} max={240} value={f.min_rescan_minutes}
                onChange={(v) => set("min_rescan_minutes", Number(v) || 0)} disabled={ro} />
            </Group>
          </Stack>
        </Paper>

        <Paper withBorder p="lg">
          <Title order={4} mb={4}>Verification at the kiosk</Title>
          <Text size="sm" c="dimmed" mb="md">Stricter settings stop more impostors but may ask honest people to try again more often.</Text>
          <Stack gap="lg">
            <div>
              <Text size="sm" fw={500}>Face match strictness: {f.match_threshold.toFixed(2)}</Text>
              <Slider min={0.36} max={0.6} step={0.01} value={f.match_threshold} onChange={(v) => set("match_threshold", v)} disabled={ro}
                marks={[{ value: 0.36, label: "Lenient" }, { value: 0.42, label: "Default" }, { value: 0.6, label: "Strict" }]} mb="md" />
            </div>
            <div>
              <Text size="sm" fw={500}>Photo and screen detection: {f.liveness_threshold.toFixed(2)}</Text>
              <Slider min={0.3} max={0.9} step={0.05} value={f.liveness_threshold} onChange={(v) => set("liveness_threshold", v)} disabled={ro}
                marks={[{ value: 0.3, label: "Lenient" }, { value: 0.6, label: "Default" }, { value: 0.9, label: "Strict" }]} mb="md" />
            </div>
            <NumberInput label="Movements to ask for (blink, turn, smile…)" description="0 turns the movement check off. 2 is a good balance."
              min={0} max={4} value={f.challenge_actions} onChange={(v) => set("challenge_actions", Number(v) || 0)} disabled={ro} w={320} />
            <Checkbox label="Keep a small face snapshot when a check fails, for review" checked={f.store_failure_snapshots}
              onChange={(e) => set("store_failure_snapshots", e.currentTarget.checked)} disabled={ro} />
            <Checkbox label="Keep enrollment photos (needed to switch face models later)" checked={f.keep_enrollment_photos}
              onChange={(e) => set("keep_enrollment_photos", e.currentTarget.checked)} disabled={ro} />
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
              <Group key={i} align="flex-end" wrap="nowrap">
                <TextInput label="Label" value={fd.label} disabled={ro} style={{ flex: 1 }}
                  onChange={(e) => {
                    const label = e.currentTarget.value;
                    const auto = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "f_$1").slice(0, 40);
                    setFieldDef(i, { label, key: fd.key && space?.field_schema.some((s) => s.key === fd.key) ? fd.key : auto });
                  }} />
                <Select label="Type" w={120} data={["text", "number", "date", "select"]} value={fd.type} disabled={ro}
                  onChange={(v) => setFieldDef(i, { type: (v ?? "text") as FieldDef["type"] })} />
                {fd.type === "select" && <TagsInput label="Options" w={220} value={fd.options} onChange={(v) => setFieldDef(i, { options: v })} disabled={ro} />}
                <Checkbox label="Required" mb={8} checked={fd.required} onChange={(e) => setFieldDef(i, { required: e.currentTarget.checked })} disabled={ro} />
                {!ro && <ActionIcon variant="subtle" color="red" mb={4} aria-label="Remove field" onClick={() => set("field_schema", fields.filter((_, j) => j !== i))}><IconTrash size={16} /></ActionIcon>}
              </Group>
            ))}
          </Stack>
        </Paper>

        {me.is_superadmin && (
          <Alert color="red" variant="light" title="Delete this space">
            <Group justify="space-between">
              <Text size="sm">Kiosks stop working and the space is hidden. Attendance data stays in the database.</Text>
              <Button color="red" variant="outline" onClick={del}>Delete space</Button>
            </Group>
          </Alert>
        )}
      </Stack>
    </>
  );
}
