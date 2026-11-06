import { ActionIcon, Badge, Button, Chip, Group, Modal, NumberInput, Paper, SimpleGrid, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { DateInput, TimeInput, YearPickerInput } from "@mantine/dates";
import { useDisclosure } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { api } from "../api";
import type { Shift } from "../types";
import { PageHeader, WEEKDAYS, notifyError, notifyOk, useSpace } from "../ui";

type ShiftForm = Omit<Shift, "id" | "overnight" | "active">;
const EMPTY: ShiftForm = {
  name: "", start_time: "09:00", end_time: "17:00", weekdays: [0, 1, 2, 3, 4], grace_minutes: 10,
  early_window_minutes: 120, late_window_minutes: 240, half_day_minutes: null,
};

export function Schedule() {
  const { spaceId, space, canManage } = useSpace();
  const qc = useQueryClient();
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`) });
  const [editing, setEditing] = useState<Shift | "new" | null>(null);
  const [form, setForm] = useState<ShiftForm>(EMPTY);
  const saveShift = useMutation({
    mutationFn: () => editing && editing !== "new"
      ? api(`/spaces/${spaceId}/shifts/${editing.id}`, { method: "PUT", json: { ...form, active: true } })
      : api(`/spaces/${spaceId}/shifts`, { json: form }),
    onSuccess: () => {
      notifyOk("Shift saved. Past attendance is being recalculated.");
      qc.invalidateQueries({ queryKey: ["shifts", spaceId] });
      qc.invalidateQueries({ queryKey: ["space", spaceId] });
      setEditing(null);
    },
    onError: notifyError,
  });
  const openShift = (s: Shift | "new") => {
    setForm(s === "new" ? EMPTY : {
      name: s.name, start_time: s.start_time, end_time: s.end_time, weekdays: s.weekdays, grace_minutes: s.grace_minutes,
      early_window_minutes: s.early_window_minutes, late_window_minutes: s.late_window_minutes, half_day_minutes: s.half_day_minutes,
    });
    setEditing(s);
  };
  const removeShift = (s: Shift) => modals.openConfirmModal({
    title: `Stop using "${s.name}"?`,
    children: <Text size="sm">People on this shift fall back to the space default. Past records are kept.</Text>,
    labels: { confirm: "Stop using shift", cancel: "Cancel" }, confirmProps: { color: "red" },
    onConfirm: async () => {
      try { await api(`/spaces/${spaceId}/shifts/${s.id}`, { method: "DELETE" }); qc.invalidateQueries({ queryKey: ["shifts", spaceId] }); }
      catch (e) { notifyError(e); }
    },
  });

  return (
    <>
      <PageHeader title="Schedule and holidays"
        description={space?.mode === "single"
          ? "One check per day. Arriving after the start time plus grace counts as late."
          : "People check in and out. Late arrival, early leaving, overtime and half days are worked out from the shift."} />
      <Stack gap="xl">
        <section>
          <Group justify="space-between" mb="sm">
            <Title order={3} fz={20}>Shifts</Title>
            {canManage && <Button variant="default" leftSection={<IconPlus size={16} />} onClick={() => openShift("new")}>Add shift</Button>}
          </Group>
          <Paper withBorder>
            <Table verticalSpacing="sm">
              <Table.Thead><Table.Tr>
                <Table.Th>Name</Table.Th><Table.Th>Hours</Table.Th><Table.Th>Days</Table.Th><Table.Th>Grace</Table.Th>
                {space?.mode === "in_out" && <Table.Th>Half day below</Table.Th>}<Table.Th />
              </Table.Tr></Table.Thead>
              <Table.Tbody>
                {shifts.data?.filter((s) => s.active).map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td fw={500}>{s.name} {s.id === space?.default_shift_id && <Badge ml={6} variant="light" tt="none">Default</Badge>}</Table.Td>
                    <Table.Td>{s.start_time}–{s.end_time}{s.overnight && <Text span size="xs" c="dimmed"> (overnight)</Text>}</Table.Td>
                    <Table.Td>{s.weekdays.map((d) => WEEKDAYS[d]).join(", ")}</Table.Td>
                    <Table.Td>{s.grace_minutes} min</Table.Td>
                    {space?.mode === "in_out" && <Table.Td>{s.half_day_minutes ? `${(s.half_day_minutes / 60).toFixed(1)} h` : "–"}</Table.Td>}
                    <Table.Td ta="right">
                      {canManage && <Group gap={4} justify="flex-end">
                        <ActionIcon variant="subtle" aria-label="Edit shift" onClick={() => openShift(s)}><IconPencil size={16} /></ActionIcon>
                        <ActionIcon variant="subtle" color="red" aria-label="Remove shift" onClick={() => removeShift(s)}><IconTrash size={16} /></ActionIcon>
                      </Group>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {shifts.data?.filter((s) => s.active).length === 0 && <Text p="md" c="dimmed">No shifts. Without one, check-ins are recorded but nobody is marked late or absent.</Text>}
          </Paper>
        </section>
        <Holidays />
      </Stack>

      <Modal opened={editing !== null} onClose={() => setEditing(null)} title={editing === "new" ? "Add shift" : "Edit shift"} size="lg">
        <Stack>
          <TextInput label="Name" placeholder="Day shift" required value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
          <Group grow>
            <TimeInput label="Starts" value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.currentTarget.value })} />
            <TimeInput label="Ends" description={form.end_time <= form.start_time ? "Ends next day" : undefined} value={form.end_time}
              onChange={(e) => setForm({ ...form, end_time: e.currentTarget.value })} />
          </Group>
          <div>
            <Text size="sm" fw={500} mb={6}>Working days</Text>
            <Chip.Group multiple value={form.weekdays.map(String)} onChange={(v) => setForm({ ...form, weekdays: v.map(Number).sort() })}>
              <Group gap={6}>{WEEKDAYS.map((d, i) => <Chip key={d} value={String(i)} size="sm">{d}</Chip>)}</Group>
            </Chip.Group>
          </div>
          <SimpleGrid cols={2}>
            <NumberInput label="Grace before late (min)" min={0} max={240} value={form.grace_minutes} onChange={(v) => setForm({ ...form, grace_minutes: Number(v) || 0 })} />
            {space?.mode === "in_out" && (
              <NumberInput label="Half day if worked less than (hours)" min={0} max={24} step={0.5} decimalScale={1}
                value={form.half_day_minutes ? form.half_day_minutes / 60 : ""}
                onChange={(v) => setForm({ ...form, half_day_minutes: v === "" ? null : Math.round(Number(v) * 60) })} />
            )}
            <NumberInput label="Earliest check-in (min before start)" min={0} max={720} value={form.early_window_minutes} onChange={(v) => setForm({ ...form, early_window_minutes: Number(v) || 0 })} />
            <NumberInput label="Latest check-out (min after end)" min={0} max={720} value={form.late_window_minutes} onChange={(v) => setForm({ ...form, late_window_minutes: Number(v) || 0 })} />
          </SimpleGrid>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(null)}>Cancel</Button>
            <Button onClick={() => saveShift.mutate()} loading={saveShift.isPending} disabled={!form.name.trim() || !form.weekdays.length}>Save shift</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

function Holidays() {
  const { spaceId, space, canManage } = useSpace();
  const qc = useQueryClient();
  const [year, setYear] = useState<string>(dayjs().format("YYYY-01-01"));
  const y = dayjs(year).year();
  const list = useQuery({
    queryKey: ["holidays", spaceId, y],
    queryFn: () => api<{ id: string; day: string; name: string; source: string }[]>(`/spaces/${spaceId}/holidays`, { query: { year: y } }),
  });
  const [addOpen, addCtl] = useDisclosure();
  const [h, setH] = useState<{ day: string | null; name: string }>({ day: null, name: "" });
  const [country, setCountry] = useState(space?.country ?? "");
  const refresh = () => qc.invalidateQueries({ queryKey: ["holidays", spaceId] });
  const add = async () => {
    try { await api(`/spaces/${spaceId}/holidays`, { json: h }); refresh(); addCtl.close(); setH({ day: null, name: "" }); }
    catch (e) { notifyError(e); }
  };
  const preset = async () => {
    try {
      const r = await api<{ imported: number }>(`/spaces/${spaceId}/holidays/preset`, { json: { country, year: y } });
      notifyOk(`${r.imported} public holidays added`); refresh();
    } catch (e) { notifyError(e); }
  };
  const del = async (id: string) => {
    try { await api(`/spaces/${spaceId}/holidays/${id}`, { method: "DELETE" }); refresh(); } catch (e) { notifyError(e); }
  };
  return (
    <section>
      <Group justify="space-between" mb="sm" wrap="wrap">
        <Group><Title order={3} fz={20}>Holidays</Title><YearPickerInput value={year} onChange={(v) => v && setYear(v)} w={100} aria-label="Year" /></Group>
        {canManage && (
          <Group>
            <TextInput placeholder="Country code" w={130} value={country} onChange={(e) => setCountry(e.currentTarget.value.toUpperCase())} maxLength={3} />
            <Button variant="default" onClick={preset} disabled={country.length < 2}>Add public holidays for {y}</Button>
            <Button variant="default" leftSection={<IconPlus size={16} />} onClick={addCtl.open}>Add holiday</Button>
          </Group>
        )}
      </Group>
      <Paper withBorder>
        <Table verticalSpacing={8}>
          <Table.Tbody>
            {list.data?.map((x) => (
              <Table.Tr key={x.id}>
                <Table.Td w={170}>{dayjs(x.day).format("ddd D MMM YYYY")}</Table.Td>
                <Table.Td>{x.name}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{x.source === "preset" ? "Public holiday" : "Added by admin"}</Text></Table.Td>
                <Table.Td ta="right">{canManage && <ActionIcon variant="subtle" color="red" aria-label="Delete holiday" onClick={() => del(x.id)}><IconTrash size={16} /></ActionIcon>}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {list.data?.length === 0 && <Text p="md" c="dimmed">No holidays in {y}.</Text>}
      </Paper>
      <Modal opened={addOpen} onClose={addCtl.close} title="Add holiday">
        <Stack>
          <DateInput label="Date" value={h.day} onChange={(v) => setH({ ...h, day: v })} />
          <TextInput label="Name" value={h.name} onChange={(e) => setH({ ...h, name: e.currentTarget.value })} />
          <Button onClick={add} disabled={!h.day || !h.name.trim()}>Add holiday</Button>
        </Stack>
      </Modal>
    </section>
  );
}
