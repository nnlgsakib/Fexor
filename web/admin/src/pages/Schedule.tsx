import { ActionIcon, Badge, Button, Chip, Group, Modal, NumberInput, Paper, SimpleGrid, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { DatePickerInput, TimeInput, YearPickerInput } from "@mantine/dates";
import { useDisclosure } from "@mantine/hooks";
import { IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { api } from "../api";
import type { Shift } from "../types";
import { PageHeader, QueryState, type TimeFormat, WEEKDAYS, confirmAction, fmtClock, notifyError, notifyOk, plural, useSpace } from "../ui";

type ShiftForm = Omit<Shift, "id" | "overnight" | "active">;
const EMPTY: ShiftForm = {
  name: "", start_time: "09:00", end_time: "17:00", weekdays: [0, 1, 2, 3, 4], grace_minutes: 10,
  early_window_minutes: 120, late_window_minutes: 240, half_day_minutes: null,
  checkin_close_minutes: null, checkout_open_minutes: null,
};

/** "09:00" plus 30 minutes is "09:30" (wraps past midnight). */
export function clockPlus(hhmm: string, minutes: number) {
  const [h, m] = hhmm.split(":").map(Number);
  const t = (((h * 60 + m + minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

const windowsText = (s: ShiftForm, single: boolean, f: TimeFormat) => [
  s.checkin_close_minutes != null && `in by ${fmtClock(clockPlus(s.start_time, s.checkin_close_minutes), f)}`,
  !single && s.checkout_open_minutes != null && `out from ${fmtClock(clockPlus(s.end_time, -s.checkout_open_minutes), f)}`,
].filter(Boolean).join(", ");

const minutesOrNull = (v: string | number) => (v === "" || Number.isNaN(Number(v)) ? null : Math.max(0, Math.round(Number(v))));
const MAX_HOLIDAY_DAYS = 120;

export function Schedule() {
  const { spaceId, space, canManage } = useSpace();
  const tf = space?.time_format;
  const qc = useQueryClient();
  const shifts = useQuery({ queryKey: ["shifts", spaceId], queryFn: () => api<Shift[]>(`/spaces/${spaceId}/shifts`) });
  const [editing, setEditing] = useState<Shift | "new" | null>(null);
  const [form, setForm] = useState<ShiftForm>(EMPTY);
  const single = space?.mode === "single";
  const timesOk = /^\d\d:\d\d$/.test(form.start_time) && /^\d\d:\d\d$/.test(form.end_time) && form.start_time !== form.end_time;
  const saveShift = useMutation({
    mutationFn: () => editing && editing !== "new"
      ? api(`/spaces/${spaceId}/shifts/${editing.id}`, { method: "PUT", json: { ...form, active: true } })
      : api(`/spaces/${spaceId}/shifts`, { json: form }),
    onSuccess: () => {
      notifyOk("Shift saved. The last two months are being recounted with it.");
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
      checkin_close_minutes: s.checkin_close_minutes, checkout_open_minutes: s.checkout_open_minutes,
    });
    setEditing(s);
  };
  const removeShift = (s: Shift) => confirmAction({
    title: `Stop using "${s.name}"?`,
    text: "From today, people on this shift fall back to the space default. Past days keep the shift they were counted with.",
    confirm: "Stop using shift",
    onConfirm: async () => {
      try {
        await api(`/spaces/${spaceId}/shifts/${s.id}`, { method: "DELETE" });
        notifyOk("Shift removed");
        qc.invalidateQueries({ queryKey: ["shifts", spaceId] });
        qc.invalidateQueries({ queryKey: ["space", spaceId] });  // it may have been the default
      } catch (e) { notifyError(e); }
    },
  });
  const active = shifts.data?.filter((s) => s.active) ?? [];

  return (
    <>
      <PageHeader title="Schedule and holidays"
        description={single
          ? "One check per day. Arriving after the start time plus grace counts as late."
          : "People check in and out. Late arrival, early leaving, overtime and half days are worked out from the shift."} />
      <Stack gap="xl">
        <section>
          <Group justify="space-between" mb="sm">
            <Title order={3} fz={20}>Shifts</Title>
            {canManage && <Button variant="default" leftSection={<IconPlus size={16} />} onClick={() => openShift("new")}>Add shift</Button>}
          </Group>
          <QueryState q={shifts}>
            <Paper withBorder>
              <Table.ScrollContainer minWidth={620}>
                <Table verticalSpacing="sm">
                  <Table.Thead><Table.Tr>
                    <Table.Th>Name</Table.Th><Table.Th>Hours</Table.Th><Table.Th>Days</Table.Th><Table.Th>Grace</Table.Th>
                    {!single && <Table.Th>Half day below</Table.Th>}<Table.Th>Fixed times</Table.Th><Table.Th />
                  </Table.Tr></Table.Thead>
                  <Table.Tbody>
                    {active.map((s) => (
                      <Table.Tr key={s.id}>
                        <Table.Td fw={500}>{s.name} {s.id === space?.default_shift_id && <Badge ml={6} variant="light" tt="none">Default</Badge>}</Table.Td>
                        <Table.Td style={{ whiteSpace: "nowrap" }}>{fmtClock(s.start_time, tf)}–{fmtClock(s.end_time, tf)}{s.overnight && <Text span size="xs" c="dimmed"> (overnight)</Text>}</Table.Td>
                        <Table.Td>{s.weekdays.map((d) => WEEKDAYS[d]).join(", ")}</Table.Td>
                        <Table.Td>{s.grace_minutes} min</Table.Td>
                        {!single && <Table.Td>{s.half_day_minutes ? `${(s.half_day_minutes / 60).toFixed(1)} h` : "–"}</Table.Td>}
                        <Table.Td>{windowsText(s, single, tf) || "–"}</Table.Td>
                        <Table.Td ta="right">
                          {canManage && <Group gap={4} justify="flex-end" wrap="nowrap">
                            <ActionIcon variant="subtle" aria-label={`Edit ${s.name}`} onClick={() => openShift(s)}><IconPencil size={16} /></ActionIcon>
                            <ActionIcon variant="subtle" color="red" aria-label={`Remove ${s.name}`} onClick={() => removeShift(s)}><IconTrash size={16} /></ActionIcon>
                          </Group>}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
              {active.length === 0 && <Text p="md" c="dimmed">No shifts. Without one, check-ins are recorded but nobody is marked late or absent.</Text>}
            </Paper>
          </QueryState>
        </section>
        <Holidays />
      </Stack>

      <Modal opened={editing !== null} onClose={() => setEditing(null)} title={editing === "new" ? "Add shift" : "Edit shift"} size="lg">
        <Stack>
          <TextInput label="Name" placeholder="Day shift" required value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
          <SimpleGrid cols={2}>
            <TimeInput label="Starts" required value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.currentTarget.value })} />
            <TimeInput label="Ends" required description={timesOk && form.end_time < form.start_time ? "Ends the next day" : undefined}
              error={form.start_time && form.start_time === form.end_time ? "Start and end can't be the same" : undefined}
              value={form.end_time} onChange={(e) => setForm({ ...form, end_time: e.currentTarget.value })} />
          </SimpleGrid>
          <div>
            <Text size="sm" fw={500} mb={6}>Working days</Text>
            <Chip.Group multiple value={form.weekdays.map(String)} onChange={(v) => setForm({ ...form, weekdays: v.map(Number).sort() })}>
              <Group gap={6}>{WEEKDAYS.map((d, i) => <Chip key={d} value={String(i)} size="sm">{d}</Chip>)}</Group>
            </Chip.Group>
          </div>
          <SimpleGrid cols={{ base: 1, xs: 2 }}>
            <NumberInput label="Grace before late (min)" min={0} max={240} value={form.grace_minutes} onChange={(v) => setForm({ ...form, grace_minutes: Number(v) || 0 })} />
            {!single && (
              <NumberInput label="Half day if worked less than (hours)" description="Leave empty for no half days" min={0.5} max={24} step={0.5} decimalScale={1}
                value={form.half_day_minutes ? form.half_day_minutes / 60 : ""}
                onChange={(v) => setForm({ ...form, half_day_minutes: v === "" || !Number(v) ? null : Math.round(Number(v) * 60) })} />
            )}
            <NumberInput label="Earliest check-in (min before start)" min={0} max={720} value={form.early_window_minutes}
              onChange={(v) => setForm({ ...form, early_window_minutes: Number(v) || 0 })} />
            <NumberInput label={single ? "Check-ins counted until (min after end)" : "Latest check-out (min after end)"} min={0} max={720}
              value={form.late_window_minutes} onChange={(v) => setForm({ ...form, late_window_minutes: Number(v) || 0 })} />
          </SimpleGrid>
          <div>
            <Text size="sm" fw={500}>Fixed check-in and check-out times</Text>
            <Text size="xs" c="dimmed" mb={6}>Leave empty to allow any time. A scan outside these times waits on the Review page until you approve it.</Text>
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <NumberInput label="Check-in closes (min after start)" min={0} max={720} placeholder="Any time"
                description={timesOk && form.checkin_close_minutes != null
                  ? `Check-in ${fmtClock(clockPlus(form.start_time, -form.early_window_minutes), tf)}–${fmtClock(clockPlus(form.start_time, form.checkin_close_minutes), tf)}. Not checked in by then: absent.`
                  : "Late check-ins count as usual"}
                value={form.checkin_close_minutes ?? ""} onChange={(v) => setForm({ ...form, checkin_close_minutes: minutesOrNull(v) })} />
              {!single && (
                <NumberInput label="Check-out opens (min before end)" min={0} max={720} placeholder="Any time"
                  description={timesOk && form.checkout_open_minutes != null
                    ? `Check-out from ${fmtClock(clockPlus(form.end_time, -form.checkout_open_minutes), tf)}. Earlier: early leave, needs your approval.`
                    : "People can check out any time"}
                  value={form.checkout_open_minutes ?? ""} onChange={(v) => setForm({ ...form, checkout_open_minutes: minutesOrNull(v) })} />
              )}
            </SimpleGrid>
          </div>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(null)}>Cancel</Button>
            <Button onClick={() => saveShift.mutate()} loading={saveShift.isPending} disabled={!form.name.trim() || !form.weekdays.length || !timesOk}>Save shift</Button>
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
  const [h, setH] = useState<{ range: [string | null, string | null]; name: string }>({ range: [null, null], name: "" });
  const [typed, setCountry] = useState<string | null>(null);
  const country = typed ?? space?.country ?? "";  // follows the space until the user types
  const refresh = () => qc.invalidateQueries({ queryKey: ["holidays", spaceId] });
  const [from, to] = h.range;
  const nDays = from ? dayjs(to ?? from).diff(dayjs(from), "day") + 1 : 0;
  const add = useMutation({
    mutationFn: async () => {
      // A school break is one holiday per day.
      for (let i = 0; i < nDays; i++) {
        await api(`/spaces/${spaceId}/holidays`, { json: { day: dayjs(from).add(i, "day").format("YYYY-MM-DD"), name: h.name } });
      }
    },
    onSuccess: () => { notifyOk(nDays > 1 ? `${nDays} holiday days added` : "Holiday added"); refresh(); addCtl.close(); setH({ range: [null, null], name: "" }); },
    onError: (e) => { refresh(); notifyError(e); },
  });
  const preset = useMutation({
    mutationFn: () => api<{ imported: number }>(`/spaces/${spaceId}/holidays/preset`, { json: { country, year: y } }),
    onSuccess: (r) => { notifyOk(`Public holidays for ${y} added (${r.imported} in the calendar)`); refresh(); },
    onError: notifyError,
  });
  const del = (x: { id: string; day: string; name: string }) => confirmAction({
    title: `Remove "${x.name}"?`, text: `${dayjs(x.day).format("ddd D MMM YYYY")} becomes a normal day again and is recounted.`,
    confirm: "Remove holiday",
    onConfirm: async () => {
      try { await api(`/spaces/${spaceId}/holidays/${x.id}`, { method: "DELETE" }); notifyOk("Holiday removed"); refresh(); } catch (e) { notifyError(e); }
    },
  });
  return (
    <section>
      <Group justify="space-between" mb="sm" wrap="wrap">
        <Group><Title order={3} fz={20}>Holidays</Title><YearPickerInput value={year} onChange={(v) => v && setYear(v)} w={100} aria-label="Year" /></Group>
        {canManage && (
          <Group align="flex-end" wrap="wrap">
            <TextInput label="Country code" placeholder="IN, US, GB" w={120} value={country}
              onChange={(e) => setCountry(e.currentTarget.value.toUpperCase().trim())} maxLength={3} />
            <Button variant="default" onClick={() => preset.mutate()} loading={preset.isPending} disabled={!/^[A-Z]{2,3}$/.test(country)}>
              Add public holidays for {y}
            </Button>
            <Button variant="default" leftSection={<IconPlus size={16} />} onClick={addCtl.open}>Add holiday</Button>
          </Group>
        )}
      </Group>
      <QueryState q={list}>
        <Paper withBorder>
          <Table.ScrollContainer minWidth={480}>
            <Table verticalSpacing={8}>
              <Table.Tbody>
                {list.data?.map((x) => (
                  <Table.Tr key={x.id}>
                    <Table.Td w={170} style={{ whiteSpace: "nowrap" }}>{dayjs(x.day).format("ddd D MMM YYYY")}</Table.Td>
                    <Table.Td>{x.name}</Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{x.source === "preset" ? "Public holiday" : "Added by admin"}</Text></Table.Td>
                    <Table.Td ta="right">{canManage && <ActionIcon variant="subtle" color="red" aria-label={`Remove ${x.name}`} onClick={() => del(x)}><IconTrash size={16} /></ActionIcon>}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {list.data?.length === 0 && <Text p="md" c="dimmed">No holidays in {y}.</Text>}
        </Paper>
      </QueryState>
      <Modal opened={addOpen} onClose={addCtl.close} title="Add holiday">
        <Stack>
          <DatePickerInput type="range" allowSingleDateInRange label="Date or dates" description="Pick one day, or the first and last day of a break"
            value={h.range} onChange={(v) => setH({ ...h, range: v })} valueFormat="D MMM YYYY" />
          <TextInput label="Name" placeholder="Diwali, Summer break" value={h.name} onChange={(e) => setH({ ...h, name: e.currentTarget.value })} />
          {nDays > MAX_HOLIDAY_DAYS && <Text c="red" size="sm">At most {MAX_HOLIDAY_DAYS} days at once.</Text>}
          <Button onClick={() => add.mutate()} loading={add.isPending} disabled={!from || !h.name.trim() || nDays > MAX_HOLIDAY_DAYS}>
            {nDays > 1 ? `Add ${plural(nDays, "day")}` : "Add holiday"}
          </Button>
        </Stack>
      </Modal>
    </section>
  );
}
