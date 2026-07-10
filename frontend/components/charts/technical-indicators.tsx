"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PricePoint } from "@/lib/types";

export function TechnicalIndicatorsChart({ data }: { data: PricePoint[] }) {
  return (
    <div className="mt-5 grid gap-4 lg:grid-cols-2">
      <section>
        <h3 className="mb-2 text-xs font-medium text-neutral-500">RSI / KD</h3>
        <ResponsiveContainer width="100%" height={190}>
          <LineChart data={data}>
            <CartesianGrid strokeOpacity={0.12} vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={45} />
            <YAxis domain={[0, 100]} width={32} tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ fontSize: 11 }} />
            <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="3 3" />
            <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="3 3" />
            <Line dataKey="rsi14" name="RSI14" stroke="#a855f7" dot={false} />
            <Line dataKey="kd_k" name="K" stroke="#f59e0b" dot={false} />
            <Line dataKey="kd_d" name="D" stroke="#3b82f6" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>
      <section>
        <h3 className="mb-2 text-xs font-medium text-neutral-500">MACD</h3>
        <ResponsiveContainer width="100%" height={190}>
          <LineChart data={data}>
            <CartesianGrid strokeOpacity={0.12} vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={45} />
            <YAxis width={42} tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ fontSize: 11 }} />
            <ReferenceLine y={0} stroke="#737373" />
            <Line dataKey="macd" name="MACD" stroke="#ef4444" dot={false} />
            <Line dataKey="macd_signal" name="Signal" stroke="#3b82f6" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>
    </div>
  );
}
