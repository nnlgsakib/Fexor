import { Alert, Badge, Button, Center, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useParams } from "react-router";
import { api, ApiError } from "./api";
import { STATUS } from "./theme";
import type { Space } from "./types";

export function useSpace() {
  const { spaceId = "" } = useParams();
  const q = useQuery({ queryKey: ["space", spaceId], queryFn: () => api<Space>(`/spaces/${spaceId}`) });
  const role = q.data?.role;
  return {
    spaceId,
    space: q.data,
    spaceQuery: q,
    canManage: role === "owner" || role === "manager",
    isOwner: role === "owner",
    /** "Today" where the space is, not where the admin's browser is. */
    today: dateIn(q.data?.timezone),
  };
}

/** YYYY-MM-DD in a time zone (the browser's when none is given). */
export function dateIn(tz?: string, at: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
  } catch {
    return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
  }
}

export const today = () => dateIn();

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-end" mb="lg" wrap="wrap" gap="md">
      <Stack gap={4}>
        <Title order={2} fz={26}>{title}</Title>
        {description && <Text c="dimmed" maw={720}>{description}</Text>}
      </Stack>
      {actions && <Group gap="sm" wrap="wrap">{actions}</Group>}
    </Group>
  );
}

/** Loading spinner, or an error with a retry button; renders children once data is there. */
type QueryLike = Pick<UseQueryResult<unknown>, "isPending" | "isError" | "error" | "refetch">;

export function QueryState({ q, children }: { q: QueryLike; children: ReactNode }) {
  if (q.isPending) return <Center py="xl"><Loader size="sm" aria-label="Loading" /></Center>;
  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Alert color={notFound ? "gray" : "red"} variant="light" title={notFound ? "Not found" : "Couldn't load this"}>
        <Stack gap="xs" align="flex-start">
          <Text size="sm">{notFound ? "It doesn't exist or you no longer have access to it." : q.error?.message}</Text>
          {!notFound && <Button size="xs" variant="default" onClick={() => q.refetch()}>Try again</Button>}
        </Stack>
      </Alert>
    );
  }
  return <>{children}</>;
}

/** Ask before anything that can't be undone or that signs people out. */
export function confirmAction({ title, text, confirm, onConfirm, danger = true }: {
  title: string; text: ReactNode; confirm: string; onConfirm: () => unknown; danger?: boolean;
}) {
  modals.openConfirmModal({
    title,
    children: typeof text === "string" ? <Text size="sm">{text}</Text> : text,
    labels: { confirm, cancel: "Cancel" },
    confirmProps: danger ? { color: "red" } : undefined,
    onConfirm,
  });
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status];
  if (!s) return <Badge variant="light" color="gray">{status}</Badge>;
  return <Badge variant="filled" color={s.color} radius="sm" tt="none">{s.label}</Badge>;
}

export const EVENT_COLORS = { accepted: "teal", rejected: "red", pending: "yellow" } as const;
export const EVENT_LABELS = { accepted: "Accepted", rejected: "Failed", pending: "Waiting for review" } as const;

export function EventBadge({ status }: { status: keyof typeof EVENT_COLORS }) {
  return <Badge variant="light" color={EVENT_COLORS[status]} tt="none">{EVENT_LABELS[status]}</Badge>;
}

export function fmtTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: tz });
}

/** Date and time; the year only when it isn't this year. */
export function fmtDateTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "–";
  const d = new Date(iso);
  const sameYear = dateIn(tz, d).slice(0, 4) === dateIn(tz).slice(0, 4);
  return d.toLocaleString([], {
    day: "numeric", month: "short", year: sameYear ? undefined : "numeric", hour: "2-digit", minute: "2-digit", timeZone: tz,
  });
}

export function fmtDate(isoDate: string | null | undefined) {
  if (!isoDate) return "–";
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

export function fmtMinutes(m: number) {
  if (!m) return "–";
  const h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}

export function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

const REASONS: Record<string, string> = {
  face_mismatch: "Face did not match",
  face_matches_other: "Face matched someone else",
  liveness_failed: "Live face check failed",
  face_changed: "Face changed during check",
  challenge: "Movement check failed",
  timeout: "Timed out",
  qr_invalid: "Invalid QR code",
  qr_unknown_person: "QR from another space",
  qr_revoked: "Old QR code used",
  member_inactive: "Inactive person",
  no_face_enrolled: "No face photo on file",
  unknown_member: "Unknown person",
  manual_entry: "Added by admin",
  verified: "Verified",
  review_requested: "Review requested",
  manual_review_requested: "Review requested",
  device_clock_ahead: "Kiosk clock ahead",
  device_clock_behind: "Kiosk clock behind, or very old check-in",
  error: "Kiosk error",
};

export function reasonText(reason: string) {
  if (!reason) return "";
  const key = reason.split(";")[0].split(":")[0];
  return REASONS[key] ?? key.replaceAll("_", " ");
}

const FLAGS: Record<string, string> = {
  late: "late", missing_out: "no check-out", early_leave: "left early", overtime: "overtime",
  worked_unscheduled: "worked on a day off",
};
export const flagText = (flags: string[]) => flags.map((f) => FLAGS[f] ?? f.replaceAll("_", " ")).join(", ");

export function notifyError(e: unknown) {
  notifications.show({ color: "red", title: "That didn't work", message: e instanceof Error ? e.message : String(e) });
}

export function notifyOk(message: string) {
  notifications.show({ color: "ledger", message });
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
