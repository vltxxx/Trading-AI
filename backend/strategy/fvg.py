from dataclasses import dataclass
from enum import Enum


class FVGSide(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass
class FVGZone:
    side: FVGSide
    tf: str
    created_ts: int
    low: float
    high: float
    mid: float
    active: bool = True


def detect_fvg(candles, tf: str = "1H"):
    fvg_list = []

    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]

        # Bullish FVG
        if c1["high"] < c3["low"]:
            low = c1["high"]
            high = c3["low"]
            mid = (low + high) / 2
            fvg_list.append(
                FVGZone(
                    side=FVGSide.BULLISH,
                    tf=tf,
                    created_ts=c3.get("ts", i),
                    low=low,
                    high=high,
                    mid=mid,
                )
            )

        # Bearish FVG
        if c1["low"] > c3["high"]:
            low = c3["high"]
            high = c1["low"]
            mid = (low + high) / 2
            fvg_list.append(
                FVGZone(
                    side=FVGSide.BEARISH,
                    tf=tf,
                    created_ts=c3.get("ts", i),
                    low=low,
                    high=high,
                    mid=mid,
                )
            )

    return fvg_list
def price_in_zone(price: float, zone: FVGZone) -> bool:
    return zone.low <= price <= zone.high