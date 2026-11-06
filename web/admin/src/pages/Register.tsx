import { Button, Group, Modal, Pagination, Paper, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { MonthPickerInput, TimeInput } from "@mantine/dates";
import { IconDownload, IconSearch } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import { api, url } from "../api";
import { STATUS } from "../theme";
import type { DayRow, Member } from "../types";
import { PageHeader, StatusBadge, fmtMinutes, fmtTime, notifyError, notifyOk, today, useSpace } from "../ui";

const PAGE = 60;

export function Register() {
  const { spaceId, space, canManage } = useSpace();
  const qc = useQueryClient();
  const [month, setMonth] = useState(dayjs().startOf("month").format("YYYY-MM-DD"));
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<{ member: Member; day: string; row?: DayRow } | null>(null);

  const start = dayjs(month).startOf("month");
  const end = start.endOf("month");
  const lastDay = end.isAfter(dayjs()) ? dayjs() : end;
  const days = Array.from({ length: start.daysInMonth() }, (_, i) => start.add(i, "day"));

  const members = useQuery({
    queryKey: ["members", spaceId, search, page, "register"],
    queryFn: () => api<{ items: Member[]; total: number }>(`/spaces/${spaceId}/members`,
      { query: { q: search, page, size: PAGE, active: true } }),
  });
  const rows = useQuery({
    queryKey: ["days", spaceId, month],
    queryFn: () => api<DayRow[]>(`/spaces/${spaceId}/days`,
      { query: { start: start.format("YYYY-MM-DD"), end: lastDay.format("YYYY-MM-DD"), limit: 10000 } }),
    enabled: !start.isAfter(dayjs()),
  });

  const byKey = useMemo(() => {
    const m = new Map<string, DayRow>();
    for (const r of rows.data ?? []) m.set(`${r.member_id}|${r.day}`, r);
    return m;
  }, [rows.data]);

  const range = { start: start.format("YYYY-MM-DD"), end: end.format("YYYY-MM-DD") };

  return (
    <>
      <PageHeader
        title="Register"
        description="Each square is one day. Click a square to see times or add a missed check-in."
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
        <MonthPickerInput value={month} onChange={(v) => { if (v) { setMonth(v); } }} maxDate={today()} w={180} aria-label="Month" />
        <TextInput placeholder="Search name or ID" leftSection={<IconSearch size={16} />} value={search}
          onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} w={260} />
        <Group gap={14} ml="auto" wrap="wrap">
          {Object.entries(STATUS).filter(([k]) => space?.mode === "in_out" || k !== "half_day").map(([k, s]) => (
            <Group key={k} gap={6}><span className="stamp" style={{ background: s.color, cursor: "default" }}>{s.short}</span><Text size="sm">{s.label}</Text></Group>
          ))}
        </Group>
      </Group>

      <div className="register">
        <table>
          <thead>
            <tr>
              <th className="who">{space?.member_label ?? "Person"}</th>
              {days.map((d) => (
                <th key={d.date()} className={`${d.day() === 0 || d.day() === 6 ? "weekend" : ""} ${d.format("YYYY-MM-DD") === today() ? "today" : ""}`}>
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
                <tr key={m.id}>
                  <td className="who" title={`${m.full_name} (${m.external_id})`}>
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
                    const future = d.isAfter(dayjs(), "day");
                    return (
                      <td key={key}>
                        {s ? (
                          <button className="stamp" style={{ background: s.color }} aria-label={`${m.full_name} ${key} ${s.label}`}
                            onClick={() => setOpen({ member: m, day: key, row: r })}>{s.short}</button>
                        ) : canManage && !future ? (
                          <button className="stamp empty" style={{ cursor: "pointer" }} aria-label={`${m.full_name} ${key} no record`}
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
        {members.data?.items.length === 0 && <Text p="lg" c="dimmed">No people in this space yet. Add them on the People page.</Text>}
      </div>
      {(members.data?.total ?? 0) > PAGE && (
        <Pagination mt="md" total={Math.ceil((members.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} />
      )}

      <DayModal state={open} onClose={() => setOpen(null)} tz={space?.timezone} canManage={canManage} mode={space?.mode}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["days", spaceId] }); setOpen(null); }} spaceId={spaceId} />
    </>
  );
}

function DayModal({ state, onClose, tz, canManage, onSaved, spaceId, mode }: {
  state: { member: Member; day: string; row?: DayRow } | null; onClose: () => void; tz?: string;
  canManage: boolean; onSaved: () => void; spaceId: string; mode?: string;
}) {
  const [time, setTime] = useState("09:00");
  const [note, setNote] = useState("");
  const add = useMutation({
    // No offset: the server reads it in the space's time zone.
    mutationFn: () => api(`/spaces/${spaceId}/events`,
      { json: { member_id: state!.member.id, occurred_at: `${state!.day}T${time}:00`, note } }),
    onSuccess: () => { notifyOk("Check-in added"); setNote(""); onSaved(); },
    onError: notifyError,
  });
  const r = state?.row;
  return (
    <Modal opened={!!state} onClose={onClose} title={state ? `${state.member.full_name}, ${dayjs(state.day).format("ddd D MMM YYYY")}` : ""}>
      {state && (
        <Stack>
          {r ? (
            <Paper withBorder p="md">
              <Group justify="space-between" mb="xs"><StatusBadge status={r.status} />
                {r.flags.length > 0 && <Text size="sm" c="dimmed">{r.flags.map((f) => f.replaceAll("_", " ")).join(", ")}</Text>}</Group>
              <Group grow>
                <div><Text size="xs" c="dimmed">In</Text><Text fw={600}>{fmtTime(r.first_in, tz)}</Text></div>
                {mode === "in_out" && <div><Text size="xs" c="dimmed">Out</Text><Text fw={600}>{fmtTime(r.last_out, tz)}</Text></div>}
                {mode === "in_out" && <div><Text size="xs" c="dimmed">Worked</Text><Text fw={600}>{fmtMinutes(r.worked_minutes)}</Text></div>}
                <div><Text size="xs" c="dimmed">Late</Text><Text fw={600}>{fmtMinutes(r.late_minutes)}</Text></div>
              </Group>
            </Paper>
          ) : <Text c="dimmed">No attendance recorded for this day.</Text>}
          {canManage && (
            <Paper withBorder p="md">
              <Text fw={600} mb="xs">Add a missed check-in</Text>
              <Stack gap="xs">
                <TimeInput label={`Time (${tz})`} value={time} onChange={(e) => setTime(e.currentTarget.value)} />
                <Textarea label="Reason" placeholder="Kiosk was offline, forgot QR card…" required value={note}
                  onChange={(e) => setNote(e.currentTarget.value)} />
                <Button onClick={() => add.mutate()} loading={add.isPending} disabled={note.trim().length < 3 || !time}>Add check-in</Button>
              </Stack>
            </Paper>
          )}
        </Stack>
      )}
    </Modal>
  );
}
