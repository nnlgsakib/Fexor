import { Badge, Button, Group, Modal, Paper, Radio, Select, SimpleGrid, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api";
import { useMe } from "../auth";
import type { Space } from "../types";
import { PageHeader, QueryState, notifyError } from "../ui";

// Older browsers (Safari before 15.4) don't have supportedValuesOf: offer the common zones.
const FALLBACK_TZ = ["UTC", "Europe/London", "Europe/Berlin", "Africa/Lagos", "Africa/Nairobi", "Asia/Dubai",
  "Asia/Karachi", "Asia/Kolkata", "Asia/Dhaka", "Asia/Bangkok", "Asia/Singapore", "Asia/Tokyo",
  "Australia/Sydney", "America/Sao_Paulo", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"];
const ALL_TZ: string[] = (() => {
  try {
    return (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf("timeZone");
  } catch {
    return FALLBACK_TZ;
  }
})();
export const ROLE_LABEL = { owner: "Owner", manager: "Manager", viewer: "Viewer" } as const;

// Browsers list some zones under older names (Asia/Calcutta), so always include the saved value.
export const timezones = (current?: string | null) =>
  current && !ALL_TZ.includes(current) ? [current, ...ALL_TZ] : ALL_TZ;

const TEMPLATES = [
  { value: "office", label: "Office", text: "Check in and check out. Tracks hours, late arrivals and early leaving." },
  { value: "school", label: "School", text: "One check per day. Marks students present or late." },
  { value: "custom", label: "Custom", text: "Check in and out, no schedule yet. Set it up yourself." },
];

export function Spaces() {
  const { me, refresh } = useMe();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [opened, { open, close }] = useDisclosure();
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/spaces") });
  const [form, setForm] = useState({
    name: "", template: "office", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, country: "",
  });

  const countryOk = !form.country || /^[A-Z]{2,3}$/.test(form.country);
  const create = useMutation({
    mutationFn: () => api<Space>("/spaces", { json: { ...form, country: form.country || null } }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["spaces"] });
      refresh();
      close();
      nav(`/spaces/${s.id}`);
    },
    onError: notifyError,
  });

  return (
    <>
      <PageHeader
        title="Spaces"
        description="Each space has its own people, schedule, holidays, kiosks and reports. Use one per office, branch, shift or school session."
        actions={me.is_superadmin && <Button leftSection={<IconPlus size={16} />} onClick={open}>New space</Button>}
      />
      <QueryState q={spaces}>
      {spaces.data?.length === 0 && (
        <Paper withBorder p="xl" maw={560}>
          <Text fw={600} mb={4}>No spaces yet</Text>
          <Text c="dimmed" mb="md">
            {me.is_superadmin ? "Create your first space to start adding people and kiosks." : "Ask a super admin to give you access to a space."}
          </Text>
          {me.is_superadmin && <Button onClick={open}>Create a space</Button>}
        </Paper>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {spaces.data?.map((s) => (
          <UnstyledButton key={s.id} onClick={() => nav(`/spaces/${s.id}`)} aria-label={`Open ${s.name}`}>
          <Paper withBorder p="lg" h="100%">
            <Group justify="space-between" mb={6} wrap="nowrap">
              <Text fw={650} fz="lg" truncate>{s.name}</Text>
              <Badge variant="light" color={s.mode === "single" ? "blue" : "ledger"} tt="none">
                {s.mode === "single" ? "Single check" : "In and out"}
              </Badge>
            </Group>
            {s.description && <Text size="sm" c="dimmed" lineClamp={2}>{s.description}</Text>}
            <Text size="sm" c="dimmed">{s.timezone}</Text>
            <Text size="xs" c="dimmed" mt="sm">Your role: {ROLE_LABEL[s.role]}</Text>
          </Paper>
          </UnstyledButton>
        ))}
      </SimpleGrid>
      </QueryState>

      <Modal opened={opened} onClose={close} title="New space" size="lg">
        <Stack>
          <TextInput label="Name" placeholder="Head office, Night shift, Morning school" required
            value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
          <Radio.Group label="Kind of attendance" value={form.template} onChange={(v) => setForm({ ...form, template: v })}>
            <Stack gap="xs" mt={6}>
              {TEMPLATES.map((t) => (
                <Radio.Card key={t.value} value={t.value} p="sm" radius="sm" withBorder>
                  <Group wrap="nowrap" align="flex-start">
                    <Radio.Indicator />
                    <div>
                      <Text fw={600}>{t.label}</Text>
                      <Text size="sm" c="dimmed">{t.text}</Text>
                    </div>
                  </Group>
                </Radio.Card>
              ))}
            </Stack>
          </Radio.Group>
          <Group grow>
            <Select label="Time zone" data={timezones(form.timezone)} searchable value={form.timezone}
              onChange={(v) => setForm({ ...form, timezone: v ?? "UTC" })} />
            <TextInput label="Country code for holidays" placeholder="IN, US, GB…" maxLength={3}
              description="Two letters, used to add public holidays" error={countryOk ? undefined : "Use 2 letters, like IN or US"}
              value={form.country} onChange={(e) => setForm({ ...form, country: e.currentTarget.value.toUpperCase().trim() })} />
          </Group>
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>Cancel</Button>
            <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!form.name.trim() || !countryOk}>Create space</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
