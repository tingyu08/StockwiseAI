/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import { DataStatus } from "./data-status";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn().mockResolvedValue({
    TW: { latest_price_date: "2026-07-10", latest_nav_date: null, latest_ai_date: "2026-07-10" },
    US: { latest_price_date: "2026-07-09", latest_nav_date: null, latest_ai_date: null },
  }),
}));

it("shows market data freshness", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(createElement(QueryClientProvider, { client }, createElement(DataStatus)));

  expect(await screen.findByText(/行情 2026-07-10/)).toBeInTheDocument();
});
