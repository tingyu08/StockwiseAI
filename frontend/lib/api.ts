import type { Market } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";
export const API_TOKEN_KEY = "stockwise-api-token";
const ACTIVE_JOBS_KEY = "stockwise-active-jobs";

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
    public readonly retryAfterSeconds: number | null = null,
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
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface StartedJob {
  started: true;
  job: string;
  run_id: number;
}

export interface ActiveJob {
  runId: number;
  name: string;
  startedAt: string;
}

export interface JobRun<T> {
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

export function listActiveJobs(): ActiveJob[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.sessionStorage.getItem(ACTIVE_JOBS_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter(
      (job): job is ActiveJob =>
        typeof job?.runId === "number" &&
        typeof job?.name === "string" &&
        typeof job?.startedAt === "string",
    );
  } catch {
    return [];
  }
}

export function trackActiveJob(job: Pick<ActiveJob, "runId" | "name">): void {
  if (typeof window === "undefined") return;
  const jobs = listActiveJobs().filter((item) => item.runId !== job.runId);
  jobs.push({ ...job, startedAt: new Date().toISOString() });
  window.sessionStorage.setItem(ACTIVE_JOBS_KEY, JSON.stringify(jobs));
  window.dispatchEvent?.(new Event("stockwise:jobs-changed"));
}

export function removeActiveJob(runId: number): void {
  if (typeof window === "undefined") return;
  const jobs = listActiveJobs().filter((job) => job.runId !== runId);
  window.sessionStorage.setItem(ACTIVE_JOBS_KEY, JSON.stringify(jobs));
  window.dispatchEvent?.(new Event("stockwise:jobs-changed"));
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

  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 30_000);
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
  } catch (error) {
    if (timedOut) throw new ApiError("連線等待逾時，請稍後重試", 408);
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }

  let envelope: Envelope<T> | null = null;
  try {
    const contentType = response.headers?.get("content-type") ?? "";
    if (contentType.includes("json") || typeof response.json === "function") {
      envelope = (await response.json()) as Envelope<T>;
    }
  } catch {
    envelope = null;
  }
  if (!response.ok || !envelope?.success) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("stockwise:unauthorized"));
    }
    const message = envelope?.error ??
      `服務暫時無法回應（HTTP ${response.status}），請稍後重試`;
    throw new ApiError(message, response.status, parseRetryAfter(response));
  }
  if (!envelope) {
    throw new ApiError("服務回應格式錯誤，請稍後重試", response.status);
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
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? 2_000;
  const timeoutMs = options.timeoutMs ?? 10 * 60_000;
  const startedAt = Date.now();

  while (Date.now() - startedAt <= timeoutMs) {
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const run = await apiRequest<JobRun<T>>(`/jobs/runs/${runId}`, {
      signal: options.signal,
    });
    if (run.status === "succeeded") return run.result as T;
    if (run.status === "failed") {
      throw new ApiError(run.error ?? "背景工作執行失敗", 502);
    }
    await abortableDelay(intervalMs, options.signal);
  }
  throw new ApiError("背景工作等待逾時，稍後可重新整理查看結果", 408);
}

function parseRetryAfter(response: Response): number | null {
  const value = response.headers?.get("retry-after");
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds;
  const retryAt = Date.parse(value);
  if (Number.isNaN(retryAt)) return null;
  return Math.max(0, Math.ceil((retryAt - Date.now()) / 1_000));
}

function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}
