from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class SymbolState:
    symbol: str
    last_price: float = 0.0
    last_score: int = 0
    last_status: str = "BEKLE"
    last_signal: str = "WAIT"
    last_update: float = 0.0
    values: dict[str, Any] = field(
        default_factory=dict
    )


class V29StateManager:
    """
    Her sembolün son V29 durumunu hafızada tutar.

    Böylece her tick'te sıfırdan durum oluşturmak
    yerine önceki değerlerle karşılaştırma yapılabilir.
    """

    def __init__(self) -> None:
        self.states: dict[str, SymbolState] = {}

    def get(self, symbol: str) -> SymbolState:
        symbol = symbol.upper()

        if symbol not in self.states:
            self.states[symbol] = SymbolState(
                symbol=symbol
            )

        return self.states[symbol]

    def update(
        self,
        symbol: str,
        **values: Any,
    ) -> SymbolState:
        state = self.get(symbol)

        state.last_update = time()

        if "price" in values:
            state.last_price = float(
                values["price"]
            )

        if "score" in values:
            state.last_score = int(
                values["score"]
            )

        if "status" in values:
            state.last_status = str(
                values["status"]
            )

        if "signal" in values:
            state.last_signal = str(
                values["signal"]
            )

        state.values.update(values)

        return state

    def changed_significantly(
        self,
        symbol: str,
        score: int,
        minimum_change: int = 10,
    ) -> bool:
        state = self.get(symbol)

        return abs(
            int(score) - state.last_score
        ) >= minimum_change

    def snapshot(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for symbol, state in self.states.items():
            result[symbol] = {
                "symbol": state.symbol,
                "price": state.last_price,
                "score": state.last_score,
                "status": state.last_status,
                "signal": state.last_signal,
                "last_update": state.last_update,
                **state.values,
            }

        return result

    def remove(self, symbol: str) -> None:
        self.states.pop(
            symbol.upper(),
            None,
        )

    def clear(self) -> None:
        self.states.clear()
              
