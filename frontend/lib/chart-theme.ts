/** Recharts Tooltip 共用樣式：跟隨 --background/--foreground，深色主題下日期與數值才可讀。 */
export const TOOLTIP_CONTENT_STYLE = {
  fontSize: 12,
  backgroundColor: "var(--background)",
  border: "1px solid #52525b",
  borderRadius: 8,
} as const;

export const TOOLTIP_LABEL_STYLE = {
  color: "var(--foreground)",
  fontWeight: 500,
} as const;
