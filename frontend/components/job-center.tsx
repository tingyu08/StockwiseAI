"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  apiRequest,
  listActiveJobs,
  removeActiveJob,
  type ActiveJob,
  type JobRun,
  type StartedJob,
} from "@/lib/api";

type JobState = Pick<JobRun<unknown>, "status" | "error">;

const statusLabel: Record<JobState["status"], string> = {
  queued: "等待中",
  running: "執行中",
  succeeded: "已完成",
  failed: "失敗",
};

export function JobCenter() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<ActiveJob[]>([]);
  const [states, setStates] = useState<Record<number, JobState>>({});

  useEffect(() => {
    const restore = () => setJobs(listActiveJobs());
    restore();
    window.addEventListener("stockwise:jobs-changed", restore);
    return () => window.removeEventListener("stockwise:jobs-changed", restore);
  }, []);

  useEffect(() => {
    if (jobs.length === 0) return;
    const controller = new AbortController();
    const refresh = async () => {
      await Promise.all(
        jobs.map(async (job) => {
          try {
            const run = await apiRequest<JobRun<unknown>>(`/jobs/runs/${job.runId}`, {
              signal: controller.signal,
            });
            if (run.status === "succeeded") {
              const match = /^sync-(tw|us)-(.+)$/.exec(job.name);
              if (match) {
                const [, market, symbol] = match;
                void queryClient.invalidateQueries({
                  queryKey: ["stock-dashboard", market, symbol],
                });
                void queryClient.invalidateQueries({
                  queryKey: ["prices", market, symbol],
                });
              }
              removeActiveJob(job.runId);
              return;
            }
            setStates((current) => ({
              ...current,
              [job.runId]: { status: run.status, error: run.error },
            }));
          } catch (error) {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              setStates((current) => ({
                ...current,
                [job.runId]: {
                  status: "failed",
                  error: error instanceof Error ? error.message : "無法取得工作狀態",
                },
              }));
            }
          }
        }),
      );
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [jobs, queryClient]);

  const retry = async (job: ActiveJob) => {
    await apiRequest<StartedJob>(`/jobs/runs/${job.runId}:retry`, { method: "POST" });
    setStates((current) => ({
      ...current,
      [job.runId]: { status: "queued", error: null },
    }));
  };

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={`工作 ${jobs.length}`}
        onClick={() => setOpen((value) => !value)}
        className="rounded-md border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
      >
        工作{jobs.length > 0 ? ` ${jobs.length}` : ""}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-80 rounded-lg border border-neutral-200 bg-white p-3 shadow-lg dark:border-neutral-700 dark:bg-neutral-900">
          <p className="mb-2 text-sm font-medium">背景工作</p>
          {jobs.length === 0 ? (
            <p className="text-xs text-neutral-500">目前沒有進行中的工作</p>
          ) : (
            <ul className="space-y-2">
              {jobs.map((job) => {
                const state = states[job.runId] ?? { status: "queued", error: null };
                return (
                  <li key={job.runId} className="rounded border border-neutral-200 p-2 text-xs dark:border-neutral-700">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{job.name}</span>
                      <span>{statusLabel[state.status]}</span>
                    </div>
                    {state.error && <p className="mt-1 break-words text-red-500">{state.error}</p>}
                    {state.status === "failed" && (
                      <div className="mt-2 flex justify-end gap-2">
                        <button type="button" onClick={() => removeActiveJob(job.runId)}>
                          移除
                        </button>
                        <button
                          type="button"
                          onClick={() => void retry(job)}
                          className="rounded bg-neutral-900 px-2 py-1 text-white dark:bg-white dark:text-neutral-900"
                        >
                          重試
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
