/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { trackActiveJob } from "@/lib/api";
import { JobCenter } from "./job-center";

function renderJobCenter(queryClient = new QueryClient()) {
  render(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(JobCenter),
    ),
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        success: true,
        data: { status: "failed", result: null, error: "provider timeout" },
        error: null,
        meta: null,
      }),
    }),
  );
});

afterEach(cleanup);

it("restores active jobs and offers retry after a failure", async () => {
  trackActiveJob({ runId: 17, name: "routine-analysis" });
  renderJobCenter();

  fireEvent.click(screen.getAllByRole("button")[0]);
  expect(screen.getByText("routine-analysis")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("provider timeout")).toBeInTheDocument());
  expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(3);
});

it("invalidates dashboard and prices when stock sync succeeds", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        success: true,
        data: { status: "succeeded", result: {}, error: null },
        error: null,
        meta: null,
      }),
    }),
  );
  trackActiveJob({ runId: 18, name: "sync-tw-2330" });
  const queryClient = new QueryClient();
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

  renderJobCenter(queryClient);

  await waitFor(() => {
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["stock-dashboard", "tw", "2330"],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["prices", "tw", "2330"],
    });
  });
});
