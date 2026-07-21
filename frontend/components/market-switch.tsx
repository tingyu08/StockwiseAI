"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { MARKET_LABELS, useMarketStore, type Market } from "@/stores/market";

const MARKETS: Market[] = ["tw", "us"];

/** 全站市場切換 Radio Button，狀態同步到 URL（?market=）方便分享連結。 */
export function MarketSwitch() {
  const { market, setMarket } = useMarketStore();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // 初始載入：URL 有 market 參數時以 URL 為準
  useEffect(() => {
    const fromUrl = searchParams.get("market");
    if (fromUrl === "tw" || fromUrl === "us") setMarket(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (next: Market) => {
    setMarket(next);
    // 個股頁的代號屬於單一市場，切換市場後導回儀表板（避免停在他市場的個股）
    if (pathname.startsWith("/stock/")) {
      router.push(`/?market=${next}`);
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    params.set("market", next);
    router.replace(`${pathname}?${params.toString()}`);
  };

  return (
    <fieldset className="flex items-center gap-1 rounded-lg border border-neutral-300 p-1 dark:border-neutral-700">
      <legend className="sr-only">選擇市場</legend>
      {MARKETS.map((m) => (
        <label
          key={m}
          className={`cursor-pointer rounded-md px-3 py-1 text-sm transition-colors ${
            market === m
              ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
              : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          }`}
        >
          <input
            type="radio"
            name="market"
            value={m}
            checked={market === m}
            onChange={() => handleChange(m)}
            className="sr-only"
          />
          {MARKET_LABELS[m]}
        </label>
      ))}
    </fieldset>
  );
}
