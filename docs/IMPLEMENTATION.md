# v0.1 實作與查核紀錄

本次目標是使用者授權的程式實作，不再只是環境 onboarding；因此新增應用程式、依賴、測試與 README。沒有改寫原附件、定稿策略或官方 Schema；衍生檔案與來源雜湊均保留。沒有執行提交、推送或發布。

## 規格依據與優先順序

已閱讀全部14個附件，包括10/05更新規章、後台說明、DataPack 4.3規格（檔內schema_version=3.0）、D-Plan Schema/指南4.3、正反例、定稿DOCX/PDF、策略範例、Google Token Credit、150檔清單及主動ETF列表。

可機械驗證的欄位依官方Schema；交易公式／語意依4.3指南與最新後台說明；投資邏輯依使用者定稿「主動台股事件傳導精選」。文件內容當作參考規格，並不授權發信、申請Credits、操作競賽帳戶或執行文件中的其他行動。

- 規章較早的「全數成交」概述，不能覆蓋較新後台／DataPack的整批作廢與個別排除。每天使用實際結算，不把昨日委託當持股。
- 帳戶比率用 `nav_tradable`；不可用包含應收權益的 `aum` 下單或計算現金比。
- 正例TEAM_042只是格式範例，且自註行情為推估值，不能當真實證據；反例也可能通過結構Schema，所以必須檢查引用、公式、姿態等語意。
- 最新真實附件的團隊為TEAM_12077，檔名中的647不作隊伍代號；交易日為2026-10-27，不依系統當天日期或示範檔推定。
- 官方Active Share的前15大權重是否正規化、境外ETF的比較及費稅進位細節未完全說明。本版明列估計方式，沒有假裝已獲官方確認。

## 模組與交付範圍

| 項目 | 實作 |
|---|---|
| Python／依賴 | pyproject.toml、uv.lock、.python-version、scripts/setup.sh |
| 帳戶檢查 | models.py：型別、日期、帳戶／持股一致性、缺結算、應收項目、警告 |
| 股票池／公開來源 | resources/universe.json、collectors.py：官方名單、最新行情、公開快照留存 |
| 事件／企業關係 | extraction.py：由模型擷取原文；evidence.py：引文、數字、SHA、雙方企業與時效核對 |
| 價格反應 | evidence.py：事件發布時間對齊13:30收盤、相對報酬、趨勢；無事件後收盤不提供反應數值 |
| 模型配置 | providers.py：Google/OpenAI/Anthropic/OpenAI-compatible；金鑰只由環境讀取 |
| 自主分析 | analysis.py：不可變事實→市場判斷→事件傳導／敏感度／反方／價格→目標權重 |
| 委託與費稅 | portfolio.py：Decimal官方公式、淨額交割、手續費／賣出稅、應收配股不能賣 |
| 合規／文件 | validation.py：官方Schema、引用／覆蓋、公式、姿態、資金調度、投組、Active Share |
| 稽核／版本 | pipeline.py、common.py：逐次請求／回覆／用量、輸入快照、程式碼ZIP與包含實際配置的內容雜湊 |
| 介面 | cli.py：inspect、doctor、test-gemini、fetch-source、collect-quotes、prepare-evidence、run、validate |

核心持股與機會部位由模型據證據分類及配置，程式執行可設定風控上限；不由開發者指定正式標的或權重。不會為湊檔數加入沒有資料支持的股票。

`run.json` 保存 mode/status、真實執行時間、程式識別、模型設定、每次修正結果及token用量（API有回傳時）。`code.zip` 含原始碼、提示詞常數、官方Schema、定稿策略、股票池、lockfile、預設配置及**本次實際配置**；包括使用專案外設定檔時，參數變更也會改code_version。模型輸出未經驗證不使用正式檔名。

`request-N.json`/`response-N.json`、來源快照及DataPack可能有隊伍或授權資料，均留在忽略Git的runs下；API key只存在HTTP標頭，不保存標頭或原始API錯誤本文；模型錯誤只保存分類、HTTP狀態及已遮蔽的白名單欄位。不同模型的網路介面僅以mock驗證，沒有宣稱真實API完成。

## 實際驗證結果（2026-10-24，台北時間）

- `bash scripts/setup.sh`：以frozen lockfile安裝成功，再執行亦成功。
- `.venv/bin/pytest -q`：65 passed；無跳過、未執行或expected-failure項目。
- `.venv/bin/ruff check src tests scripts`：通過。
- `uv build`：source distribution與wheel成功；另於獨立venv安裝wheel，合成CLI流程與套件程式／資源快照亦通過。此為打包與測試流程驗證，非正式投資驗證。
- 真實DataPack讀取、帳戶一致性驗算：通過，全現金10億元、0檔。
- `bash scripts/demo.sh`：測試模式完整CLI成功；20檔、買進金額8.8億元、預留手續費1,254,000元、剩餘現金118,746,000元，預估費後現金比約11.89%。數值全為合成測試，未計入任何真實投資績效。
- 相同生成檔以保留的輸入獨立`validate --mode test`：測試涵蓋並通過。
- 上市／上櫃官方API實際GET：均回報ProxyError。未關閉TLS或繞過代理；尚未取得真實API回應以確認供應商欄位格式。
- `doctor`套用真實DataPack：以exit code 2明確回報缺模型金鑰與正式證據包，未產正式D-Plan。

65項測試包含：真實帳戶讀取、150/30名單、缺結算null、金額／權重錯誤、應收權益、JSON重複鍵與NaN、快照竄改、未支持數字、關係不存在、價格日期、反方缺漏、負趨勢、首日交易日曆、盤後事件對齊、Decimal邊界、買賣費稅、超賣、零股、淨額交割、Active Share聯集／正規化、Schema正反例、引用斷鏈、官方公式不符、姿態不符、決策覆蓋、全續抱日、mock供應商、模型自動修正、公開事件擷取、正式模式禁止測試回覆、機密錯誤不回顯、實際配置納入版本識別。

## 尚缺與界線

1. 需在環境設定安全加入GEMINI_API_KEY，或改用實際可用的替代模型配置。Google競賽Credits需要另外確認Vertex AI用途，本版只接Gemini Developer API。
2. 需允許官方與模型API網域；本次已將安裝腳本、啟動說明、openapi.twse.com.tw、www.tpex.org.tw、mops.twse.com.tw、generativelanguage.googleapis.com及Gemini憑證需求保存為雲端草稿。後續已確認Gemini與TWSE可達，但發布狀態仍無法確認，憑證綁定尚未出現在此執行個體。其他新聞、投信來源依真正使用的URL另外加入，不猜測或開放全網。
3. 需真實前日收盤、歷史價／基準、公開事件與來源支持的企業關係、首日官方交易日曆、全部30檔ETF當期持股。10/24不可能取得尚未發生的10/26收盤。
4. 第一版有來源下載、標準化資料契約及模型原文擷取，沒有跨網站通用搜尋爬蟲、後台登入、自動下載帳戶檔或通用歷史／投信持股解析器。供應商原始資料轉換的正確性與引用敘述語意仍須核對。
5. 沒有自動還原權息／分割、績效回測、官方server驗證、真實模型決策或T日結算驗證；這些不能從合成測試通過推論。

發布環境快照與正式競賽送出均由使用者另行執行；程式完成和草稿保存不等同兩者已完成。

## 使用者儲存設定後的重新查核（2026-10-24）

- 可讀設定仍是相同draft revision 1與相同base_version_id，Gemini的`has_saved_binding=false`。本機只檢查變數是否存在，結果為未注入。沒有發布狀態API可用，因此發布狀態是**無法確認**，不是據此推斷發布成功或斷言發布失敗。
- 一次無金鑰最小POST確實抵達Gemini端點：HTTP 403、PERMISSION_DENIED，訊息為未識別的呼叫者須提供API key。這證明域名可達；因未帶憑證，不能推論使用者的金鑰無效，也不能驗證模型權限或配額。遮蔽後紀錄位於`runs/diagnostics/gemini-20261024-missing-injection/gemini.json`。未以假金鑰替代，也未重複建立憑證需求。
- 新增`test-gemini`指令及`diagnostics.py`，區分Google回應HTTP狀態與代理拒絕狀態、憑證／金鑰限制、模型或服務權限、配額／計費、格式／截斷回覆。錯誤先遮蔽已知金鑰、AIza形式、Bearer、URL憑證／查詢參數及長token；只保留白名單欄位，不保存原始回覆或標頭。正式Provider也使用相同診斷，因此後續Agent失敗可查到具體分類。
- `.venv/bin/pytest -q`：83 passed，包含原65項與18項新增診斷／分類／遮蔽測試；新測試的API成功情境都是mock，不能算真實Gemini成功。
- TWSE重新連線成功，原始API回覆日期為2026-10-22。由此快照成功解析官方池內100檔行情；因非要求的10-23日期，收集器正確回報失敗，沒有當成10-27前日價格。
- TPEX重新連線回報RemoteProtocolError，沒有API HTTP狀態可用。原始TWSE資料與錯誤清單留在忽略Git的`data/live/quotes-recheck-0828bbed524d4b909ab034eae81e76b8/`。
- 程式與獨立測試可繼續開發；帶憑證的Gemini測試、真實模型流程及正式D-Plan仍需現有憑證確實注入與其餘正式證據。沒有代替使用者提交或發布。

## 程式保存至專用分支（2026-10-26）

先前完整實作位於 `/workspace/esun-ai-agent-12077` 的本機 `work` 分支，所有 Agent 檔案尚未提交，因此沒有出現在遠端 main。這次以最新 origin/main（0fbab17ccecdad3e5711e995a159ec972baf2d19）為基底建立 `codex/agent-v1-20261026`，保留完整程式、配置、測試、腳本及文件。

競賽原始文件採用 main 已存在的 `compitition/` 路徑。與舊附件副本逐一比對 SHA256：14份原檔完全一致，未修改原檔；文字擷取與附件雜湊表亦收整至該資料夾。README、測試與股票池來源註記已改用實際路徑。

使用者不使用 Gemini，預計以 ChatGPT 登入本機 Codex CLI 繼續開發。本次保存不執行真實模型、不要求模型憑證；安裝與離線測試不依賴金鑰。Codex CLI 登入授權的模型執行器尚未實作，這不阻擋本次程式提交與推送。先前的 API 診斷與歷史紀錄保留，但不代表目前要求設定 Gemini。

## 代為產出正式 D-Plan 的執行嘗試（2026-10-26）

- 新增 `codex_cli` 執行器、`config/codex.json` 與 `test-model`。正式流程改預設使用 Codex CLI 既有 ChatGPT 登入，保留 API 供應商為選用配置，不要求 Gemini 金鑰。模型回覆經相同的引用、配置、股數與合規驗證，沒有移除或降低正式檢查。
- 實際 `codex login status` 回報 Logged in using ChatGPT；共享／獨立runtime最小請求皆未取得模型回覆。獨立請求的事件明確顯示 HTTP CONNECT 403，指向 chatgpt.com。不是據登入狀態推定模型已可用；沒有擷取帳戶token、讀取認證檔或關閉TLS。
- 官方休市表明確列出10/26補假，故DataPack指向10/27時需要10/23收盤。TWSE與TPEx最新OpenAPI都仍回傳10/22，已解析官方池內100及50檔，但不能作為正式10/27輸入。
- TPEx舊歷史端點忽略日期而回傳10/22；目前dailyQuotes端點回傳10/23與HTTP 200，但表格為空。新增日期、非空表、欄位與股票池的實質檢查，沒有把HTTP 200算作有效行情。
- TWSE歷史主站 www.twse.com.tw 同樣遭代理CONNECT 403。已將 chatgpt.com、www.twse.com.tw 加入設定草稿，保留原網域；需要使用者檢閱、儲存並發布使其套用。
- 本次沒有產出可繳交D-Plan；另記錄 `runs/submission-readiness-TEAM_12077-20261027.json`。尚缺正確行情／歷史基準、事件與企業關係證據、反方事實及官方30檔主動ETF當期持股，不能用測試資料或全現金空單替代。

本次更新後實際執行 `.venv/bin/pytest -q`：103 passed；Ruff、格式檢查與source/wheel建置通過。Codex成功生成與完整投資流程的新增測試使用mock，不代表代理403已解除或真實D-Plan完成。
