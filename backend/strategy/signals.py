from dataclasses import dataclass
from typing import Optional
from .fvg import FVGZone

@dataclass
class TradePlan:
    entry: float
    sl: float
    tp: float

from dataclasses import dataclass
from typing import Optional
from .fvg import FVGZone

@dataclass
class TradePlan:
    entry: float
    sl: float
    tp: float

@dataclass
class Signal:
    symbol: str
    fvg: FVGZone
    direction: str
    liquidity: Optional[str] = None
    reason: str = ""
    plan: TradePlan = None
    tf: Optional[str] = None

def build_signal_text(sig: Signal) -> str:
    liq = sig.liquidity or "—"
    return (
        f"📌 {sig.symbol}\n"
        f"• FVG: {sig.fvg.tf} ({sig.fvg.side}) [{sig.fvg.low} - {sig.fvg.high}]\n"
        f"• Direction: {sig.direction}\n"
        f"• Liquidity: {liq}\n"
        f"• Reason: {sig.reason}\n"
        f"• Plan:\n"
        f"   - Entry: {sig.plan.entry}\n"
        f"   - SL:    {sig.plan.sl}\n"
        f"   - TP:    {sig.plan.tp}\n"
    )