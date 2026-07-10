"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { PredictionBandPoint } from "@/hooks/use-premium";
import type { PricePoint } from "@/lib/types";
import type { Market } from "@/stores/market";

// 台股慣例紅漲綠跌；美股綠漲紅跌
const COLORS: Record<Market, { up: string; down: string }> = {
  tw: { up: "#ef4444", down: "#22c55e" },
  us: { up: "#22c55e", down: "#ef4444" },
};

const MA_COLORS = { ma5: "#f59e0b", ma20: "#3b82f6", ma60: "#a855f7" };

interface Props {
  data: PricePoint[];
  market: Market;
  prediction?: PredictionBandPoint[];
}

export function CandlestickChart({ data, market, prediction }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || data.length === 0) return;

    const { up, down } = COLORS[market];
    const chart = createChart(container, {
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#737373",
      },
      grid: {
        vertLines: { color: "rgba(115,115,115,0.1)" },
        horzLines: { color: "rgba(115,115,115,0.1)" },
      },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
      autoSize: true,
    });
    chartRef.current = chart;

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down,
      wickUpColor: up, wickDownColor: down,
      borderVisible: false,
    });
    candles.setData(
      data
        .filter((d) => d.open != null)
        .map((d) => ({
          time: d.date,
          open: d.open!, high: d.high!, low: d.low!, close: d.close!,
        })),
    );

    for (const key of ["ma5", "ma20", "ma60"] as const) {
      const line = chart.addSeries(LineSeries, {
        color: MA_COLORS[key], lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false,
      });
      line.setData(
        data.filter((d) => d[key] != null).map((d) => ({ time: d.date, value: d[key]! })),
      );
    }

    for (const key of ["bb_upper", "bb_lower"] as const) {
      const line = chart.addSeries(LineSeries, {
        color: "rgba(14,165,233,0.45)", lineWidth: 1, lineStyle: 3,
        priceLineVisible: false, lastValueVisible: false,
      });
      line.setData(
        data.filter((d) => d[key] != null).map((d) => ({ time: d.date, value: d[key]! })),
      );
    }

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });
    volume.setData(
      data
        .filter((d) => d.volume != null)
        .map((d) => ({
          time: d.date,
          value: d.volume!,
          color: (d.close ?? 0) >= (d.open ?? 0) ? `${up}55` : `${down}55`,
        })),
    );

    if (prediction && prediction.length > 0) {
      const lastClose = data.at(-1);
      const anchor =
        lastClose?.close != null
          ? [{ time: lastClose.date, value: lastClose.close }]
          : [];
      const bandStyle = {
        lineWidth: 1 as const,
        priceLineVisible: false,
        lastValueVisible: false,
      };
      const mid = chart.addSeries(LineSeries, {
        ...bandStyle, color: "#60a5fa", lineStyle: 2, // dashed
      });
      mid.setData([...anchor, ...prediction.map((p) => ({ time: p.date, value: p.mid }))]);
      for (const key of ["upper", "lower"] as const) {
        const line = chart.addSeries(LineSeries, {
          ...bandStyle, color: "rgba(96,165,250,0.45)", lineStyle: 3, // dotted
        });
        line.setData([...anchor, ...prediction.map((p) => ({ time: p.date, value: p[key] }))]);
      }
    }

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data, market, prediction]);

  return (
    <div>
      <div className="mb-1 flex gap-4 text-xs text-neutral-500">
        <span style={{ color: MA_COLORS.ma5 }}>MA5</span>
        <span style={{ color: MA_COLORS.ma20 }}>MA20</span>
        <span style={{ color: MA_COLORS.ma60 }}>MA60</span>
        <span className="text-sky-500">布林通道</span>
      </div>
      <div ref={containerRef} className="h-[420px] w-full" />
    </div>
  );
}
