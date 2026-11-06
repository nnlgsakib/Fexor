import { createTheme, type MantineColorsTuple } from "@mantine/core";

const ledger: MantineColorsTuple = [
  "#eaf4f0", "#d5e8e0", "#a8d0c1", "#79b7a0", "#53a185",
  "#3a9374", "#2a8064", "#1e6b58", "#135b4a", "#06483a",
];

export const theme = createTheme({
  primaryColor: "ledger",
  primaryShade: { light: 7, dark: 5 },
  colors: { ledger },
  fontFamily: '"Schibsted Grotesk", system-ui, sans-serif',
  headings: { fontFamily: '"Schibsted Grotesk", system-ui, sans-serif', fontWeight: "650" },
  defaultRadius: "sm",
  black: "#1b2320",
  components: {
    Button: { defaultProps: { radius: "sm" } },
    Paper: { defaultProps: { radius: "md" } },
  },
});

// Stamp-ink colours for attendance states; used by the register grid and badges.
export const STATUS: Record<string, { color: string; label: string; short: string }> = {
  present: { color: "#2e7d4f", label: "Present", short: "P" },
  late: { color: "#b7791f", label: "Late", short: "L" },
  half_day: { color: "#8c6a1c", label: "Half day", short: "H" },
  absent: { color: "#b3412f", label: "Absent", short: "A" },
  holiday: { color: "#4f63a6", label: "Holiday", short: "Ho" },
  leave: { color: "#6f5ba6", label: "Leave", short: "Lv" },
  off: { color: "#8f9a95", label: "Worked day off", short: "O" },
};
