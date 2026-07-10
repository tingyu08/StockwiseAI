/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { expect, it } from "vitest";

import { TechnicalIndicatorsChart } from "./technical-indicators";

it("renders RSI, KD, and MACD panels from price indicators", () => {
  render(createElement(TechnicalIndicatorsChart, {
    data: [{
      date: "2026-07-10", open: 100, high: 101, low: 99, close: 100, volume: 1,
      ma5: 100, ma20: 99, ma60: 98, rsi14: 55, kd_k: 60, kd_d: 50,
      macd: 1.2, macd_signal: 0.8, bb_upper: 105, bb_lower: 95,
    }],
  }));

  expect(screen.getByText("RSI / KD")).toBeInTheDocument();
  expect(screen.getByText("MACD")).toBeInTheDocument();
});
