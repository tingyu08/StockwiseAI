import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";

const okResponse = (data: unknown) => ({
  ok: true,
  status: 200,
  json: async () => ({ success: true, data, error: null, meta: null }),
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("API client", () => {
  it("adds the session bearer token to requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", {
      sessionStorage: { getItem: () => "single-user-secret" },
    });

    await api.apiGet("/usage");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer single-user-secret",
        }),
      }),
    );
  });

  it("provides one typed request function for JSON mutations", async () => {
    const request = (api as typeof api & {
      apiRequest?: <T>(path: string, options: unknown) => Promise<T>;
    }).apiRequest;
    expect(request).toBeTypeOf("function");
    if (!request) return;

    const fetchMock = vi.fn().mockResolvedValue(okResponse({ updated: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await request<{ updated: number }>("/watchlist/reorder", {
      method: "PUT",
      body: { market: "TW", items: [] },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ market: "TW", items: [] }),
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("polls a background job until its result is ready", async () => {
    const waitForJob = (api as typeof api & {
      waitForJob?: <T>(runId: number, options?: { intervalMs?: number }) => Promise<T>;
    }).waitForJob;
    expect(waitForJob).toBeTypeOf("function");
    if (!waitForJob) return;

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ status: "running", result: null }))
      .mockResolvedValueOnce(
        okResponse({ status: "succeeded", result: { summary: "完成" } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(waitForJob<{ summary: string }>(42, { intervalMs: 0 })).resolves.toEqual({
      summary: "完成",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
