# 主動台股事件傳導精選 — 玉山 AI CUP 2026 投資 Agent v0.1

從公開事件出發，核對企業關係、營運敏感度、事件後相對價格、趨勢及反方證據，由模型自主決定目標配置；程式依官方公式換算委託並阻擋不合規結果。這是 CLI，不會自動上傳、送出競賽文件或操作券商。

已在目前雲端實作並執行測試。**尚未完成真實模型與正式資料的端到端驗證**：已接上使用 ChatGPT 登入的 Codex CLI，不需要 Gemini 金鑰；目前 Codex 模型請求遭代理 CONNECT 403。TWSE／TPEx最新API可連線但只回傳10/22資料，TPEx的10/23歷史查詢價格表為空；仍缺當期公開事件／企業關係、歷史價格與指數、30 檔主動 ETF 持股。2026-10-24 時尚未產生 10-26 收盤行情，不能偽造為 10-27 決策輸入。

**程式保存、安裝與離線測試均不需要 Gemini API Key。** 可以在本機使用已透過 ChatGPT 登入的 Codex CLI 開啟本專案繼續開發；這種登入不等同 Gemini/OpenAI API 憑證。已提供 `codex_cli` 模型執行介面，預設使用 `config/codex.json`；原有 API 供應商設定保留為可選配置。執行器使用 CLI 管理的既有登入，不讀取或複製認證檔。

競賽原始文件以儲存庫實際的 [`compitition/`](compitition/) 為準；保留此資料夾原本的拼字。

## 安裝與執行

需要 Python 3.12+ 與 uv；正式 Codex 模式另需安裝 Codex CLI 並完成 `codex login`。依賴完整版本、雜湊在 `uv.lock`。使用現有隔離 checkout，不需另建 worktree。

```bash
cd /workspace/esun-ai-agent-12077
bash scripts/setup.sh
.venv/bin/esun-agent inspect compitition/DataPack_647_2026-10-27.json
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
```

真實附件 DataPack：**TEAM_12077 / 2026-10-27 / 可交易淨值 1,000,000,000 元 / 全現金、0 檔**。程式讀取檔案取得身分與日期，未在交易邏輯硬編碼。每天應重新下載最新結算檔；範例 TEAM_042/043 的金額、交易及隊伍皆不能拿來正式使用。

不需要金鑰的完整合成資料示範：

```bash
bash scripts/demo.sh
```

結果位於 `runs/test/<run_id>/TEST_ONLY_TEAM_TEST_2026-10-26.json`。`is_test=true`、`_NOTE` 及模型版本均標明測試；這是固定測試回覆，**不表示模型投資能力或策略報酬已驗證**。正式模式拒絕 `--test-response` 和合成證據。各次執行使用不同目錄，不會把舊成功檔當成新成功。

## 正式資料與模型

將最新帳戶檔及來源快照放在 `data/live/`（已忽略 Git）。API 金鑰只用環境變數，不寫入程式、JSON、命令列參數或紀錄；`.env.example` 僅列出名稱，程式不自動讀 `.env`。

| 設定檔 | API | 金鑰環境變數 |
|---|---|---|
| `config/codex.json`（預設） | Codex CLI，使用既有 ChatGPT 登入 | 不需要 API Key |
| `config/google.json` | Gemini Developer API | `GEMINI_API_KEY` |
| `config/openai.json` | OpenAI Chat Completions | `OPENAI_API_KEY` |
| `config/anthropic.json` | Anthropic Messages | `ANTHROPIC_API_KEY` |

可改 `model`、`api_key_env`、`timeout_seconds`、`max_output_tokens`；`provider=other` 配合 HTTPS `base_url` 支援 OpenAI-compatible API。模型須可輸出足夠長的 JSON。上述預設模型名稱是可調設定，尚未以真實憑證確認帳戶可用性。Google Cloud Token Credits 不等於 Gemini Developer API 金鑰；本版沒有 Vertex AI/ADC 介面，不宣稱可直接使用競賽抵用額。

最小 Gemini 連線診斷（一次請求，最多32個輸出token；缺金鑰時明確標示為無憑證網路探測）：

```bash
.venv/bin/esun-agent test-gemini --config config/google.json \
  --output runs/diagnostics/gemini-check.json
```

只有帶憑證的請求取得HTTP 200、正常完成且符合預期JSON才算通過。結果區分 `credentials`／`credential_restrictions`、`network`／`network_proxy`、`model_or_service_permissions`／`model_unavailable_or_inaccessible`、`quota_or_rate_limit`／`billing_configuration`，並回報遮蔽後訊息。非結構化HTTP錯誤會標為來源未確認，不冒認模型權限問題。代理HTTP狀態與Gemini API狀態分開；不保存原始標頭、金鑰或模型回覆全文。診斷失敗exit code為2，不會重試或切換模型。輸出路徑已存在時拒絕覆寫，請使用新檔名。

Gemini 診斷為選用功能。使用者目前不使用 Gemini，缺少該憑證不阻擋程式保存、安裝、測試或本機 Codex CLI 開發；未執行真實 API 模式時，不宣稱正式模型驗證完成。

Codex CLI 模型連線測試：

```bash
.venv/bin/esun-agent test-model --config config/codex.json
```

本版呼叫 `codex exec`，以 read-only sandbox、ephemeral 模式、指定模型和新建回覆檔執行。忽略使用者的自訂 CLI 配置，保留 CLI 管理的登入認證；若 CLI 事件顯示額外工具使用，會拒收該回覆。`config/codex.json` 預設 `gpt-5.4`，可改為帳戶實際可用的模型；這個模型設定尚未完成真實連線驗證。`timeout_seconds` 適用 CLI，`max_output_tokens` 僅用於 API 供應商。

2026-10-26 實測：CLI 的 `login status` 成功，但模型連線遭 HTTPS 代理 CONNECT 403；需在環境設定允許 `chatgpt.com`。TWSE 歷史行情另需 `www.twse.com.tw`，這兩個網域已存入環境設定草稿，仍須檢閱、儲存並發布後套用。不能把「已登入」當成模型呼叫成功。

正式流程：

```bash
# 先用官方日曆確認 T-1；2026-10-26 為休市日，10-27 的 T-1 為 10-23。
.venv/bin/esun-agent collect-history --date 2026-10-23 --market twse --output data/live/twse-20261023
.venv/bin/esun-agent collect-history --date 2026-10-23 --market tpex --output data/live/tpex-20261023

# 公開來源原始內容留存（換成實際公開 URL；不會自動認定其內容為事實）
# .venv/bin/esun-agent fetch-source --url 'https://...' --output data/live/evidence/source.txt

# manifest 提供來源/行情/日曆/ETF持股，不含人工交易指令；由模型擷取公開事實及企業關係。
.venv/bin/esun-agent prepare-evidence \
  --manifest data/live/evidence/manifest.json \
  --datapack data/live/DataPack.json \
  --output data/live/evidence/bundle.json --config config/codex.json

.venv/bin/esun-agent doctor --datapack data/live/DataPack.json \
  --evidence data/live/evidence/bundle.json --config config/codex.json

.venv/bin/esun-agent run --mode live --datapack data/live/DataPack.json \
  --evidence data/live/evidence/bundle.json --config config/codex.json
```

`manifest.json` 的資料契約與來源處理詳見 [資料輸入](docs/INPUTS.md)，機器可讀格式見 [Evidence Schema](docs/schemas/evidence.schema.json)。可用相同格式匯入外部資料服務已擷取且可核對的事實；程式不接受人為指定的正式目標配置。第一版不含全網搜尋、登入後台抓取或跨投信網站的通用爬蟲，來源 URL 與快照清單須由資料蒐集流程提供。

正式檢核全部通過才產出 `runs/live/<run_id>/D-Plan_<team_id>_<trade_date>.json`。失敗狀態為 `blocked`、exit code 2，只保留紀錄及可能的 `candidate.json`，不產正式檔名。最多由模型自主修正兩次（總共三次呼叫），不會以手調股數、補造新聞或停用限制取得通過。

獨立重驗：

```bash
.venv/bin/esun-agent validate runs/live/<run_id>/D-Plan_<team_id>_<trade_date>.json \
  --datapack runs/live/<run_id>/datapack.json \
  --evidence runs/live/<run_id>/evidence/bundle.json --config config/codex.json
```

請閱讀同目錄 `validation.json`、`run.json`。本機通過不等於官方收件或結算通過。提交時間以官方系統為準：交易日前一日 19:30 至交易日 08:55（台北）；上傳後仍須「送出繳交」，並確認當日委託。

## 實作與限制

- DataPack 3.0：空陣列與缺結算的 null 分開處理；驗算 NAV、現金比、持股權重／數目；應收現金不能下單，應收配股不能出售。
- 股票池：直接由附件 PDF 擷取上市 100／上櫃 50 檔，區分 TWSE/TPEX；附來源雜湊，非自行選出的名單。
- 證據：快照 SHA256、原文逐字引文、數字、企業雙方名稱／代號、引用圖、事件時效、未來時間及價格日期檢查。事實與來源由資料管線固定，投資模型只能產生推論／配置。
- 事件價格：事件前最近收盤至前日收盤的個股報酬減基準報酬，另計趨勢；至少五個交易日、正趨勢確認。缺事件前基準就不建倉。跨除權息／分割的原始報酬可能失真，應提供可比較區間；本版未自動還原權息或判讀企業行動。
- 策略風控參數：單一事件共同曝險最多30%、機會部位最多35%、確認受惠核心至少45%、現金下限至少3%。這些是 v1 實作預設，**不是官方規定**；參數／提示詞變更會改程式雜湊。
- 官方公式：`floor(target_weight × account.nav_tradable ÷ T-1 close ÷ 1000) × 1000 − current_shares`。Decimal 換算，不手動調整。若配股造成零股而公式導出非千股，會阻擋並回報。
- 合規：20–30檔、現金0–25%、2330最高25%／其他10%、現股／不超賣／不當沖、買賣手續費0.1425%及賣出稅0.3%、淨額交割、淨流向／現金姿態、資金調度缺口與決策覆蓋。
- 費稅逐筆向上取整只是保守預留（主辦方進位方式未揭露）；不利成交價 ±3% 壓力測試僅檢查現金。T日 VWAP、收盤價、排除標的與官方結算仍未知，不能保證當日成交後合規。
- 被動權重超限採保守阻擋，不自行推算官方五日寬限；需看 DataPack 異常及後台連續日數。
- Active Share：要求官方清單內30檔 ETF 當期持股。前15大取聯集計算，並列原NAV權重／前15大重新正規化兩種結果，採較嚴者≥15%；規章未釐清的解讀與境外ETF比較方式須以官方答覆為準。本版不假裝已通過連續兩日官方抽查。
- Schema 4.3＋語意驗證：連續唯一ID、完整 order→decision→inference→observation→source、每筆持股只出現一次、不存在引用、公式／方向不一致均失敗。Schema 通過本身不代表合規。
- 引文與雜湊能核對「所存內容」，不代表可自動判定所有敘述語意或供應鏈真實性；模型仍需據證據審慎判讀。未執行績效回測，不宣稱獲利。

附件原檔及文字版在 `compitition/`，其 `manifest.json` 記錄原附件雜湊。附帶的真實帳戶快照含隊伍帳戶資訊；請維持儲存庫與執行紀錄的存取權限。執行輸出與未來正式資料均忽略 Git；原始文檔與定稿策略保留供查核。

完整架構、規章對照與目前驗證證據見 [實作紀錄](docs/IMPLEMENTATION.md)。
