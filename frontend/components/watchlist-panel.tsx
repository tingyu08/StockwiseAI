"use client";

import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { FreshnessNote, FRESHNESS } from "@/components/freshness-note";
import {
  useCreateGroup,
  useDeleteGroup,
  useGroups,
  useRenameGroup,
  useReorderWatchlist,
} from "@/hooks/use-groups";
import { useRemoveWatch, useWatchlist } from "@/hooks/use-stocks";
import type { WatchItem } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

const UNGROUPED = "none";

type Lists = Record<string, string[]>; // containerKey -> symbols

const containerKey = (groupId: number | null) => (groupId === null ? UNGROUPED : `g${groupId}`);
const keyToGroupId = (key: string): number | null => (key === UNGROUPED ? null : Number(key.slice(1)));

export function WatchlistPanel() {
  const market = useMarketStore((s) => s.market);
  const { data: items, isLoading, isError, error } = useWatchlist();
  const { data: groups } = useGroups();
  const removeWatch = useRemoveWatch();
  const reorder = useReorderWatchlist();

  const [lists, setLists] = useState<Lists>({});
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  const byGroup = useMemo(() => {
    const sorted = [...(items ?? [])].sort((a, b) => a.sort_order - b.sort_order);
    const next: Lists = { [UNGROUPED]: [] };
    for (const g of groups ?? []) next[containerKey(g.id)] = [];
    for (const w of sorted) {
      const key = containerKey(w.group_id);
      (next[key] ??= []).push(w.symbol);
    }
    return next;
  }, [items, groups]);

  // 只在伺服器資料變動且非拖曳中時同步（避免拖曳結束瞬間被舊資料蓋回）
  const draggingRef = useRef(false);
  useEffect(() => {
    if (!draggingRef.current) setLists(byGroup);
  }, [byGroup]);

  const itemMap = useMemo(
    () => new Map((items ?? []).map((w) => [w.symbol, w])),
    [items],
  );

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const findContainer = (id: string): string | undefined => {
    if (id in lists) return id;
    return Object.keys(lists).find((key) => lists[key].includes(id));
  };

  const onDragStart = ({ active }: DragStartEvent) => {
    draggingRef.current = true;
    setActiveSymbol(String(active.id));
  };

  const onDragOver = ({ active, over }: DragOverEvent) => {
    if (!over) return;
    const from = findContainer(String(active.id));
    const to = findContainer(String(over.id));
    if (!from || !to || from === to) return;
    // 跨群組：先在本地把項目搬到目標容器（拖曳中的即時視覺回饋）
    setLists((prev) => {
      const fromItems = prev[from].filter((s) => s !== active.id);
      const overIdx = prev[to].indexOf(String(over.id));
      const insertAt = overIdx >= 0 ? overIdx : prev[to].length;
      const toItems = [...prev[to]];
      toItems.splice(insertAt, 0, String(active.id));
      return { ...prev, [from]: fromItems, [to]: toItems };
    });
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    setActiveSymbol(null);
    if (!over) {
      draggingRef.current = false;
      return setLists(byGroup);
    }
    const container = findContainer(String(active.id));
    if (!container) {
      draggingRef.current = false;
      return setLists(byGroup);
    }

    let next = lists;
    const overContainer = findContainer(String(over.id));
    if (overContainer === container && String(over.id) !== String(active.id)) {
      const oldIdx = lists[container].indexOf(String(active.id));
      const newIdx = lists[container].indexOf(String(over.id));
      if (oldIdx >= 0 && newIdx >= 0) {
        next = { ...lists, [container]: arrayMove(lists[container], oldIdx, newIdx) };
        setLists(next);
      }
    }
    // 整批送出最終狀態；成功 refetch 後 byGroup 更新，屆時才重新同步
    const payload = Object.entries(next).flatMap(([key, symbols]) =>
      symbols.map((symbol, i) => ({
        symbol,
        group_id: keyToGroupId(key),
        sort_order: i,
      })),
    );
    reorder.mutate(payload, {
      onSettled: () => {
        draggingRef.current = false;
      },
    });
  };

  if (isLoading) return <p className="text-sm text-neutral-500">載入自選清單中…</p>;
  if (isError) return <p className="text-sm text-red-500">{(error as Error).message}</p>;

  const sections = [
    ...(groups ?? []).map((g) => ({ key: containerKey(g.id), id: g.id as number | null, name: g.name })),
    { key: UNGROUPED, id: null as number | null, name: "未分組" },
  ];
  const total = (items ?? []).length;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          onClick={() => setShowCreateModal(true)}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          ＋建立群組
        </button>
      </div>

      {showCreateModal && <CreateGroupModal onClose={() => setShowCreateModal(false)} />}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => {
          draggingRef.current = false;
          setActiveSymbol(null);
          setLists(byGroup);
        }}
      >
        {sections.map((section) => {
          const symbols = lists[section.key] ?? [];
          if (section.key === UNGROUPED && symbols.length === 0 && sections.length > 1) return null;
          return (
            <GroupSection
              key={section.key}
              containerId={section.key}
              groupId={section.id}
              name={section.name}
              count={symbols.length}
            >
              <SortableContext items={symbols} strategy={verticalListSortingStrategy}>
                {symbols.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-neutral-200 px-4 py-3 text-xs text-neutral-400 dark:border-neutral-800">
                    （把股票拖進來）
                  </p>
                ) : (
                  <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
                    {symbols.map((symbol) => {
                      const w = itemMap.get(symbol);
                      return w ? (
                        <SortableRow
                          key={symbol}
                          item={w}
                          market={market}
                          onRemove={() => removeWatch.mutate(symbol)}
                        />
                      ) : null;
                    })}
                  </ul>
                )}
              </SortableContext>
            </GroupSection>
          );
        })}
        <DragOverlay>
          {activeSymbol && itemMap.get(activeSymbol) && (
            <div className="rounded-lg border border-blue-400 bg-white px-4 py-2 text-sm shadow-lg dark:bg-neutral-900">
              <span className="font-mono font-semibold">{activeSymbol}</span>
              <span className="ml-2 text-neutral-500">{itemMap.get(activeSymbol)!.name}</span>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {total === 0 && <p className="text-sm text-neutral-500">尚無自選股，請由上方搜尋加入。</p>}
      <FreshnessNote>{FRESHNESS.prices}</FreshnessNote>
    </div>
  );
}

function GroupSection({
  containerId, groupId, name, count, children,
}: {
  containerId: string;
  groupId: number | null;
  name: string;
  count: number;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: containerId });
  const deleteGroup = useDeleteGroup();

  return (
    <div ref={setNodeRef} className={isOver ? "rounded-lg ring-2 ring-blue-400/50" : ""}>
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-1 text-sm font-medium text-neutral-500">
          {groupId === null ? (
            <span>{name}</span>
          ) : (
            <EditableGroupName groupId={groupId} name={name} />
          )}
          <span className="text-xs">（{count}）</span>
        </div>
        {groupId !== null && (
          <button
            onClick={() => deleteGroup.mutate(groupId)}
            className="text-xs text-neutral-400 hover:text-red-500"
            title="刪除群組（股票會移回未分組）"
          >
            刪除群組
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function EditableGroupName({ groupId, name }: { groupId: number; name: string }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  const rename = useRenameGroup();

  const commit = () => {
    setEditing(false);
    const trimmed = value.trim();
    if (trimmed && trimmed !== name) rename.mutate({ id: groupId, name: trimmed });
    else setValue(name);
  };

  if (!editing) {
    return (
      <button
        onClick={() => {
          setValue(name);
          setEditing(true);
        }}
        className="cursor-text hover:underline"
        title="點擊編輯群組名稱"
      >
        {name} ✏️
      </button>
    );
  }
  return (
    <input
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") {
          setValue(name);
          setEditing(false);
        }
      }}
      className="w-32 rounded border border-neutral-300 bg-transparent px-1 py-0.5 text-sm dark:border-neutral-600"
    />
  );
}

function SortableRow({
  item, market, onRemove,
}: {
  item: WatchItem;
  market: string;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: item.symbol });

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-2 bg-white px-4 py-2 dark:bg-neutral-950 ${
        isDragging ? "opacity-40" : ""
      }`}
    >
      <span
        {...attributes}
        {...listeners}
        className="cursor-grab touch-none select-none text-neutral-300 hover:text-neutral-500 active:cursor-grabbing"
        title="拖曳排序或移動群組"
        aria-label="拖曳把手"
      >
        ⠿
      </span>
      <Link href={`/stock/${item.symbol}?market=${market}`} className="flex-1 text-sm hover:underline">
        <span className="font-mono font-semibold">{item.symbol}</span>
        <span className="ml-2 text-neutral-500">{item.name}</span>
      </Link>
      <button
        onClick={onRemove}
        className="text-xs text-neutral-400 hover:text-red-500"
      >
        移除
      </button>
    </li>
  );
}

function CreateGroupModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const createGroup = useCreateGroup();

  const submit = () => {
    if (!name.trim()) return;
    createGroup.mutate(name.trim(), { onSuccess: onClose });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-80 rounded-xl border border-neutral-200 bg-white p-5 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-3 text-sm font-semibold">建立群組</h3>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="群組名稱，如：半導體、高股息"
          className="mb-3 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
        />
        {createGroup.isError && (
          <p className="mb-2 text-xs text-red-500">{(createGroup.error as Error).message}</p>
        )}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={submit}
            disabled={!name.trim() || createGroup.isPending}
            className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
          >
            建立
          </button>
        </div>
      </div>
    </div>
  );
}
