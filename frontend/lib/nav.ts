import type { Market } from "@/stores/market";

export interface NavItem {
  href: string;
  label: string;
  /** 限定適用市場；未指定＝全市場皆有。 */
  markets?: Market[];
}

/**
 * 導覽項目與其市場適用性的單一真相來源。
 * 折溢價僅台股：免費資料源沒有美股 ETF 淨值（見後端 premium_service）。
 */
export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "儀表板" },
  { href: "/compare", label: "比較" },
  { href: "/premium", label: "折溢價", markets: ["tw"] },
  { href: "/simulation", label: "模擬交易" },
  { href: "/backtest", label: "回測" },
];

export function navItemsFor(market: Market): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.markets || item.markets.includes(market));
}

/** 該路徑在此市場是否可用（切換市場時用來決定要不要導回儀表板）。 */
export function isPathAvailable(pathname: string, market: Market): boolean {
  const item = NAV_ITEMS.find(
    (nav) => nav.href !== "/" && pathname.startsWith(nav.href),
  );
  return !item?.markets || item.markets.includes(market);
}
