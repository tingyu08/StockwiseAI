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
      // 統計範圍＝下面的 include。未指定時 v8 只統計「被測試載入過」的檔案，
      // 於是為某個尚無測試的元件補上第一個測試，會把它與其相依一次拉進分母，
      // 造成「加了測試反而讓覆蓋率下降、CI 轉紅」（2026-08-14 實際發生）。
      // 明確列出原始碼範圍後分母才穩定，補測試永遠是加分。
      //
      // 註：舊版的 coverage.all 選項在 Vitest 4 已移除（填了會是型別錯誤），
      // 列出 include 就是現在的等效寫法。
      include: [
        "app/**/*.{ts,tsx}",
        "components/**/*.{ts,tsx}",
        "hooks/**/*.{ts,tsx}",
        "lib/**/*.{ts,tsx}",
        "stores/**/*.{ts,tsx}",
      ],
      exclude: [
        "**/*.test.{ts,tsx}",
        "**/*.d.ts",
        // 測試專用工具：本身沒有產品行為可測，計入只會稀釋訊號
        "hooks/query-test-utils.ts",
        // Next.js 的框架檔（layout/loading/error 等）幾乎只有結構宣告
        "app/**/layout.tsx",
      ],
      // 門檻是「防止退步」的地板，不是品質目標。
      //
      // 開啟 all 之前這裡是 75/70/70/80，但那是只統計約一半檔案得出的數字：
      // app/*/page.tsx、watchlist-panel、use-simulation 等大型未測檔案從未
      // 進入分母。納入全部原始碼後實測為
      // statements 40.10／branches 32.66／functions 36.36／lines 40.16，
      // 下列數字即由此各留約 2 個百分點的餘裕而來。
      //
      // 數字變低不代表品質變差——是同一份程式碼第一次被誠實丈量。
      // 補測試時請一併把地板往上帶。
      thresholds: {
        statements: 38,
        branches: 30,
        functions: 34,
        lines: 38,
      },
    },
  },
});
