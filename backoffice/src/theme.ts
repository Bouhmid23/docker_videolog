import { createTheme, type MantineColorsTuple } from "@mantine/core";

const videologCyan: MantineColorsTuple = [
  "#e6f7ff",
  "#cceeff",
  "#99dcff",
  "#66cbff",
  "#38bbff",
  "#18abf8",
  "#0f95dc",
  "#0a77b3",
  "#065a8a",
  "#043d61",
];

const videologDark: MantineColorsTuple = [
  "#eef2f6",
  "#c6cfd9",
  "#98a3b0",
  "#6c7887",
  "#3e4957",
  "#232b35",
  "#151a21",
  "#0f1318",
  "#090b0e",
  "#050608",
];

export const videologTheme = createTheme({
  primaryColor: "cyan",
  primaryShade: 5,
  colors: {
    cyan: videologCyan,
    dark: videologDark,
  },
  fontFamily: '"Montserrat", system-ui, -apple-system, "Segoe UI", sans-serif',
  headings: {
    fontFamily: '"TradeGothicBold", "Montserrat", sans-serif',
    fontWeight: "700",
  },
  defaultRadius: "md",
  components: {
    Paper: {
      defaultProps: {
        radius: 14,
      },
    },
    Card: {
      defaultProps: {
        radius: 14,
      },
    },
    Button: {
      defaultProps: {
        radius: 10,
      },
    },
    TextInput: {
      defaultProps: {
        radius: 10,
      },
    },
    Select: {
      defaultProps: {
        radius: 10,
      },
    },
    Badge: {
      defaultProps: {
        radius: "xl",
      },
    },
  },
});

