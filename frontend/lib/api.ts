import type { Market } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";
export const API_TOKEN_KEY = "stockwise-api-token";

export interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  meta: { total?: number; page?: number; limit?: number } | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  params?: Record<string, string>;
  market?: Market;
  body?: unknown;
  headers?: Record<string, string>;
}

export interface StartedJob {
  started: true;
  job: string;
  run_id: number;
}

interface JobRun<T> {
  status: "queued" | "running" | "succeeded" | "failed";
  result: T | null;
  error: string | null;
}

export function getApiToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(API_TOKEN_KEY);
}

export function setApiToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(API_TOKEN_KEY, token);
  else window.sessionStorage.removeItem(API_TOKEN_KEY);
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const url = new URL(`/api/v1${path}`, API_BASE);
  if (options.market) url.searchParams.set("market", options.market.toUpperCase());
  for (const [key, value] of Object.entries(options.params ?? {})) {
    url.searchParams.set(key, value);
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };
  const token = getApiToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const envelope = (await response.json()) as Envelope<T>;
  if (!response.ok || !envelope.success) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("stockwise:unauthorized"));
    }
    throw new ApiError(envelope.error ?? "發生未知錯誤，請稍後再試", response.status);
  }
  return envelope.data as T;
}

/** 統一 API client：解包 envelope、注入 market 參數、拋出使用者可讀的錯誤。 */
export async function apiGet<T>(
  path: string,
  params: Record<string, string> = {},
  market?: Market,
): Promise<T> {
  return apiRequest<T>(path, { params, market });
}

export async function waitForJob<T>(
  runId: number,
  options: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? 2_000;
  const timeoutMs = options.timeoutMs ?? 10 * 60_000;
  const startedAt = Date.now();

  while (Date.now() - startedAt <= timeoutMs) {
    const run = await apiGet<JobRun<T>>(`/jobs/runs/${runId}`);
    if (run.status === "succeeded") return run.result as T;
    if (run.status === "failed") {
      throw new ApiError(run.error ?? "背景工作執行失敗", 502);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new ApiError("背景工作等待逾時，稍後可重新整理查看結果", 408);
}
