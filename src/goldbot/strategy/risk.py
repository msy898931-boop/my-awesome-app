"""Position sizing — how much to risk, stated plainly.

This is arithmetic, not advice. Given an account, a risk percentage, and the
plan's entry and stop, it returns the size that would lose exactly that
percentage if the stop is hit. It never assumes leverage, never compounds, and
never places anything — it answers a question the trader asked.
"""

from __future__ import annotations


def position_size(
    account_usd: float, risk_pct: float, entry: float, stop: float,
    contract_size: float = 100.0,
) -> dict:
    """Lots to trade so a stop-out costs ``risk_pct`` of the account.

    ``contract_size`` is ounces per lot (100 for standard XAUUSD). Returns the
    dollar risk, the per-ounce risk, and the lot size, each rounded to a usable
    precision. Invalid inputs return a zeroed result with the reason, never a
    silent guess.
    """
    risk_usd = account_usd * (risk_pct / 100.0)
    per_ounce = abs(entry - stop)
    if risk_usd <= 0 or per_ounce <= 0 or contract_size <= 0:
        return {
            "risk_usd": 0.0, "per_ounce": round(per_ounce, 2), "lots": 0.0,
            "note": "مدخلات غير صالحة — لا يمكن حساب الحجم",
        }
    lots = risk_usd / (per_ounce * contract_size)
    return {
        "risk_usd": round(risk_usd, 2),
        "per_ounce": round(per_ounce, 2),
        "lots": round(lots, 2),
        "note": f"مخاطرة {risk_pct}% من حساب {account_usd:.0f}$",
    }
