import { Badge, Button, Group, Image, Paper, SegmentedControl, Stack, Table, Text, Textarea } from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, url } from "../api";
import type { AttendanceEvent } from "../types";
import { EVENT_COLORS, PageHeader, fmtDateTime, notifyError, notifyOk, reasonText, today, useSpace } from "../ui";

function Scores({ s }: { s: AttendanceEvent["scores"] }) {
  const parts = [];
  if (typeof s.match === "number") parts.push(`face ${Math.round(s.match * 100)}%`);
  if (typeof s.liveness === "number") parts.push(`live ${Math.round(s.liveness * 100)}%`);
  return <Text size="xs" c="dimmed">{parts.join(", ")}</Text>;
}

export function Activity() {
  const { spaceId, space } = useSpace();
  const [range, setRange] = useState<[string | null, string | null]>([today(), today()]);
  const [status, setStatus] = useState("all");
  const q = useQuery({
    queryKey: ["events", spaceId, range, status],
    queryFn: () => api<AttendanceEvent[]>(`/spaces/${spaceId}/events`, {
      query: { start: range[0], end: range[1] ?? range[0], status: status === "all" ? undefined : status, limit: 2000 },
    }),
    enabled: !!range[0],
    refetchInterval: 15_000,
  });
  const tz = space?.timezone;
  return (
    <>
      <PageHeader title="Check-ins" description="Every scan at a kiosk, including failed ones, plus check-ins added by admins." />
      <Group mb="md">
        <DatePickerInput type="range" value={range} onChange={setRange} maxDate={today()} w={260} aria-label="Dates" allowSingleDateInRange />
        <SegmentedControl value={status} onChange={setStatus} data={[
          { value: "all", label: "All" }, { value: "accepted", label: "Accepted" },
          { value: "rejected", label: "Failed" }, { value: "pending", label: "Waiting for review" }]} />
      </Group>
      <Paper withBorder>
        <Table.ScrollContainer minWidth={760}>
          <Table verticalSpacing="sm" highlightOnHover>
            <Table.Thead><Table.Tr>
              <Table.Th>When</Table.Th><Table.Th>Person</Table.Th><Table.Th>Result</Table.Th>
              <Table.Th>Details</Table.Th><Table.Th>Kiosk</Table.Th>
            </Table.Tr></Table.Thead>
            <Table.Tbody>
              {q.data?.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{fmtDateTime(e.occurred_at, tz)}</Table.Td>
                  <Table.Td>{e.member_name ?? <Text c="dimmed" span>Unknown</Text>} <Text span size="xs" c="dimmed">{e.external_id}</Text></Table.Td>
                  <Table.Td><Badge variant="light" color={EVENT_COLORS[e.status]} tt="none">{e.status === "rejected" ? "failed" : e.status}</Badge></Table.Td>
                  <Table.Td>
                    <Text size="sm">{reasonText(e.reason)}{e.note ? `: ${e.note}` : ""}</Text>
                    <Scores s={e.scores} />
                  </Table.Td>
                  <Table.Td>{e.source === "admin" ? <Text size="sm">Admin{e.reviewed_by ? ` (${e.reviewed_by})` : ""}</Text> : e.device}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {q.data?.length === 0 && <Text p="lg" c="dimmed">No check-ins in this period.</Text>}
      </Paper>
    </>
  );
}

export function Review() {
  const { spaceId, space, canManage } = useSpace();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["review", spaceId], queryFn: () => api<AttendanceEvent[]>(`/spaces/${spaceId}/review`), enabled: canManage });
  const [notes, setNotes] = useState<Record<string, string>>({});
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api(`/spaces/${spaceId}/events/${id}/review`, { json: { decision, note: notes[id] ?? "" } }),
    onSuccess: (_, v) => {
      notifyOk(v.decision === "approve" ? "Approved and counted" : "Rejected");
      qc.invalidateQueries({ queryKey: ["review", spaceId] });
      qc.invalidateQueries({ queryKey: ["dashboard", spaceId] });
    },
    onError: notifyError,
  });
  if (!canManage) return <Text>Only managers can review check-ins.</Text>;
  return (
    <>
      <PageHeader title="Review queue"
        description="People who could not be verified at a kiosk asked for a manual check. Approve only if you can confirm they were there." />
      {q.data?.length === 0 && <Paper withBorder p="xl"><Text fw={600}>Nothing to review</Text><Text c="dimmed">Requests from kiosks show up here.</Text></Paper>}
      <Stack>
        {q.data?.map((e) => (
          <Paper key={e.id} withBorder p="md">
            <Group align="flex-start" wrap="nowrap">
              {e.has_snapshot && <Image src={url(`/spaces/${spaceId}/events/${e.id}/snapshot`)} w={110} h={130} radius="sm" fit="cover" alt="Kiosk snapshot" />}
              <Stack gap={6} style={{ flex: 1 }}>
                <Group justify="space-between">
                  <Text fw={650}>{e.member_name ?? "Unknown person"} <Text span c="dimmed" size="sm">{e.external_id}</Text></Text>
                  <Text size="sm" c="dimmed">{fmtDateTime(e.occurred_at, space?.timezone)}, {e.device ?? "admin"}</Text>
                </Group>
                <Text size="sm">Failed because: {reasonText(e.reason.replace("review_requested:", ""))}</Text>
                <Scores s={e.scores} />
                <Textarea placeholder="Note for the record (optional)" autosize minRows={1} value={notes[e.id] ?? ""}
                  onChange={(ev) => { const v = ev.currentTarget.value; setNotes((n) => ({ ...n, [e.id]: v })); }} />
                <Group>
                  <Button onClick={() => decide.mutate({ id: e.id, decision: "approve" })} disabled={!e.member_id}>Approve</Button>
                  <Button variant="default" color="red" onClick={() => decide.mutate({ id: e.id, decision: "reject" })}>Reject</Button>
                </Group>
              </Stack>
            </Group>
          </Paper>
        ))}
      </Stack>
    </>
  );
}
