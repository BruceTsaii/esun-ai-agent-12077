from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentError, decimal


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PreviousOrders(StrictModel):
    outcome: Literal["none", "unknown", "voided", "executed"]
    void_reasons: list[str]
    excluded_codes: list[str]

    @model_validator(mode="after")
    def consistency(self):
        if (self.outcome == "voided") != bool(self.void_reasons):
            raise ValueError("voided 與 void_reasons 不一致")
        return self


class Account(StrictModel):
    cash: float = Field(ge=0)
    cash_ratio_tradable: float | None = Field(ge=0, le=1)
    nav_tradable: float = Field(gt=0)
    aum: float = Field(gt=0)
    receivable_cash: float = Field(ge=0)
    holdings_count: int = Field(ge=0, strict=True)


class Holding(StrictModel):
    code: str = Field(pattern=r"^\d{4}$")
    shares: int = Field(ge=0, strict=True)
    receivable_shares: int = Field(ge=0, strict=True)
    close_price: float | None = Field(gt=0)
    tradable_weight: float | None = Field(ge=0, le=1)


class Incident(StrictModel):
    date: date
    check_code: str
    status: Literal["warn", "fail"]
    message: str


class Compliance(StrictModel):
    violations: int = Field(ge=0, strict=True)
    notices: int = Field(ge=0, strict=True)
    incidents: list[Incident]


class DataPack(StrictModel):
    schema_version: Literal["3.0"]
    doc_type: Literal["DataPack"]
    team_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    trade_date: date
    as_of_date: date | None
    generated_at: datetime
    missing: list[Literal["prev_settlement", "prev_close"]]
    prev_day_orders: PreviousOrders
    account: Account | None
    holdings: list[Holding] | None
    compliance: Compliance

    @model_validator(mode="after")
    def integrity(self):
        if self.generated_at.utcoffset() != timedelta(hours=8):
            raise ValueError("generated_at 須為台北時間 +08:00")
        if self.as_of_date and self.as_of_date >= self.trade_date:
            raise ValueError("as_of_date 必須早於 trade_date")
        if "prev_settlement" in self.missing:
            if any(x is not None for x in [self.account, self.holdings, self.as_of_date]):
                raise ValueError("缺結算時 account/holdings/as_of_date 應皆為 null")
            return self
        if self.account is None or self.holdings is None:
            raise ValueError("無 prev_settlement 卻缺 account/holdings")
        codes = [h.code for h in self.holdings]
        if len(codes) != len(set(codes)):
            raise ValueError("持股代號重複")
        a = self.account
        count = sum(h.shares > 0 for h in self.holdings)
        if count != a.holdings_count:
            raise ValueError("holdings_count 不符")
        if self.as_of_date is None and (
            count or a.receivable_cash or any(h.receivable_shares for h in self.holdings)
        ):
            raise ValueError("首次全現金帳戶與 as_of_date=null 不一致")
        nav = decimal(a.cash) + sum(decimal(h.close_price or 0) * h.shares for h in self.holdings)
        if abs(nav - decimal(a.nav_tradable)) > decimal("1"):
            raise ValueError("nav_tradable 與可交易持股、現金不一致")
        if a.aum + 1 < a.nav_tradable + a.receivable_cash:
            raise ValueError("aum 不得低於可交易淨值加應收現金")
        if self.missing and a.cash_ratio_tradable is not None:
            raise ValueError("missing 非空時現金比應為 null")
        if not self.missing and (
            a.cash_ratio_tradable is None
            or abs(a.cash / a.nav_tradable - a.cash_ratio_tradable) > 1e-6
        ):
            raise ValueError("cash_ratio_tradable 不符")
        for h in self.holdings:
            expected = (
                h.shares * h.close_price / a.nav_tradable if h.shares and h.close_price else None
            )
            if expected is None:
                if h.tradable_weight is not None:
                    raise ValueError(f"{h.code}: 不適用權重應為 null")
            elif h.tradable_weight is None or abs(expected - h.tradable_weight) > 1e-6:
                raise ValueError(f"{h.code}: tradable_weight 不符")
        return self

    def ready(self):
        if self.missing or self.account is None or self.holdings is None:
            raise AgentError(f"DataPack 缺資料 {self.missing}；請重新下載完成結算的檔案")
        if self.compliance.violations >= 3:
            raise AgentError("累計警告已達三日，請確認競賽資格")
        if any(h.shares and h.close_price is None for h in self.holdings):
            raise AgentError("可交易持股缺少收盤價")

    def summary(self):
        return {
            "team_id": self.team_id,
            "trade_date": str(self.trade_date),
            "as_of_date": str(self.as_of_date) if self.as_of_date else None,
            "missing": self.missing,
            "account": self.account.model_dump() if self.account else None,
            "previous_orders": self.prev_day_orders.model_dump(),
            "compliance": self.compliance.model_dump(mode="json"),
            "first_day_cash": self.as_of_date is None and self.account is not None,
        }


class ModelConfig(StrictModel):
    provider: Literal["openai", "google", "anthropic", "other"] = "google"
    model: str = Field(min_length=3, max_length=60)
    api_key_env: str = "GEMINI_API_KEY"
    base_url: str | None = None
    timeout_seconds: float = Field(default=120, gt=0, le=300)
    max_output_tokens: int = Field(default=16000, ge=1000, le=64000)


class StrategyConfig(StrictModel):
    max_event_weight: float = Field(default=0.30, gt=0, le=1)
    max_opportunity_weight: float = Field(default=0.35, ge=0, le=1)
    min_core_weight: float = Field(default=0.45, ge=0, le=1)
    min_cash_buffer: float = Field(default=0.03, gt=0, lt=0.25)
    max_event_age_days: int = Field(default=45, ge=1)
    max_relationship_age_days: int = Field(default=400, ge=1)
    max_benchmark_age_days: int = Field(default=7, ge=0)
    stress_price_move: float = Field(default=0.03, ge=0, le=0.2)


class RunConfig(StrictModel):
    model: ModelConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
