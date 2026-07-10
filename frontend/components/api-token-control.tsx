"use client";

import { useEffect, useState } from "react";

import { getApiToken, setApiToken } from "@/lib/api";

export function ApiTokenControl() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [configured, setConfigured] = useState(false);

  useEffect(() => {
    setConfigured(!!getApiToken());
    const show = () => setOpen(true);
    window.addEventListener("stockwise:unauthorized", show);
    return () => window.removeEventListener("stockwise:unauthorized", show);
  }, []);

  const save = () => {
    const value = token.trim();
    setApiToken(value || null);
    setConfigured(!!value);
    setToken("");
    setOpen(false);
    window.dispatchEvent(new Event("stockwise:token-changed"));
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="rounded-md border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
        aria-label="設定 API Token"
      >
        {configured ? "🔒 API" : "🔓 API Token"}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-72 rounded-lg border border-neutral-200 bg-white p-3 shadow-lg dark:border-neutral-700 dark:bg-neutral-900">
          <label htmlFor="api-token" className="mb-1 block text-xs text-neutral-500">
            API Token
          </label>
          <input
            id="api-token"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            className="w-full rounded border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
            autoComplete="off"
          />
          <div className="mt-2 flex justify-end gap-2">
            {configured && (
              <button type="button" onClick={() => { setApiToken(null); setConfigured(false); }} className="text-xs text-red-500">
                清除
              </button>
            )}
            <button type="button" onClick={save} className="rounded bg-neutral-900 px-3 py-1 text-xs text-white dark:bg-white dark:text-neutral-900">
              儲存
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
