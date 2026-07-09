"use client";

import Link from "next/link";
import { useState } from "react";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import {
  useCreateGroup,
  useDeleteGroup,
  useGroups,
  useReorderWatchlist,
  useSetGroup,
} from "@/hooks/use-groups";
import { useRemoveWatch, useWatchlist } from "@/hooks/use-stocks";
import type { WatchItem } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export function WatchlistPanel() {
  const market = useMarketStore((s) => s.market);
  const { data: items, isLoading, isError, error } = useWatchlist();
  const { data: groups } = useGroups();
  const removeWatch = useRemoveWatch();
  const createGroup = useCreateGroup();
  const deleteGroup = useDeleteGroup();
  const setGroup = useSetGroup();
  const reorder = useReorderWatchlist();
  const [newGroupName, setNewGroupName] = useState("");

  if (isLoading) return <p className="text-sm text-neutral-500">載入自選清單中…</p>;
  if (isError) return <p className="text-sm text-red-500">{(error as Error).message}</p>;

  const sorted = [...(items ?? [])].sort((a, b) => a.sort_order - b.sort_order);
  const sections: { id: number | null; name: string; items: WatchItem[] }[] = [
    ...(groups ?? []).map((g) => ({
      id: g.id as number | null,
      name: g.name,
      items: sorted.filter((w) => w.group_id === g.id),
    })),
    { id: null, name: "未分組", items: sorted.filter((w) => w.group_id === null) },
  ];

  const move = (item: WatchItem, dir: -1 | 1) => {
    // 只在同群組內交換順序，整批送出新排序
    const siblings = sorted.filter((w) => w.group_id === item.group_id);
    const idx = siblings.findIndex((w) => w.symbol === item.symbol);
    const target = idx + dir;
    if (target < 0 || target >= siblings.length) return;
    [siblings[idx], siblings[target]] = [siblings[target], siblings[idx]];
    const others = sorted.filter((w) => w.group_id !== item.group_id);
    const all = [...others, ...siblings];
    reorder.mutate(
      all.map((w, i) => ({
        symbol: w.symbol,
        group_id: w.group_id,
        sort_order: w.group_id === item.group_id
          ? siblings.findIndex((s) => s.symbol === w.symbol)
          : w.sort_order,
      })),
    );
  };

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (newGroupName.trim()) {
            createGroup.mutate(newGroupName.trim(), { onSuccess: () => setNewGroupName("") });
          }
        }}
        className="flex gap-2"
      >
        <input
          value={newGroupName}
          onChange={(e) => setNewGroupName(e.target.value)}
          placeholder="新群組名稱，如：半導體、高股息"
          className="flex-1 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button
          type="submit"
          disabled={!newGroupName.trim() || createGroup.isPending}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          ＋新增群組
        </button>
      </form>
      {createGroup.isError && (
        <p className="text-sm text-red-500">{(createGroup.error as Error).message}</p>
      )}

      {sections.map((section) =>
        section.id === null && section.items.length === 0 && sections.length > 1 ? null : (
          <div key={section.id ?? "ungrouped"}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-sm font-medium text-neutral-500">
                {section.name}
                <span className="ml-1 text-xs">（{section.items.length}）</span>
              </h3>
              {section.id !== null && (
                <button
                  onClick={() => deleteGroup.mutate(section.id!)}
                  className="text-xs text-neutral-400 hover:text-red-500"
                  title="刪除群組（股票會移回未分組）"
                >
                  刪除群組
                </button>
              )}
            </div>
            {section.items.length === 0 ? (
              <p className="rounded-lg border border-dashed border-neutral-200 px-4 py-2 text-xs text-neutral-400 dark:border-neutral-800">
                （空群組，用股票列右側的下拉選單把股票移進來）
              </p>
            ) : (
              <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
                {section.items.map((w, i) => (
                  <li key={w.symbol} className="flex items-center gap-2 px-4 py-2">
                    <div className="flex flex-col">
                      <button
                        onClick={() => move(w, -1)}
                        disabled={i === 0 || reorder.isPending}
                        className="text-xs leading-none text-neutral-400 hover:text-neutral-900 disabled:opacity-20 dark:hover:text-white"
                        aria-label="上移"
                      >
                        ▲
                      </button>
                      <button
                        onClick={() => move(w, 1)}
                        disabled={i === section.items.length - 1 || reorder.isPending}
                        className="text-xs leading-none text-neutral-400 hover:text-neutral-900 disabled:opacity-20 dark:hover:text-white"
                        aria-label="下移"
                      >
                        ▼
                      </button>
                    </div>
                    <Link
                      href={`/stock/${w.symbol}?market=${market}`}
                      className="flex-1 text-sm hover:underline"
                    >
                      <span className="font-mono font-semibold">{w.symbol}</span>
                      <span className="ml-2 text-neutral-500">{w.name}</span>
                      {w.ai_managed && (
                        <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900 dark:text-blue-200">
                          AI 託管中
                        </span>
                      )}
                    </Link>
                    <select
                      value={w.group_id ?? ""}
                      onChange={(e) =>
                        setGroup.mutate({
                          symbol: w.symbol,
                          groupId: e.target.value === "" ? null : Number(e.target.value),
                        })
                      }
                      className="rounded border border-neutral-200 bg-transparent px-1 py-0.5 text-xs text-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
                      title="移動到群組"
                    >
                      <option value="">未分組</option>
                      {groups?.map((g) => (
                        <option key={g.id} value={g.id}>{g.name}</option>
                      ))}
                    </select>
                    <button
                      onClick={() => removeWatch.mutate(w.symbol)}
                      disabled={removeWatch.isPending}
                      className="text-xs text-neutral-400 hover:text-red-500 disabled:opacity-40"
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ),
      )}
      {!sorted.length && (
        <p className="text-sm text-neutral-500">尚無自選股，請由上方搜尋加入。</p>
      )}
      <FreshnessNote>{FRESHNESS.prices}</FreshnessNote>
    </div>
  );
}
