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
