# 資料輸入與來源契約

`docs/schemas/*.schema.json` 由實際 Pydantic 模型產生；變更模型後執行 `.venv/bin/python scripts/export_schemas.py`。`scripts/generate_demo.py` 是完整可執行的**合成格式範例**，其數值和引用不是正式資料。

## 檔案位置

建議 `data/live/evidence/manifest.json`、原始來源快照、整理後的行情快照放在同一目錄樹。快照不可用絕對路徑跳出目錄，也不可透過 symlink 跳出。內容使用 UTF-8，來源記錄 SHA256 需與位元組完全相符；保留下載的原始 JSON／HTML／公告以供語意核對。

`prepare-evidence` 使用的 manifest 為 EvidenceBundle 去除 `facts` 與 `relationships`，其餘欄位如下。這個步驟由模型擷取事實與關係並核對引文；**不包含目標權重、交易方向或股數**。`bundle.json` 則是補上兩個擷取結果後的完整資料。

| 欄位 | 內容 |
|---|---|
| `is_test` | 正式資料必須 false；合成資料 true。不能把修改旗標當成資料真實性證明 |
| `previous_close_date` | 官方前一交易日；有結算時須等於 DataPack.as_of_date |
| `sources` | 最多50筆：key、authority、url、content_as_of、fetched_at、snapshot_path、sha256；選填published_at/archive_url |
| `prices` | 每標的一筆：ticker、date、close、source_key；date須等於previous_close_date |
| `history` | ticker、date、close、source_key、benchmark_close、benchmark_source_key；至少5個交易日，含事件前與最新收盤 |
| `benchmarks` | 官方30檔 ETF，每筆code、as_of_date、source_key、weights（代號→占整體NAV的比率），至少15檔披露持股 |
| `calendar` | source_key、trade_dates（升序完整交易日列表）；首次DataPack.as_of_date=null的正式模式必填 |

所有時間都是帶 `+08:00` 的台北時間。`content_as_of` 是資料描述時點，不是抓取時間；不得用新抓取時間包裝舊資料。事件時效45日、關係時效400日、ETF持股時效7日均可在策略設定調整。首日交易日曆必須包含前後相鄰的 `previous_close_date` 與 `DataPack.trade_date`，不能以週一至五猜測假日。

## 來源、行情與標準化快照

`authority` 沿用官方定義；上市價量 twse，上櫃 tpex，研究與基金持股 fininst，報導 media。正式模式會確認官方 authority 的網域。來源是無金鑰的公開 HTTPS URL；金鑰、簽名URL不可放進證據。

`collect-quotes` 留存原API回覆 `twse-raw.json`／`tpex-raw.json`、整理後 prices JSON 及 collection.json（URL、雜湊、抓取時間、來源日期）。最新API不提供任意歷史查詢；日期不符必須使用交易所歷史資料或既有已留存的快照，不可修改日期。歷史資料和市場基準指數應由已驗證的資料匯入流程取得，已提供 `collect-history --date YYYY-MM-DD --market twse|tpex --output <新目錄>`，支援 TWSE MI_INDEX 與 TPEx 目前的 dailyQuotes 端點；必須核對回應日期及非空價格表。

行情整理後快照為：

```json
{"prices": [
  {"ticker": "2330", "date": "YYYY-MM-DD", "close": 123.45},
  {"ticker": "benchmark", "date": "YYYY-MM-DD", "close": 23456.7}
]}
```

以上只說明欄位，**不是可使用的真實行情**。prices/history 的每個數值必須在其source快照中找到相同 ticker、date、close。benchmark_close 在 benchmark_source_key 的 `ticker=benchmark` 列核對；建議整段歷史使用同一個官方基準，避免拼接不同指數。

ETF 標準化持股快照格式：`{"etf_holdings": {"00980A": {"as_of_date": "YYYY-MM-DD", "weights": {"2330": 0.2}}}}`。每檔實際需至少15檔及同日披露日期。保留投信原始披露頁／檔案，不能拿ETF名單、範例權重或模型猜測當持股。日曆快照格式 `{"trade_dates": ["YYYY-MM-DD", "YYYY-MM-DD"]}`，來源指向官方交易日表，保留原表及標準化結果。

SHA與標準化快照可驗證輸入在執行過程沒有改變，不能替代原始資料擷取／轉換正確性的查核。本版官方API的線上正規化仍待網路放行後用真實回應確認。

## 公開事件與企業關係

facts：key、kind、entity（事件必填）、tickers、statement、values、citations。kind為market/event/relation/counter。每個 citation 有source_key、quote；quote須為快照逐字連續子字串。statement是事實摘要，不能摻入看多／買入推論；values只放原文數字，沒有數字填 `{}`。程式產生官方observation時，空values會明確記錄「已核對引文筆數」，而非編造財務指標。

relationships：key、from_entity（事件企業）、to_ticker（池內公司）、relation、fact_keys。fact_keys必須指向kind=relation的事實，引文須同時有兩方企業名稱或標的代號。產業類似或模型記憶不足以證明客戶／供應商關係。事件entity沿關係接到標的後才可支持正配置。

反方事實用kind=counter，tickers可列出相關個股或留空表示市場共同風險；投資推論需引用適用的反方事實並解釋。價格觀察由程式從history計算，模型不能覆寫。

一份官方 D-Plan 最多100筆observations。本版對整份輸入建立觀察，請讓每日資料蒐集聚焦可用事件與候選池，避免大於上限；超限會回報，不會靜默丟掉證據。輸入不足以支持20–30檔組合時阻擋，不能以湊數理由建倉。
