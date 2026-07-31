import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 測試時區固定為 Asia/Taipei（產品的實際使用時區）。
// 不釘死的話與時間顯示有關的斷言會隨執行環境改變結果：本機是 +08 而 CI 跑在
// UTC，「有沒有帶 Z」在 UTC 下指向同一時刻，用來防止時區誤判的測試會失去意義
// 並在 CI 失敗（實際發生過）。設在 config 頂層，worker 啟動前即生效。
process.env.TZ = "Asia/Taipei";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      thresholds: {
        statements: 75,
        branches: 70,
        functions: 70,
        lines: 80,
      },
    },
  },
});
