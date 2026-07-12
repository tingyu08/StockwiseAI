/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { trackActiveJob } from "@/lib/api";
import { JobCenter } from "./job-center";

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

it("restores active jobs and offers retry after a failure", async () => {
  trackActiveJob({ runId: 17, name: "每日簡報" });
  render(createElement(JobCenter));

  fireEvent.click(screen.getByRole("button", { name: /工作/ }));
  expect(screen.getByText("每日簡報")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "重試" })).toBeInTheDocument());
});
