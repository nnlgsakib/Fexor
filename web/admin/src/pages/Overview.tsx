import { BarChart } from "@mantine/charts";
import { Anchor, Button, Grid, Group, Modal, Paper, SimpleGrid, Stack, Table, Text, ThemeIcon, Title, UnstyledButton } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";
import { DateInput } from "@mantine/dates";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { STATUS } from "../theme";
import type { AttendanceEvent, DayRow } from "../types";
import { EventBadge, PageHeader, QueryState, type TimeFormat, dateIn, fmtDateTime, fmtMinutes, fmtTime, reasonText, useSpace } from "../ui";

interface Dashboard {
  day: string;
  members: number;
  counts: Record<string, number>;
  present: number;
  not_checked_in: number;
  failed_today: number;
  pending_reviews: number;
  trend: ({ day: string } & Record<string, number | string>)[];
  late_top: { name: string; external_id: string; days: number; minutes: number }[];
  failure_reasons: Record<string, number>;
  recent: AttendanceEvent[];
  devices: { total: number; online: number; clock_drift: string[] };
  setup: { members: number; members_without_face: number; shifts: number; kiosks_ready: number; has_checkins: boolean };
}

function Checklist({ d, spaceId }: { d: Dashboard; spaceId: string }) {
  const s = d.setup;
  const items = [
    { done: s.members > 0, title: "Add people", text: "Add them one by one or import a spreadsheet.", to: "people", action: "Open People" },
    { done: s.members > 0 && s.members_without_face === 0, title: "Add a face photo for everyone",
      text: s.members_without_face ? `${s.members_without_face} still need a photo. Then print their QR cards from People.` : "Then print their QR cards from People.",
      to: "people", action: "Add photos" },
    { done: s.shifts > 0, title: "Set working hours", text: "Needed to mark people late or absent.", to: "schedule", action: "Open schedule" },
    { done: s.kiosks_ready > 0, title: "Set up a kiosk", text: "Install the kiosk app, then pair it by showing a QR code to its camera.", to: "devices", action: "Add kiosk" },
    { done: s.has_checkins, title: "Do a test check-in", text: "Show a QR card at the kiosk and follow the instructions on screen.", to: "", action: "" },
  ];
  if (items.every((i) => i.done)) return null;
  const done = items.filter((i) => i.done).length;
  return (
    <Paper withBorder p="lg">
      <Group justify="space-between" mb="sm">
        <Title order={4}>Get this space ready</Title>
        <Text size="sm" c="dimmed">{done} of {items.length} done</Text>
      </Group>
      <Stack gap="xs">
        {items.map((i, n) => (
          <Group key={i.title} justify="space-between" wrap="nowrap" opacity={i.done ? 0.6 : 1}>
            <Group gap="sm" wrap="nowrap" align="flex-start">
              <ThemeIcon radius="xl" size={26} color={i.done ? "ledger" : "gray"} variant={i.done ? "filled" : "light"}>
                {i.done ? <IconCheck size={16} /> : <Text size="sm" fw={600}>{n + 1}</Text>}
              </ThemeIcon>
              <div>
                <Text fw={600} td={i.done ? "line-through" : undefined}>{i.title}</Text>
                {!i.done && <Text size="sm" c="dimmed">{i.text}</Text>}
              </div>
            </Group>
            {!i.done && i.to !== "" && (
              <Button size="xs" variant="default" component={Link} to={`/spaces/${spaceId}/${i.to}`}>{i.action}</Button>
            )}
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}

function Stat({ label, value, color, hint, onOpen }: {
  label: string; value: number | string; color?: string; hint?: string; onOpen?: () => void;
}) {
  const body = (
    <Paper withBorder p="md" h="100%">
      <Text size="sm" c="dimmed">{label}</Text>
      <div className="stat-value" style={{ color }}>{value}</div>
      {hint && <Text size="xs" c="dimmed" mt={2}>{hint}</Text>}
      {onOpen && Number(value) > 0 && <Text size="xs" c="ledger.7" mt={2}>See who</Text>}
    </Paper>
  );
  return onOpen && Number(value) > 0
    ? <UnstyledButton onClick={onOpen} aria-label={`${label}: see who`}>{body}</UnstyledButton> : body;
}

/** "Who was late / absent that day": the question admins ask most. */
function WhoModal({ spaceId, day, status, tz, tf, onClose }: { spaceId: string; day: string; status: string | null; tz?: string; tf?: TimeFormat; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["days", spaceId, "who", day, status],
    queryFn: () => api<DayRow[]>(`/spaces/${spaceId}/days`, { query: { start: day, end: day, status } }),
    enabled: !!status,
  });
  return (
    <Modal opened={!!status} onClose={onClose} title={status ? `${STATUS[status]?.label ?? status} on ${dayjs(day).format("D MMM YYYY")}` : ""}>
      <QueryState q={q}>
        <Table verticalSpacing={6}>
          <Table.Tbody>
            {q.data?.map((r) => (
              <Table.Tr key={r.member_id}>
                <Table.Td>{r.member_name} <Text span c="dimmed" size="sm">{r.external_id}</Text></Table.Td>
                <Table.Td ta="right">{status === "late" ? `${fmtTime(r.first_in, tz, tf)}, ${fmtMinutes(r.late_minutes)} late` : ""}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {q.data?.length === 0 && <Text c="dimmed" size="sm">Nobody.</Text>}
      </QueryState>
    </Modal>
  );
}

export function Overview() {
  const { spaceId, space, canManage, today, spaceQuery } = useSpace();
  const [picked, setPicked] = useState<string | null>(null);
  const [who, setWho] = useState<string | null>(null);
  const day = picked ?? today;
  const q = useQuery({
    queryKey: ["dashboard", spaceId, day],
    // No day for "today": the server uses today in the space's time zone.
    queryFn: () => api<Dashboard>(`/spaces/${spaceId}/dashboard`, { query: { day: picked ?? undefined } }),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });
  const d = q.data;
  const tz = space?.timezone;
  const isToday = day === today;
  const statuses = space?.mode === "single" ? ["present", "late", "absent", "leave"] : ["present", "late", "half_day", "absent", "leave"];

  return (
    <>
      <PageHeader
        title={space?.name ?? "Overview"}
        description={isToday ? "Today so far. Updates every few seconds." : `Attendance on ${dayjs(day).format("dddd, D MMMM YYYY")}`}
        actions={<DateInput value={day} onChange={(v) => setPicked(v && v !== today ? v : null)} maxDate={today} valueFormat="D MMM YYYY" w={170} aria-label="Day" />}
      />
      {spaceQuery.isError ? <QueryState q={spaceQuery}>{null}</QueryState> : (
      <QueryState q={q}>
      {d && (
        <Stack gap="lg">
          {canManage && <Checklist d={d} spaceId={spaceId} />}
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
            <Stat label="Present" value={d.present} color={STATUS.present.color} hint={`of ${d.members} people`} />
            <Stat label="Late" value={d.counts.late ?? 0} color={STATUS.late.color} onOpen={() => setWho("late")} />
            {isToday
              ? <Stat label="Not checked in yet" value={d.not_checked_in} color={STATUS.absent.color} />
              : <Stat label="Absent" value={d.counts.absent ?? 0} color={STATUS.absent.color} onOpen={() => setWho("absent")} />}
            <Stat label="On leave or holiday" value={(d.counts.leave ?? 0) + (d.counts.holiday ?? 0)} color={STATUS.holiday.color} />
            <Stat label="Failed checks" value={d.failed_today} hint="that day" />
            {canManage ? (
              <Paper withBorder p="md" component={Link} to={`/spaces/${spaceId}/review`} style={{ textDecoration: "none", color: "inherit" }}>
                <Text size="sm" c="dimmed">Waiting for review</Text>
                <div className="stat-value" style={{ color: d.pending_reviews ? STATUS.late.color : undefined }}>{d.pending_reviews}</div>
                <Text size="xs" c="ledger.7" mt={2}>Open review queue</Text>
              </Paper>
            ) : <Stat label="Waiting for review" value={d.pending_reviews} />}
          </SimpleGrid>

          <Grid>
            <Grid.Col span={{ base: 12, lg: 8 }}>
            <Paper withBorder p="lg" h="100%">
              <Title order={4} mb="md">Last 14 days</Title>
              <BarChart
                h={240}
                data={d.trend.map((t) => ({ ...t, label: dayjs(t.day).format("D MMM") }))}
                dataKey="label"
                type="stacked"
                withLegend
                legendProps={{ verticalAlign: "bottom" }}
                series={statuses.map((s) => ({ name: s, label: STATUS[s].label, color: STATUS[s].color }))}
                gridAxis="y"
                tickLine="none"
              />
            </Paper>
            </Grid.Col>
            <Grid.Col span={{ base: 12, lg: 4 }}>
            <Paper withBorder p="lg" h="100%">
              <Title order={4} mb="sm">Kiosks</Title>
              <Text>
                <b>{d.devices.online}</b> of {d.devices.total} online
              </Text>
              {d.devices.clock_drift.length > 0 && (
                <Text size="sm" c="orange.8" mt={4}>Clock is off on: {d.devices.clock_drift.join(", ")}</Text>
              )}
              <Anchor component={Link} to={`/spaces/${spaceId}/devices`} size="sm">Manage kiosks</Anchor>
              <Title order={4} mt="lg" mb="sm">Why checks failed (7 days)</Title>
              {Object.keys(d.failure_reasons).length === 0 && <Text c="dimmed" size="sm">No failed checks.</Text>}
              <Stack gap={4}>
                {Object.entries(d.failure_reasons).map(([r, n]) => (
                  <Group key={r} justify="space-between"><Text size="sm">{reasonText(r)}</Text><Text size="sm" fw={600}>{n}</Text></Group>
                ))}
              </Stack>
            </Paper>
            </Grid.Col>
          </Grid>

          <SimpleGrid cols={{ base: 1, lg: 2 }}>
            <Paper withBorder p="lg">
              <Title order={4} mb="sm">Latest check-ins</Title>
              {d.recent.length === 0 ? <Text c="dimmed" size="sm">Nothing yet. Check-ins appear here as people scan at a kiosk.</Text> : (
                <Table verticalSpacing={6}>
                  <Table.Tbody>
                    {d.recent.map((e) => (
                      <Table.Tr key={e.id}>
                        <Table.Td w={110} style={{ whiteSpace: "nowrap" }}>
                          {dateIn(tz, new Date(e.occurred_at)) === today ? fmtTime(e.occurred_at, tz, space?.time_format) : fmtDateTime(e.occurred_at, tz, space?.time_format)}
                        </Table.Td>
                        <Table.Td>{e.member_name ?? <Text c="dimmed" span>Unknown</Text>}</Table.Td>
                        <Table.Td visibleFrom="sm"><Text size="sm" c="dimmed">{reasonText(e.reason)}</Text></Table.Td>
                        <Table.Td ta="right"><EventBadge status={e.status} /></Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Paper>
            <Paper withBorder p="lg">
              <Title order={4} mb="sm">Most often late (30 days)</Title>
              {d.late_top.length === 0 ? <Text c="dimmed" size="sm">Nobody was late.</Text> : (
                <Table verticalSpacing={6}>
                  <Table.Thead><Table.Tr><Table.Th>Name</Table.Th><Table.Th ta="right">Days</Table.Th><Table.Th ta="right">Total late</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>
                    {d.late_top.map((r) => (
                      <Table.Tr key={r.external_id}>
                        <Table.Td>{r.name} <Text span c="dimmed" size="sm">{r.external_id}</Text></Table.Td>
                        <Table.Td ta="right">{r.days}</Table.Td>
                        <Table.Td ta="right">{fmtMinutes(r.minutes)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Paper>
          </SimpleGrid>
        </Stack>
      )}
      </QueryState>
      )}
      <WhoModal spaceId={spaceId} day={day} status={who} tz={tz} tf={space?.time_format} onClose={() => setWho(null)} />
    </>
  );
}
