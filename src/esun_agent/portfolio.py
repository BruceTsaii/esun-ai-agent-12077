from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .common import AgentError, decimal
from .models import DataPack

FEE = Decimal("0.001425")
TAX = Decimal("0.003")


def target_shares(weight, nav, close) -> int:
    if decimal(close) <= 0 or decimal(weight) < 0:
        raise AgentError("無效股數換算輸入")
    return (
        int(
            (decimal(weight) * decimal(nav) / decimal(close) / 1000).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        * 1000
    )


def derive_orders(decisions: list[dict], dp: DataPack, prices: dict) -> list[dict]:
    dp.ready()
    held = {h.code: h.shares for h in dp.holdings}
    orders = []
    for d in decisions:
        t = d["ticker"]
        if t not in prices:
            raise AgentError(f"{t}: 缺前日收盤價，不能換算委託")
        price = prices[t].close
        target = target_shares(d["target_weight"], dp.account.nav_tradable, price)
        current = held.get(t, 0)
        delta = target - current
        if not delta:
            raise AgentError(f"{t}: 零委託應列入 no_trade_decisions")
        expected = (
            ("BUY" if not current else "ADD")
            if delta > 0
            else ("SELL_ALL" if not target else "TRIM")
        )
        if d["action"] != expected:
            raise AgentError(f"{t}: action 應為 {expected}")
        if abs(delta) % 1000:
            raise AgentError(f"{t}: 持股含零股，官方公式導出非千股單位，不可手調")
        orders.append(
            {
                "ticker": t,
                "side": "BUY" if delta > 0 else "SELL",
                "shares": abs(delta),
                "decision_ref": d["decision_id"],
            }
        )
    return orders


def project(dp: DataPack, orders: list[dict], prices: dict, stress=0) -> dict:
    """Net settlement; fees rounded UP per order as a conservative reserve.

    T-1 valuation, not a claim about T-day VWAP settlement. Stress buys up/sells down.
    """
    dp.ready()
    shares = {h.code: h.shares for h in dp.holdings if h.shares}
    cash = decimal(dp.account.cash)
    fee_total = tax_total = buy = sell = Decimal(0)
    for order in orders:
        t, qty = order["ticker"], order["shares"]
        if t not in prices or type(qty) is not int or qty <= 0 or qty % 1000:
            raise AgentError("委託缺價格/非千股單位")
        if order["side"] not in {"BUY", "SELL"}:
            raise AgentError("無效委託方向")
        is_buy = order["side"] == "BUY"
        px = decimal(prices[t].close) * (1 + decimal(stress) * (1 if is_buy else -1))
        amount = px * qty
        fee = (amount * FEE).to_integral_value(rounding=ROUND_CEILING)
        tax = Decimal(0) if is_buy else (amount * TAX).to_integral_value(rounding=ROUND_CEILING)
        shares[t] = shares.get(t, 0) + qty * (1 if is_buy else -1)
        if shares[t] < 0:
            raise AgentError(f"{t}: 超賣；應收配股不可賣出")
        cash += (-amount if is_buy else amount) - fee - tax
        buy += amount if is_buy else 0
        sell += amount if not is_buy else 0
        fee_total += fee
        tax_total += tax
    shares = {t: n for t, n in shares.items() if n}
    values = {t: n * decimal(prices[t].close) for t, n in shares.items()}
    nav = cash + sum(values.values())
    if nav <= 0:
        raise AgentError("預估淨值非正")
    return {
        "cash": float(cash),
        "nav_tradable": float(nav),
        "cash_ratio": float(cash / nav),
        "holdings_count": len(shares),
        "shares": shares,
        "weights": {t: float(v / nav) for t, v in values.items()},
        "buy_notional": float(buy),
        "sell_notional": float(sell),
        "fee_reserve": float(fee_total),
        "tax_reserve": float(tax_total),
    }


def active_share(weights: dict, benchmark: dict, normalized=False) -> float:
    """Top-15 union; report both raw NAV weights and renormalized top-15 variants."""

    def top(w):
        result = dict(sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:15])
        if normalized:
            total = sum(result.values())
            if not total:
                raise AgentError("Active Share 空權重")
            result = {t: v / total for t, v in result.items()}
        return result

    a, b = top(weights), top(benchmark)
    return 0.5 * sum(abs(a.get(t, 0) - b.get(t, 0)) for t in a.keys() | b.keys())
