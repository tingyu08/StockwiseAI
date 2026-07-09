"use client";

import { useToggleAiManaged } from "@/hooks/use-simulation";
import { useWatchlist } from "@/hooks/use-stocks";

/** AI 託管管理：勾選哪些自選股交給 AI 在模擬帳戶自動操作。 */
export function AiManagedPanel() {
  const { data: items, isLoading } = useWatchlist();
  const toggle = useToggleAiManaged();

  if (isLoading) return <p className="text-sm text-neutral-500">載入中…</p>;
  if (!items?.length)
    return <p className="text-sm text-neutral-500">自選清單為空，請先到儀表板加入股票。</p>;

  const managedCount = items.filter((w) => w.ai_managed).length;

  return (
    <div className="space-y-2">
      <p className="text-xs text-neutral-500">
        勾選的股票會在每日 AI 批次分析後，由 AI 依報告自動下模擬單（信心 ≥70% 才買進，
        單一持股上限 20%、保留 10% 現金、跌破停損強制出清）。目前託管 {managedCount} 檔。
      </p>
      <ul className="grid gap-1 sm:grid-cols-2">
        {items.map((w) => (
          <li key={w.symbol}>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-900">
              <input
                type="checkbox"
                checked={w.ai_managed}
                disabled={toggle.isPending}
                onChange={(e) =>
                  toggle.mutate({ symbol: w.symbol, ai_managed: e.target.checked })
                }
              />
              <span className="font-mono font-semibold">{w.symbol}</span>
              <span className="text-neutral-500">{w.name}</span>
            </label>
          </li>
        ))}
      </ul>
      {toggle.isError && (
        <p className="text-sm text-red-500">{(toggle.error as Error).message}</p>
      )}
    </div>
  );
}
