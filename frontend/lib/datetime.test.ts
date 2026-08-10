import { describe, expect, it } from "vitest";

import { formatMarketTime, formatRunTime } from "./datetime";

describe("formatMarketTime", () => {
  it("台股以台北時間顯示：01:00Z 就是開盤的 09:00", () => {
    expect(formatMarketTime("2026-07-15T01:00:00Z", "tw")).toBe("2026-07-15 09:00");
  });

  it("美股以紐約時間顯示：13:30Z 就是開盤的 09:30（夏令）", () => {
    expect(formatMarketTime("2026-07-15T13:30:00Z", "us")).toBe("2026-07-15 09:30");
  });

  it("美股冬令同樣是 09:30——換季不可錯一小時", () => {
    expect(formatMarketTime("2026-01-14T14:30:00Z", "us")).toBe("2026-01-14 09:30");
  });

  it("顯示的是市場當地時間，不是瀏覽器所在時區", () => {
    // 同一時刻在兩個市場的日曆日不同：美東仍是前一天的盤後
    const iso = "2026-07-15T01:00:00Z";
    expect(formatMarketTime(iso, "tw")).toBe("2026-07-15 09:00");
    expect(formatMarketTime(iso, "us")).toBe("2026-07-14 21:00");
  });

  it("無值或無法解析回傳 null", () => {
    expect(formatMarketTime(null, "tw")).toBeNull();
    expect(formatMarketTime(undefined, "tw")).toBeNull();
    expect(formatMarketTime("not-a-date", "tw")).toBeNull();
  });

  it("缺少時區標記的字串不可當本地時間解讀", () => {
    // 後端一律送 Z（見 time_service.as_utc_iso）；萬一少了，
    // 仍要當 UTC 處理，否則對台灣就整整差 8 小時
    expect(formatMarketTime("2026-07-15T01:00:00", "tw")).toBe("2026-07-15 09:00");
  });
});

describe("formatRunTime 不受影響", () => {
  it("仍以瀏覽器當地時區顯示排程執行時間", () => {
    expect(formatRunTime("2026-07-15T01:00:00Z")).toMatch(/^\d{2}\/\d{2} \d{2}:\d{2}$/);
  });
});
