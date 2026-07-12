/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import { DataStatus } from "./data-status";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn().mockResolvedValue({
    TW: {
      latest_price_date: "2026-07-10",
      latest_nav_date: null,
      latest_ai_date: "2026-07-10",
      latest_ai_dates: { news: "2026-07-10", routine: "2026-07-10", trade: "2026-07-09" },
      latest_successful_job: { id: 5, name: "ai-batch-tw", finished_at: "2026-07-10T15:01:00" },
    },
    US: { latest_price_date: "2026-07-09", latest_nav_date: null, latest_ai_date: null },
  }),
}));

it("shows market data freshness", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(createElement(QueryClientProvider, { client }, createElement(DataStatus)));

  expect(await screen.findByText(/行情 2026-07-10/)).toBeInTheDocument();
  expect(screen.getByText(/例行 2026-07-10/)).toBeInTheDocument();
  expect(screen.getByText(/交易 2026-07-09/)).toBeInTheDocument();
  expect(screen.getByText(/最近工作 ai-batch-tw/)).toBeInTheDocument();
});
