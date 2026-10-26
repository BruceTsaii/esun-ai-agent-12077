"""Read auditable public evidence. Models cannot invent sources or observations."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator

from .common import TAIPEI, AgentError, now, resource, sha256
from .models import DataPack, StrategyConfig, StrictModel


class Source(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    authority: Literal["twse", "tpex", "taifex", "mops", "fininst", "media", "vendor", "other"]
    url: str
    content_as_of: datetime
    fetched_at: datetime
    published_at: datetime | None = None
    archive_url: str | None = None
    snapshot_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Citation(StrictModel):
    source_key: str
    quote: str = Field(min_length=5, max_length=2000)


class Fact(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    kind: Literal["market", "event", "relation", "counter"]
    entity: str | None = None
    tickers: list[str] = Field(default_factory=list)
    statement: str = Field(min_length=5, max_length=400)
    values: dict[str, float] = Field(default_factory=dict)
    citations: list[Citation] = Field(min_length=1, max_length=10)

    @field_validator("key")
    @classmethod
    def reserved_prefix(cls, value):
        if value.startswith("price_"):
            raise ValueError("price_ 前綴保留給程式產生的價格觀察")
        return value


class Relationship(StrictModel):
    key: str
    from_entity: str = Field(min_length=2)
    to_ticker: str = Field(pattern=r"^\d{4}$")
    relation: str = Field(min_length=2, max_length=200)
    fact_keys: list[str] = Field(min_length=1)


class Price(StrictModel):
    ticker: str = Field(pattern=r"^\d{4}$")
    date: date
    close: float = Field(gt=0)
    source_key: str


class History(Price):
    benchmark_close: float = Field(gt=0)
    benchmark_source_key: str


class Benchmark(StrictModel):
    code: str
    as_of_date: date
    source_key: str
    # Full disclosed portfolio weights (not normalized to top 15).
    weights: dict[str, float]


class TradingCalendar(StrictModel):
    source_key: str
    trade_dates: list[date] = Field(min_length=2)


class EvidenceBundle(StrictModel):
    is_test: bool = Field(strict=True)
    previous_close_date: date
    sources: list[Source] = Field(min_length=1, max_length=50)
    facts: list[Fact] = Field(min_length=1)
    relationships: list[Relationship]
    prices: list[Price]
    history: list[History]
    benchmarks: list[Benchmark] = Field(default_factory=list)
    calendar: TradingCalendar | None = None


def unique(items, field):
    result = {getattr(x, field): x for x in items}
    if len(result) != len(items):
        raise AgentError(f"重複 {field}")
    return result


def verify_bundle(
    bundle: EvidenceBundle, root: Path, dp: DataPack, config: StrategyConfig, mode: str
) -> dict:
    universe = {r["code"]: r for r in resource("universe.json")["stocks"]}
    if mode == "live" and bundle.is_test:
        raise AgentError("正式模式拒絕 is_test=true 證據")
    if bundle.previous_close_date >= dp.trade_date:
        raise AgentError("行情日期必須早於交易日")
    if dp.as_of_date is not None and bundle.previous_close_date != dp.as_of_date:
        raise AgentError("行情日期與 DataPack 結算日不符")
    cutoff = min(
        now(),
        datetime.combine(dp.trade_date, datetime.min.time(), TAIPEI)
        + timedelta(hours=8, minutes=55),
    )
    if mode == "live" and bundle.previous_close_date > cutoff.date():
        raise AgentError("前日收盤尚未發生，不能使用未來行情")
    if mode == "live" and dp.generated_at > cutoff:
        raise AgentError("DataPack 產生時間在未來或晚於該日提交截止")
    sources = unique(bundle.sources, "key")
    facts = unique(bundle.facts, "key")
    relations = unique(bundle.relationships, "key")
    prices = unique(bundle.prices, "ticker")
    snapshots = {}
    for s in sources.values():
        url = urlparse(s.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise AgentError(f"{s.key}: 必須使用無憑證的 HTTPS 公開來源")
        if mode == "live" and (
            url.hostname.endswith(".example")
            or url.hostname in {"example.com", "example.org"}
            or ".example.com" in url.hostname
        ):
            raise AgentError(f"{s.key}: 正式資料不可使用範例網址")
        if mode == "live" and s.authority in {"twse", "tpex", "taifex", "mops"}:
            host = {
                "twse": "twse.com.tw",
                "tpex": "tpex.org.tw",
                "taifex": "taifex.com.tw",
                "mops": "twse.com.tw",
            }[s.authority]
            if url.hostname != host and not url.hostname.endswith("." + host):
                raise AgentError(f"{s.key}: 官方 authority 與來源網域不一致")
        for ts in [s.content_as_of, s.fetched_at, s.published_at]:
            if ts is None:
                continue
            if ts.utcoffset() != timedelta(hours=8):
                raise AgentError(f"{s.key}: 時間須為 +08:00")
            if mode == "live" and ts > cutoff:
                raise AgentError(f"{s.key}: 未來資料或逾決策截止時間")
        if s.content_as_of > s.fetched_at:
            raise AgentError(f"{s.key}: 描述時間晚於抓取時間")
        if s.published_at and s.published_at > s.fetched_at:
            raise AgentError(f"{s.key}: 發布時間晚於抓取時間")
        p = (root / s.snapshot_path).resolve()
        if not p.is_relative_to(root.resolve()) or not p.is_file():
            raise AgentError(f"{s.key}: snapshot 缺失或超出證據目錄")
        raw = p.read_bytes()
        if sha256(raw) != s.sha256:
            raise AgentError(f"{s.key}: snapshot SHA256 不符")
        snapshots[s.key] = raw.decode("utf-8")
    # First-day DataPack has no settlement date. Never guess T-1 as a weekday.
    if mode == "live" and dp.as_of_date is None and bundle.calendar is None:
        raise AgentError("首日帳戶需官方交易日曆確認前一交易日，不能以平日推算")
    if bundle.calendar:
        import json

        cal = bundle.calendar
        if cal.source_key not in sources or sources[cal.source_key].authority != "twse":
            raise AgentError("交易日曆缺交易所來源")
        days = cal.trade_dates
        if days != sorted(set(days)) or dp.trade_date not in days:
            raise AgentError("交易日曆須不重複、升序且含該交易日")
        i = days.index(dp.trade_date)
        if i == 0 or days[i - 1] != bundle.previous_close_date:
            raise AgentError("行情日期非官方日曆的前一交易日")
        try:
            if json.loads(snapshots[cal.source_key])["trade_dates"] != list(map(str, days)):
                raise AgentError("交易日曆與來源快照不符")
        except (ValueError, KeyError, TypeError) as exc:
            raise AgentError("日曆快照須為含 trade_dates 的 JSON") from exc
    for f in facts.values():
        if any(t not in universe for t in f.tickers):
            raise AgentError(f"{f.key}: 非官方股票池")
        quotes = []
        for c in f.citations:
            if c.source_key not in sources or c.quote not in snapshots[c.source_key]:
                raise AgentError(f"{f.key}: 引文未出現在來源快照")
            quotes.append(c.quote)
        # Numeric facts must literally occur in evidence, permitting commas/percent signs.
        numbers = {
            float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", " ".join(quotes).replace(",", ""))
        }
        if any(v not in numbers for v in f.values.values()):
            raise AgentError(f"{f.key}: 數值沒有原始引文支持")
        if f.kind == "event":
            if not f.entity:
                raise AgentError(f"{f.key}: 事件缺 entity")
            age = (
                bundle.previous_close_date
                - max(sources[c.source_key].content_as_of.date() for c in f.citations)
            ).days
            if age > config.max_event_age_days:
                raise AgentError(f"{f.key}: 事件過期，請更新進展證據")
    for r in relations.values():
        if r.to_ticker not in universe:
            raise AgentError(f"{r.key}: 關係標的不在官方池")
        for key in r.fact_keys:
            if key not in facts or facts[key].kind != "relation":
                raise AgentError(f"{r.key}: 缺企業關係事實來源")
        quotes = " ".join(c.quote for k in r.fact_keys for c in facts[k].citations)
        if r.from_entity not in quotes or not any(
            name in quotes for name in [r.to_ticker, universe[r.to_ticker]["name"]]
        ):
            raise AgentError(f"{r.key}: 關係引文沒有同時提到兩方企業")
        if any(
            (bundle.previous_close_date - sources[c.source_key].content_as_of.date()).days
            > config.max_relationship_age_days
            for k in r.fact_keys
            for c in facts[k].citations
        ):
            raise AgentError(f"{r.key}: 企業關係證據過期")
    for p in prices.values():
        if p.ticker not in universe or p.source_key not in sources:
            raise AgentError(f"{p.ticker}: 價格來源或股票池錯誤")
        if p.date != bundle.previous_close_date:
            raise AgentError(f"{p.ticker}: 非前日收盤價")
        if sources[p.source_key].authority != universe[p.ticker]["market"]:
            raise AgentError(f"{p.ticker}: 上市/上櫃行情來源錯置")
        if p.date > sources[p.source_key].content_as_of.date():
            raise AgentError(f"{p.ticker}: 收盤資料晚於來源描述日期")
        verify_price_snapshot(p, snapshots[p.source_key])
    history = {}
    for h in bundle.history:
        if h.ticker not in prices or h.date > bundle.previous_close_date:
            raise AgentError("歷史價格日期/標的錯誤")
        if h.source_key not in sources or h.benchmark_source_key not in sources:
            raise AgentError("歷史價格/指數缺來源")
        if any(
            h.date > sources[key].content_as_of.date()
            for key in [h.source_key, h.benchmark_source_key]
        ):
            raise AgentError("歷史價格晚於來源描述日期")
        if sources[h.source_key].authority != universe[h.ticker]["market"]:
            raise AgentError("歷史價格上市/上櫃來源錯置")
        verify_price_snapshot(h, snapshots[h.source_key])
        verify_price_snapshot(h, snapshots[h.benchmark_source_key], benchmark=True)
        history.setdefault(h.ticker, []).append(h)
    for ticker, rows in history.items():
        if len({r.date for r in rows}) != len(rows):
            raise AgentError(f"{ticker}: 歷史價格日期重複")
        rows.sort(key=lambda r: r.date)
        if rows[-1].date != bundle.previous_close_date or rows[-1].close != prices[ticker].close:
            raise AgentError(f"{ticker}: 歷史價格沒有最新收盤或價格矛盾")
    for h in dp.holdings or []:
        if h.shares and (h.code not in prices or prices[h.code].close != h.close_price):
            raise AgentError(f"{h.code}: 缺行情或與帳戶結算收盤價不符")
    if not any(f.kind == "market" for f in facts.values()):
        raise AgentError("缺市場級事實")
    for b in bundle.benchmarks:
        if b.source_key not in sources or sources[b.source_key].authority != "fininst":
            raise AgentError("ETF 持股必須引用金融機構來源")
        if (
            not 0
            <= (bundle.previous_close_date - b.as_of_date).days
            <= config.max_benchmark_age_days
        ):
            raise AgentError("ETF 持股日期過期/未來")
        if len(b.weights) < 15 or any(v < 0 or v > 1 for v in b.weights.values()):
            raise AgentError("ETF 權重無效或少於 15 檔")
        if sum(b.weights.values()) > 1.001:
            raise AgentError("ETF 權重總和超過 100%")
        # Keep normalized holdings in the archived disclosure, not unsupported model claims.
        import json

        try:
            recorded = json.loads(snapshots[b.source_key])["etf_holdings"][b.code]
            if recorded["as_of_date"] != str(b.as_of_date) or recorded["weights"] != b.weights:
                raise AgentError("ETF 持股與來源快照不符")
        except (ValueError, KeyError, TypeError) as exc:
            raise AgentError("ETF 快照須含 etf_holdings/code/as_of_date/weights") from exc
    return {
        "sources": sources,
        "facts": facts,
        "relations": relations,
        "prices": prices,
        "history": history,
        "snapshots": snapshots,
    }


def verify_price_snapshot(p: Price, raw: str, benchmark: bool = False):
    """Normalized snapshot contract, with original exchange response retained by collector."""
    import json

    try:
        data = json.loads(raw)
        key = "benchmark" if benchmark else p.ticker
        value = p.benchmark_close if benchmark else p.close
        if not any(
            row["date"] == str(p.date) and row["ticker"] == key and row["close"] == value
            for row in data["prices"]
        ):
            raise AgentError(f"{p.ticker}: 行情數值不在快照中")
    except (ValueError, KeyError, TypeError) as exc:
        raise AgentError(f"{p.ticker}: 行情快照須為含 prices 的 JSON") from exc


def build_observations(bundle: EvidenceBundle, verified: dict):
    """Model receives immutable observations, derived numeric metrics and relationship graph."""
    source_ids = {s.key: f"S{i}" for i, s in enumerate(bundle.sources, 1)}
    sources = []
    for s in bundle.sources:
        row = {
            "source_id": source_ids[s.key],
            "authority": s.authority,
            "url": s.url,
            "content_as_of": s.content_as_of.isoformat(),
            "fetched_at": s.fetched_at.isoformat(),
        }
        for k in ["published_at", "archive_url"]:
            v = getattr(s, k)
            if v is not None:
                row[k] = v.isoformat() if isinstance(v, datetime) else v
        sources.append(row)
    observations, obs_keys = [], {}

    def add(key, refs, statement, values):
        oid = f"O{len(observations) + 1}"
        obs_keys[key] = oid
        observations.append(
            {
                "obs_id": oid,
                "source_ref": list(dict.fromkeys(refs)),
                "statement": statement,
                "values": values,
            }
        )

    for f in bundle.facts:
        values = f.values or {"verified_citation_count": len(f.citations)}
        statement = f.statement
        if not f.values:
            statement += f"（已核對原文引文 {len(f.citations)} 筆；此數字僅為引文筆數）"
        add(f.key, [source_ids[c.source_key] for c in f.citations], statement, values)
    features = {}
    for ticker, rows in verified["history"].items():
        if len(rows) < 2:
            continue
        last, first = rows[-1], rows[0]
        trend = last.close / first.close - 1
        returns = {}
        refs = {source_ids[r.source_key] for r in rows}
        refs.update(source_ids[r.benchmark_source_key] for r in rows)
        for event in bundle.facts:
            if event.kind != "event":
                continue
            # A 14:00 announcement cannot have affected the same day's 13:30 close.
            event_time = max(
                verified["sources"][c.source_key].published_at
                or verified["sources"][c.source_key].content_as_of
                for c in event.citations
            )

            def closed_at(row):
                return datetime.combine(row.date, datetime.min.time(), TAIPEI) + timedelta(
                    hours=13, minutes=30
                )

            baseline = [r for r in rows if closed_at(r) < event_time]
            after = [r for r in rows if closed_at(r) >= event_time]
            if baseline and after:
                start = baseline[-1]
                returns[event.key] = (last.close / start.close - 1) - (
                    last.benchmark_close / start.benchmark_close - 1
                )
                refs.update(source_ids[c.source_key] for c in event.citations)
        values = {"close": last.close, "trend_return": trend, "history_sessions": len(rows)}
        values.update({f"relative_{k}": v for k, v in returns.items()})
        # At most 10 sources per observation as required by official schema.
        if len(refs) > 10:
            raise AgentError("價格歷史來源超過單筆引用限制，請合併同來源快照")
        add(
            "price_" + ticker,
            sorted(refs),
            f"{ticker} 收盤 {last.close}；{first.date} 至 {last.date} 報酬 {trend:.6f}；"
            "relative 欄位為事件前一收盤至最近收盤相對基準報酬差（比率）。",
            values,
        )
        features[ticker] = {
            "trend_return": trend,
            "event_relative_returns": returns,
            "obs_id": obs_keys["price_" + ticker],
        }
    if len(observations) > 100:
        raise AgentError("事實超過官方 100 筆上限，請縮減至本次實際決策的證據")
    return sources, observations, obs_keys, features
