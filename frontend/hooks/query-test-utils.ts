import type { QueryClient, QueryKey } from "@tanstack/react-query";

/**
 * 取出某個 query 實際生效的 staleTime（測試用）。
 *
 * 為什麼需要這個包裝：react-query v5 把設定拆成兩層——`Query.options` 的型別是
 * `QueryOptions`（queryKey/queryFn/gcTime 等 fetch 層設定），而 `staleTime` 屬於
 * observer 層的 `QueryObserverOptions`。但 observer 建立 Query 時會把 defaulted
 * options 一併寫入，執行期讀得到值，型別上卻沒有宣告。
 *
 * 直接寫 `query.options.staleTime` 因此會產生 TS2339，過去長期有 7 個這樣的
 * 常駐錯誤——型別檢查一旦習慣性有紅字，真正的錯誤就會被淹沒。這裡把那道
 * 落差收斂到單一處並寫明原因，而不是在每個測試裡各自 `as any`。
 *
 * 仍保有偵錯能力：若哪天 react-query 不再把 staleTime 併入 Query.options，
 * 這裡會回傳 undefined，斷言值的測試就會失敗，而不是默默通過。
 */
export function staleTimeOf(
  client: QueryClient,
  queryKey: QueryKey,
): number | undefined {
  const query = client.getQueryCache().find({ queryKey });
  return (query?.options as { staleTime?: number } | undefined)?.staleTime;
}
