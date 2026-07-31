/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import { DataStatus } from "./data-status";

// 執行時間一律是 UTC 且帶 Z：少了時區標記，new Date() 會當成當地時間，
// 對台灣就整整差 8 小時（見 lib/datetime）
vi.mock("@/lib/api", () => ({
  apiGet: vi.fn().mockResolvedValue({
    TW: {
      latest_price_date: "2026-07-10",
      latest_nav_date: null,
      latest_ai_runs: {
        news: "2026-07-10T22:12:00Z",
        routine: "2026-07-10T22:40:00Z",
        trade: null,
      },
      latest_overview_run: "2026-07-10T22:55:00Z",
      latest_successful_job: { id: 5, name: "ai-batch-tw", finished_at: "2026-07-10T15:01:00Z" },
    },
    US: { latest_price_date: "2026-07-09", latest_nav_date: null },
  }),
}));

it("資料日期與排程執行時間分開顯示", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(createElement(QueryClientProvider, { client }, createElement(DataStatus)));

  // 行情仍是資料日期（回答「資料到哪一天」）
  expect(await screen.findByText(/行情 2026-07-10/)).toBeInTheDocument();

  // AI 相關改顯示執行時刻，不再是 trade_date
  const routine = screen.getByText(/^例行/);
  expect(routine.textContent).toMatch(/例行 \d{2}\/\d{2} \d{2}:\d{2}/);
  expect(screen.getByText(/^簡報/).textContent).toMatch(/簡報 \d{2}\/\d{2} \d{2}:\d{2}/);

  // 沒跑過的要說「尚未執行」，不能顯示成空白或某個資料日期
  expect(screen.getByText(/交易 尚未執行/)).toBeInTheDocument();

  expect(screen.getByText(/最近工作 ai-batch-tw/)).toBeInTheDocument();
});

it("時間戳以 UTC 解讀而非當地時間", async () => {
  const { formatRunTime } = await import("@/lib/datetime");
  const utc = "2026-07-10T22:40:00Z";
  const expected = new Date(utc);
  const pad = (n: number) => String(n).padStart(2, "0");

  expect(formatRunTime(utc)).toBe(
    `${pad(expected.getMonth() + 1)}/${pad(expected.getDate())} ${pad(
      expected.getHours(),
    )}:${pad(expected.getMinutes())}`,
  );
  // 沒有 Z 的字串會被當成當地時間 → 兩者必須不同，這就是加 Z 的理由
  expect(formatRunTime("2026-07-10T22:40:00")).not.toBe(formatRunTime(utc));
  expect(formatRunTime(null)).toBeNull();
  expect(formatRunTime("not-a-date")).toBeNull();
});
