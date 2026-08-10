/**
 * 排程執行時間的顯示。
 *
 * 後端的 created_at / finished_at 都是 UTC，且會帶時區標記（見
 * time_service.as_utc_iso）。少了那個標記，new Date() 會把字串當成當地時間
 * 解讀——對台灣就整整差 8 小時。
 *
 * 這裡刻意顯示「執行時間」而非資料日期：AI 分析的 trade_date 是它所根據的
 * 收盤日，美股資料鏈天生落後一個 session，畫面上並排就會讓人誤以為排程停擺。
 */

/** 執行時間 → 當地「MM/DD HH:mm」。無值或無法解析回傳 null。 */
export function formatRunTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(at.getMonth() + 1)}/${pad(at.getDate())} ${pad(at.getHours())}:${pad(
    at.getMinutes(),
  )}`;
}

/** 完整當地時間，給 title 屬性用（滑鼠移上去看得到年份與秒數）。 */
export function formatRunTimeFull(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return undefined;
  return at.toLocaleString();
}

const MARKET_TIMEZONES: Record<string, string> = {
  tw: "Asia/Taipei",
  us: "America/New_York",
};

/** 市場當地時間「YYYY-MM-DD HH:mm」。無值或無法解析回傳 null。
 *
 * 交易時刻要用市場所在時區看才有意義：台股成交寫 01:00Z，在台北就是開盤的
 * 09:00；美股寫 13:30Z，在紐約就是開盤的 09:30。改用瀏覽器時區的話，人在
 * 台灣看美股單會顯示 21:30，跟「開盤成交」對不起來。
 *
 * 這與 formatRunTime 的分工是刻意的：排程執行時間問的是「我這邊幾點跑的」，
 * 用瀏覽器時區；交易時刻問的是「在那個市場的第幾分鐘成交」，用市場時區。
 */
export function formatMarketTime(
  iso: string | null | undefined,
  market: string,
): string | null {
  if (!iso) return null;
  // 後端一律送帶 Z 的 ISO（見 time_service.as_utc_iso）。萬一少了標記，
  // new Date() 會當本地時間解讀——對台灣就整整差 8 小時，故補上。
  const normalized = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const at = new Date(normalized);
  if (Number.isNaN(at.getTime())) return null;

  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: MARKET_TIMEZONES[market] ?? "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(at);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  // hour12:false 在部分執行環境會把午夜給成 "24"
  const hour = get("hour") === "24" ? "00" : get("hour");
  return `${get("year")}-${get("month")}-${get("day")} ${hour}:${get("minute")}`;
}
