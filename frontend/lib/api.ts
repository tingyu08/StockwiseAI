import type { Market } from "@/stores/market";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8123";

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

/** 統一 API client：解包 envelope、注入 market 參數、拋出使用者可讀的錯誤。 */
export async function apiGet<T>(
  path: string,
  params: Record<string, string> = {},
  market?: Market,
): Promise<T> {
  const url = new URL(`/api/v1${path}`, API_BASE);
  if (market) url.searchParams.set("market", market.toUpperCase()); // 後端使用 'TW' | 'US'
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const res = await fetch(url, { headers: { Accept: "application/json" } });
  const body = (await res.json()) as Envelope<T>;
  if (!res.ok || !body.success) {
    throw new ApiError(body.error ?? "發生未知錯誤，請稍後再試", res.status);
  }
  return body.data as T;
}
