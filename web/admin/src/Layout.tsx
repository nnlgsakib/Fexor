import { AppShell, Burger, Divider, Group, NavLink, ScrollArea, Select, Stack, Text, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconCalendarTime, IconChecklist, IconDeviceDesktop, IconGauge, IconKey, IconListDetails,
  IconLogout, IconPlugConnected, IconSettings, IconShieldLock, IconUserCircle, IconUsers, IconUsersGroup,
  IconLayoutGrid,
} from "@tabler/icons-react";
import { NavLink as RouterLink, Outlet, useLocation, useNavigate, useParams } from "react-router";
import { useMe } from "./auth";

const SPACE_NAV = [
  { to: "", label: "Overview", icon: IconGauge },
  { to: "register", label: "Register", icon: IconCalendarTime },
  { to: "people", label: "People", icon: IconUsers },
  { to: "activity", label: "Check-ins", icon: IconListDetails },
  { to: "review", label: "Review queue", icon: IconChecklist },
  { to: "schedule", label: "Schedule & holidays", icon: IconCalendarTime },
  { to: "devices", label: "Kiosks", icon: IconDeviceDesktop },
  { to: "settings", label: "Settings", icon: IconSettings },
];

export function Layout() {
  const { me, logout } = useMe();
  const [opened, { toggle, close }] = useDisclosure();
  const { spaceId } = useParams();
  const nav = useNavigate();
  const loc = useLocation();

  const link = (to: string, label: string, Icon: typeof IconGauge, end = false) => (
    <NavLink key={to} component={RouterLink} to={to} end={end} label={label} onClick={close}
      leftSection={<Icon size={18} stroke={1.6} />} style={{ borderRadius: 6 }} />
  );

  return (
    <AppShell header={{ height: 56, collapsed: false }} navbar={{ width: 264, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header px="md">
        <Group h="100%" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <UnstyledButton onClick={() => nav("/spaces")}>
              <Group gap={10}>
                <img src="/favicon.svg" width={26} height={26} alt="" />
                <Text fw={700} fz={19}>Fexor</Text>
              </Group>
            </UnstyledButton>
          </Group>
          <Text size="sm" c="dimmed" visibleFrom="xs">{me.name}</Text>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section>
          <Select
            label="Space"
            placeholder={me.spaces.length ? "Choose a space" : "No spaces yet"}
            data={me.spaces.map((s) => ({ value: s.id, label: s.name }))}
            value={spaceId ?? null}
            onChange={(v) => { if (v) { nav(`/spaces/${v}`); close(); } }}
            searchable
            nothingFoundMessage="No match"
            mb="sm"
          />
          {link("/spaces", "All spaces", IconLayoutGrid, true)}
        </AppShell.Section>
        <AppShell.Section grow component={ScrollArea} mt="xs">
          {spaceId && (
            <Stack gap={2}>
              {SPACE_NAV.map((n) => link(`/spaces/${spaceId}${n.to ? `/${n.to}` : ""}`, n.label, n.icon, n.to === ""))}
            </Stack>
          )}
          <Divider my="sm" />
          <Stack gap={2}>
            {me.is_superadmin && link("/admins", "Admins", IconUsersGroup)}
            {me.is_superadmin && link("/integrations", "Integrations", IconPlugConnected)}
            {me.is_superadmin && link("/audit", "Audit log", IconShieldLock)}
            {link("/account", "My account", IconUserCircle)}
          </Stack>
        </AppShell.Section>
        <AppShell.Section>
          {!me.totp_enabled && !loc.pathname.startsWith("/account") && (
            <NavLink component={RouterLink} to="/account" label="Turn on two-step sign-in" c="orange.8"
              leftSection={<IconKey size={18} />} />
          )}
          <NavLink label="Sign out" leftSection={<IconLogout size={18} stroke={1.6} />} onClick={logout} />
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main bg="#f6f7f5">
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
