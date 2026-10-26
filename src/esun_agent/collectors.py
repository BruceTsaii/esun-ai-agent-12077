"""Public HTTPS collection; latest quotes never silently masquerade as historical quotes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .common import AgentError, resource, sha256, stamp, write_json

ENDPOINTS = {
    "twse": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "tpex": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
}


def public_fetch(url: str) -> bytes:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise AgentError("來源須使用無憑證 HTTPS URL")
    try:
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            r = client.get(url, headers={"Accept": "application/json,text/plain,text/html"})
        if r.status_code != 200:
            raise AgentError(f"公開來源 HTTP {r.status_code}: {u.hostname}")
        return r.content
    except httpx.HTTPError as exc:
        raise AgentError(f"公開來源網路錯誤 {u.hostname} ({type(exc).__name__})") from None


def exchange_date(value: str) -> date:
    digits = value.replace("/", "").replace("-", "").strip()
    if not digits.isdigit() or len(digits) not in {7, 8}:
        raise AgentError(f"無法辨識交易所日期 {value}")
    year = int(digits[:-4])
    if year < 1911:
        year += 1911
    return date(year, int(digits[-4:-2]), int(digits[-2:]))


def normalize_quotes(rows: list[dict], market: str, expected_date: date):
    allowed = {r["code"] for r in resource("universe.json")["stocks"] if r["market"] == market}
    result = []
    for row in rows:
        code = row.get("Code") if market == "twse" else row.get("SecuritiesCompanyCode")
        if code not in allowed:
            continue
        raw_date = row.get("Date")
        raw_close = row.get("ClosingPrice") if market == "twse" else row.get("Close")
        if raw_date is None:
            raise AgentError(f"{market}: 來源缺 Date，不能推測行情日期")
        actual = exchange_date(raw_date)
        if actual != expected_date:
            raise AgentError(
                f"{market}: 回傳 {actual} 非要求的 {expected_date}；拒絕使用過期/未來行情"
            )
        if raw_close in {None, "", "--", "---", "----"}:
            continue
        close = float(str(raw_close).replace(",", ""))
        if close <= 0:
            raise AgentError("交易所價格非正")
        result.append({"ticker": code, "date": str(actual), "close": close})
    if not result or len({r["ticker"] for r in result}) != len(result):
        raise AgentError(f"{market}: 無有效價格或代號重複（API 格式可能已變）")
    return result


def collect_quotes(expected_date: date, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    failures = []
    for market, url in ENDPOINTS.items():
        try:
            raw = public_fetch(url)
            raw_file = output / f"{market}-raw.json"
            if raw_file.exists():
                raise AgentError(f"不覆寫既有快照 {raw_file}；請指定新目錄")
            raw_file.write_bytes(raw)
            prices = normalize_quotes(json.loads(raw), market, expected_date)
            snapshot = {
                "origin_url": url,
                "origin_sha256": sha256(raw),
                "origin_file": raw_file.name,
                "prices": prices,
            }
            target = output / f"{market}-prices.json"
            write_json(target, snapshot)
            manifest[market] = {
                "url": url,
                "authority": market,
                "fetched_at": stamp(),
                "date": str(expected_date),
                "snapshot_path": target.name,
                "sha256": sha256(target.read_bytes()),
                "price_count": len(prices),
            }
        except (AgentError, ValueError, TypeError) as exc:
            failures.append(f"{market}: {exc}")
    write_json(output / "collection.json", {"sources": manifest, "errors": failures})
    if failures:
        raise AgentError("；".join(failures))
    return manifest


def normalize_history(data: dict, market: str, expected_date: date):
    """Official TWSE MI_INDEX / current TPEx dailyQuotes; never ignore the response date."""
    if data.get("stat") not in {"OK", "ok"}:
        raise AgentError(f"{market}: 歷史行情來源未回報成功")
    if exchange_date(str(data.get("date", ""))) != expected_date:
        raise AgentError(f"{market}: 歷史端點回傳日期非 {expected_date}，拒絕當作前日行情")
    tables = list(data.get("tables", []))
    for key, value in data.items():
        if key.startswith("fields"):
            suffix = key[len("fields") :]
            if "data" + suffix in data:
                tables.append({"fields": value, "data": data["data" + suffix]})
    allowed = {s["code"] for s in resource("universe.json")["stocks"] if s["market"] == market}
    result = {}
    for table in tables:
        fields = table.get("fields", [])
        code_name = next((f for f in ["證券代號", "代號"] if f in fields), None)
        close_name = next((f for f in ["收盤價", "收盤"] if f in fields), None)
        if code_name is None or close_name is None:
            continue
        ci, pi = fields.index(code_name), fields.index(close_name)
        for row in table.get("data", []):
            if len(row) <= max(ci, pi):
                raise AgentError("歷史行情欄位不完整")
            ticker = str(row[ci]).strip()
            if ticker not in allowed:
                continue
            value = str(row[pi]).replace(",", "").strip()
            if value in {"", "--", "---", "----"}:
                continue
            close = float(value)
            if close <= 0 or ticker in result:
                raise AgentError("歷史行情非正價格或重複代號")
            result[ticker] = {"ticker": ticker, "date": str(expected_date), "close": close}
    if not result:
        raise AgentError("歷史行情沒有官方股票池價格或 API 欄位已變更")
    return list(result.values())


def collect_history(expected_date: date, market: str, output: Path):
    if market not in {"twse", "tpex"}:
        raise AgentError("市場須為 twse 或 tpex")
    if output.exists() and any(output.iterdir()):
        raise AgentError("不覆寫既有歷史行情，請使用新目錄")
    if market == "twse":
        url = (
            "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date="
            + expected_date.strftime("%Y%m%d")
            + "&type=ALLBUT0999"
        )
    else:
        url = (
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date="
            + expected_date.strftime("%Y/%m/%d")
            + "&response=json"
        )
    raw = public_fetch(url)
    output.mkdir(parents=True, exist_ok=True)
    raw_file = output / f"{market}-raw.json"
    raw_file.write_bytes(raw)
    prices = normalize_history(json.loads(raw), market, expected_date)
    path = output / f"{market}-prices.json"
    write_json(
        path,
        {
            "origin_url": url,
            "origin_file": raw_file.name,
            "origin_sha256": sha256(raw),
            "prices": prices,
        },
    )
    report = {
        "url": url,
        "authority": market,
        "fetched_at": stamp(),
        "date": str(expected_date),
        "snapshot_path": path.name,
        "sha256": sha256(path.read_bytes()),
        "price_count": len(prices),
    }
    write_json(output / "collection.json", report)
    return report


def trading_dates(year: int, official_schedule: list[dict]):
    """Weekdays minus actual holiday/no-trading rows, with explicit opening dates retained."""
    from datetime import timedelta

    closed, opened = set(), set()
    for row in official_schedule:
        day = exchange_date(row["Date"])
        if day.year != year:
            raise AgentError("官方休市表年度與要求不符")
        name = row["Name"]
        if "開始交易" in name or "最後交易" in name:
            opened.add(day)
        else:
            closed.add(day)
    current, end = date(year, 1, 1), date(year + 1, 1, 1)
    result = []
    while current < end:
        if current in opened or (current.weekday() < 5 and current not in closed):
            result.append(str(current))
        current += timedelta(days=1)
    return result
