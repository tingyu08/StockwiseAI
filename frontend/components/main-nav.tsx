"use client";

import Link from "next/link";

import { navItemsFor } from "@/lib/nav";
import { useMarketStore } from "@/stores/market";

/** 主導覽：依目前市場過濾（如折溢價僅台股顯示）。 */
export function MainNav() {
  const market = useMarketStore((s) => s.market);

  return (
    <nav className="flex gap-4 text-sm text-neutral-500">
      {navItemsFor(market).map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className="hover:text-neutral-900 dark:hover:text-white"
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
