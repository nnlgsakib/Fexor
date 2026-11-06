import { Badge, Group, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import type { ReactNode } from "react";
import { useParams } from "react-router";
import { api } from "./api";
import { STATUS } from "./theme";
import type { Space } from "./types";

export function useSpace() {
  const { spaceId = "" } = useParams();
  const q = useQuery({ queryKey: ["space", spaceId], queryFn: () => api<Space>(`/spaces/${spaceId}`) });
  const role = q.data?.role;
  return {
    spaceId,
    space: q.data,
    canManage: role === "owner" || role === "manager",
    isOwner: role === "owner",
  };
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-end" mb="lg" wrap="wrap" gap="md">
      <Stack gap={4}>
        <Title order={2} fz={26}>{title}</Title>
        {description && <Text c="dimmed" maw={720}>{description}</Text>}
      </Stack>
      {actions && <Group gap="sm">{actions}</Group>}
    </Group>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status];
  if (!s) return <Badge variant="light" color="gray">{status}</Badge>;
  return <Badge variant="filled" color={s.color} radius="sm" tt="none">{s.label}</Badge>;
}

export const EVENT_COLORS = { accepted: "teal", rejected: "red", pending: "yellow" } as const;

export function fmtTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: tz });
}

export function fmtDateTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString([], {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: tz,
  });
}

export function fmtMinutes(m: number) {
  if (!m) return "–";
  const h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}

export const today = () => dayjs().format("YYYY-MM-DD");

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
  device_clock_ahead: "Kiosk clock ahead",
};

export function reasonText(reason: string) {
  if (!reason) return "";
  const key = reason.split(";")[0].split(":")[0];
  return REASONS[key] ?? key.replaceAll("_", " ");
}

export function notifyError(e: unknown) {
  notifications.show({ color: "red", title: "That didn't work", message: e instanceof Error ? e.message : String(e) });
}

export function notifyOk(message: string) {
  notifications.show({ color: "ledger", message });
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
