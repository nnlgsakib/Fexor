import { ActionIcon, Button, Group, Modal, Pagination, Paper, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { MonthPickerInput, TimeInput } from "@mantine/dates";
import { useDebouncedValue } from "@mantine/hooks";
import { IconDownload, IconSearch, IconTrash } from "@tabler/icons-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import { api, url } from "../api";
import { STATUS } from "../theme";
import type { AttendanceEvent, DayRow, Member } from "../types";
import {
  PageHeader, QueryState, StatusBadge, confirmAction, flagText, fmtMinutes, fmtTime, notifyError, notifyOk, useSpace,
} from "../ui";

const PAGE = 60;

export function Register() {
  const { spaceId, space, canManage, today } = useSpace();
  const qc = useQueryClient();
  const [month, setMonth] = useState(today.slice(0, 7) + "-01");
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 300);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<{ member: Member; day: string; row?: DayRow } | null>(null);

  const start = dayjs(month).startOf("month");
  const end = start.endOf("month");
  const lastDay = end.format("YYYY-MM-DD") > today ? dayjs(today) : end;
  const days = Array.from({ length: start.daysInMonth() }, (_, i) => start.add(i, "day"));

  // Everyone, including people who left during the month (they are shown faded).
  const members = useQuery({
    queryKey: ["members", spaceId, debounced, page, "register"],
    queryFn: () => api<{ items: Member[]; total: number }>(`/spaces/${spaceId}/members`, { query: { q: debounced, page, size: PAGE } }),
    placeholderData: keepPreviousData,
  });
  const ids = (members.data?.items ?? []).map((m) => m.id);
  // Only the people on this page: a whole big space for a month can be over 10,000 rows.
  const rows = useQuery({
    queryKey: ["days", spaceId, month, ids],
    queryFn: () => api<DayRow[]>(`/spaces/${spaceId}/days`, {
      query: { start: start.format("YYYY-MM-DD"), end: lastDay.format("YYYY-MM-DD"), member_ids: ids.join(","), limit: 10000 },
    }),
    enabled: ids.length > 0 && start.format("YYYY-MM-DD") <= today,
    placeholderData: keepPreviousData,
  });

  const byKey = useMemo(() => {
    const m = new Map<string, DayRow>();
    for (const r of rows.data ?? []) m.set(`${r.member_id}|${r.day}`, r);
    return m;
  }, [rows.data]);

  const range = { start: start.format("YYYY-MM-DD"), end: lastDay.format("YYYY-MM-DD") };

  return (
    <>
      <PageHeader
        title="Register"
        description="Each square is one day. Click a square to see times or add a check-in someone missed."
        actions={
          <>
            <Button variant="default" component="a" leftSection={<IconDownload size={16} />}
              href={url(`/spaces/${spaceId}/reports/attendance.xlsx`, range)}>Excel</Button>
            <Button variant="default" component="a" leftSection={<IconDownload size={16} />}
              href={url(`/spaces/${spaceId}/reports/attendance.csv`, range)}>CSV</Button>
          </>
        }
      />
      <Group mb="md" gap="sm" wrap="wrap">
        <MonthPickerInput value={month} onChange={(v) => { if (v) { setMonth(v); } }} maxDate={today} w={180} aria-label="Month" />
        <TextInput placeholder="Search name or ID" aria-label="Search people" leftSection={<IconSearch size={16} />} value={search}
          onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} w={260} maw="100%" />
        <Group gap={14} ml="auto" wrap="wrap">
          {Object.entries(STATUS).filter(([k]) => space?.mode === "in_out" || k !== "half_day").map(([k, s]) => (
            <Group key={k} gap={6}><span className="stamp" style={{ background: s.color, cursor: "default" }}>{s.short}</span><Text size="sm">{s.label}</Text></Group>
          ))}
          <Group gap={6}><span className="stamp empty">·</span><Text size="sm">Nothing recorded</Text></Group>
        </Group>
      </Group>

      <QueryState q={members}>
        <div className="register" aria-busy={rows.isFetching}>
          <table>
            <thead>
              <tr>
                <th className="who">{space?.member_label ?? "Person"}</th>
                {days.map((d) => (
                  <th key={d.date()} className={`${d.day() === 0 || d.day() === 6 ? "weekend" : ""} ${d.format("YYYY-MM-DD") === today ? "today" : ""}`}>
                    {d.date()}
                  </th>
                ))}
                <th className="tot" title="Days present">P</th>
                <th className="tot" title="Days late">L</th>
                <th className="tot" title="Days absent">A</th>
              </tr>
            </thead>
            <tbody>
              {members.data?.items.map((m) => {
                let p = 0, l = 0, a = 0;
                return (
                  <tr key={m.id} style={{ opacity: m.active ? 1 : 0.6 }}>
                    <td className="who" title={`${m.full_name} (${m.external_id})${m.active ? "" : ", inactive"}`}>
                      <Text span fw={500} size="sm">{m.full_name}</Text> <Text span c="dimmed" size="xs">{m.external_id}</Text>
                    </td>
                    {days.map((d) => {
                      const key = d.format("YYYY-MM-DD");
                      const r = byKey.get(`${m.id}|${key}`);
                      if (r) {
                        if (["present", "late", "half_day"].includes(r.status)) p++;
                        if (r.status === "late") l++;
                        if (r.status === "absent") a++;
                      }
                      const s = r ? STATUS[r.status] : undefined;
                      const future = key > today;
                      return (
                        <td key={key}>
                          {s ? (
                            <button className="stamp" style={{ background: s.color }} aria-label={`${m.full_name} ${key} ${s.label}`}
                              onClick={() => setOpen({ member: m, day: key, row: r })}>{s.short}</button>
                          ) : canManage && !future ? (
                            <button className="stamp empty" style={{ cursor: "pointer" }} aria-label={`${m.full_name} ${key} nothing recorded`}
                              onClick={() => setOpen({ member: m, day: key })}>·</button>
                          ) : <span className="stamp empty" />}
                        </td>
                      );
                    })}
                    <td className="tot">{p}</td><td className="tot">{l}</td><td className="tot">{a}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {members.data?.items.length === 0 && (
            <Text p="lg" c="dimmed">{debounced ? "Nobody matches that search." : "No people in this space yet. Add them on the People page."}</Text>
          )}
        </div>
        {rows.isError && <Text c="red" size="sm" mt="xs">Attendance for this month could not be loaded. {rows.error.message}</Text>}
        {(members.data?.total ?? 0) > PAGE && (
          <Pagination mt="md" total={Math.ceil((members.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} />
        )}
      </QueryState>

      {open && (
        <DayModal key={`${open.member.id}|${open.day}`} state={open} onClose={() => setOpen(null)} tz={space?.timezone}
          canManage={canManage} mode={space?.mode} spaceId={spaceId}
          onSaved={() => { qc.invalidateQueries({ queryKey: ["days", spaceId] }); qc.invalidateQueries({ queryKey: ["events", spaceId] }); }} />
      )}
    </>
  );
}

function DayModal({ state, onClose, tz, canManage, onSaved, spaceId, mode }: {
  state: { member: Member; day: string; row?: DayRow }; onClose: () => void; tz?: string;
  canManage: boolean; onSaved: () => void; spaceId: string; mode?: string;
}) {
  const [time, setTime] = useState("09:00");
  const [note, setNote] = useState("");
  const events = useQuery({
    queryKey: ["events", spaceId, "day", state.member.id, state.day],
    queryFn: () => api<AttendanceEvent[]>(`/spaces/${spaceId}/events`,
      { query: { member_id: state.member.id, start: state.day, end: state.day, status: "accepted" } }),
  });
  const add = useMutation({
    // No offset: the server reads it in the space's time zone.
    mutationFn: () => api(`/spaces/${spaceId}/events`,
      { json: { member_id: state.member.id, occurred_at: `${state.day}T${time}:00`, note } }),
    onSuccess: () => { notifyOk("Time added"); setNote(""); onSaved(); events.refetch(); onClose(); },
    onError: notifyError,
  });
  const undo = (e: AttendanceEvent) => confirmAction({
    title: "Remove this time?", text: `The ${fmtTime(e.occurred_at, tz)} entry added by an admin is removed and the day is recounted.`,
    confirm: "Remove",
    onConfirm: async () => {
      try { await api(`/spaces/${spaceId}/events/${e.id}`, { method: "DELETE" }); notifyOk("Removed"); onSaved(); onClose(); }
      catch (err) { notifyError(err); }
    },
  });
  const r = state.row;
  const inOut = mode === "in_out";
  return (
    <Modal opened onClose={onClose} title={`${state.member.full_name}, ${dayjs(state.day).format("ddd D MMM YYYY")}`}>
      <Stack>
        {r ? (
          <Paper withBorder p="md">
            <Group justify="space-between" mb="xs"><StatusBadge status={r.status} />
              {r.flags.length > 0 && <Text size="sm" c="dimmed">{flagText(r.flags)}</Text>}</Group>
            <Group grow>
              <div><Text size="xs" c="dimmed">In</Text><Text fw={600}>{fmtTime(r.first_in, tz)}</Text></div>
              {inOut && <div><Text size="xs" c="dimmed">Out</Text><Text fw={600}>{fmtTime(r.last_out, tz)}</Text></div>}
              {inOut && <div><Text size="xs" c="dimmed">Worked</Text><Text fw={600}>{fmtMinutes(r.worked_minutes)}</Text></div>}
              <div><Text size="xs" c="dimmed">Late</Text><Text fw={600}>{fmtMinutes(r.late_minutes)}</Text></div>
            </Group>
          </Paper>
        ) : <Text c="dimmed">Nothing recorded for this day.</Text>}
        {(events.data ?? []).length > 0 && (
          <Stack gap={4}>
            <Text size="sm" fw={600}>Times recorded</Text>
            {events.data!.map((e) => (
              <Group key={e.id} justify="space-between" wrap="nowrap">
                <Text size="sm">{fmtTime(e.occurred_at, tz)} <Text span c="dimmed" size="sm">
                  {e.source === "admin" ? `added by admin${e.note ? `: ${e.note}` : ""}` : e.device ?? "kiosk"}</Text></Text>
                {canManage && e.source === "admin" && (
                  <ActionIcon variant="subtle" color="red" aria-label="Remove this time" onClick={() => undo(e)}><IconTrash size={16} /></ActionIcon>
                )}
              </Group>
            ))}
          </Stack>
        )}
        {canManage && (
          <Paper withBorder p="md">
            <Text fw={600} mb="xs">{inOut ? "Add a missed check-in or check-out" : "Add a missed check-in"}</Text>
            <Stack gap="xs">
              <TimeInput label={tz ? `Time (${tz})` : "Time"} value={time} onChange={(e) => setTime(e.currentTarget.value)} />
              <Textarea label="Reason" description="At least 3 characters. Kept in the audit log." placeholder="Kiosk was offline, forgot QR card…"
                required value={note} onChange={(e) => setNote(e.currentTarget.value)} />
              <Button onClick={() => add.mutate()} loading={add.isPending} disabled={note.trim().length < 3 || !time}>Add time</Button>
            </Stack>
          </Paper>
        )}
      </Stack>
    </Modal>
  );
}
