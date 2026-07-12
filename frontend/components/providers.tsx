"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { apiRequest, type ApiError } from "@/lib/api";

interface AuthState {
  authenticated: boolean;
  registration_open: boolean;
  username: string | null;
}

function AuthGate({ children, queryClient }: { children: React.ReactNode; queryClient: QueryClient }) {
  const [state, setState] = useState<AuthState | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    apiRequest<AuthState>("/auth/session").then(setState).catch(() => {
      setError("無法確認登入狀態，請稍後再試");
      setState({ authenticated: false, registration_open: false, username: null });
    });
    const expire = () => setState((current) => ({ authenticated: false, registration_open: current?.registration_open ?? false, username: null }));
    window.addEventListener("stockwise:auth-required", expire);
    return () => window.removeEventListener("stockwise:auth-required", expire);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (state?.registration_open && password !== confirmPassword) {
      setError("兩次輸入的密碼不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const next = await apiRequest<AuthState>(state?.registration_open ? "/auth/register" : "/auth/login", {
        method: "POST", body: { username, password },
      });
      setPassword("");
      setConfirmPassword("");
      setState(next);
      queryClient.clear();
    } catch (reason) {
      const apiError = reason as ApiError;
      setError(apiError.status === 429 && apiError.retryAfterSeconds
        ? `${apiError.message}，請 ${apiError.retryAfterSeconds} 秒後再試`
        : apiError.message || "操作失敗");
    } finally {
      setSubmitting(false);
    }
  };

  const logout = async () => {
    try {
      await apiRequest("/auth/logout", { method: "POST" });
      queryClient.clear();
      setState({ authenticated: false, registration_open: false, username: null });
    } catch (reason) {
      setError((reason as Error).message || "登出失敗");
    }
  };

  if (state === null) return <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">正在確認登入狀態…</div>;

  if (!state.authenticated) {
    const registering = state.registration_open;
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-50 px-4 dark:bg-neutral-950">
        <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
          <h1 className="text-xl font-semibold">📈 Stock AI Advisor</h1>
          <p className="mt-2 text-sm text-neutral-500">{registering ? "建立第一個管理員帳號；完成後將關閉註冊。" : "請登入管理員帳號。"}</p>
          <label htmlFor="auth-username" className="mt-5 block text-sm font-medium">帳號</label>
          <input id="auth-username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required autoFocus className="mt-2 w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700" />
          <label htmlFor="auth-password" className="mt-4 block text-sm font-medium">密碼</label>
          <input id="auth-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={registering ? "new-password" : "current-password"} required className="mt-2 w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700" />
          {registering && <><label htmlFor="auth-confirm" className="mt-4 block text-sm font-medium">確認密碼</label><input id="auth-confirm" type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" required className="mt-2 w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700" /></>}
          {error && <p role="alert" className="mt-3 text-sm text-red-500">{error}</p>}
          <button type="submit" disabled={submitting} className="mt-5 w-full rounded-lg bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900">{submitting ? "處理中…" : registering ? "建立帳號" : "登入"}</button>
        </form>
      </main>
    );
  }

  return <>{children}<button type="button" onClick={() => void logout()} className="fixed bottom-4 right-4 z-50 rounded-lg border border-neutral-300 bg-white/90 px-3 py-1.5 text-xs shadow-sm backdrop-blur dark:border-neutral-700 dark:bg-neutral-900/90">{state.username} · 登出</button>{error && <p role="alert" className="fixed bottom-14 right-4 z-50 text-xs text-red-500">{error}</p>}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false } } }));
  return <QueryClientProvider client={queryClient}><AuthGate queryClient={queryClient}>{children}</AuthGate></QueryClientProvider>;
}
