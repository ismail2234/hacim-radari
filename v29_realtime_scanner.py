from __future__ import annotations

from typing import Any, Iterable

from dynamic_rsi_engine import DynamicRSIEngine
from order_book_engine import (
    OrderBookImbalanceEngine,
)
from price_velocity_engine import (
    PriceVelocityEngine,
)
from realtime_ws import BinanceTRRealtimeWS
from v29_state_manager import V29StateManager
from volume_spike_engine import (
    VolumeSpikeEngine,
)


class V29RealtimeScanner:
    """
    V29 gerçek zamanlı veri yöneticisi.

    WebSocket:
        trade
        depth

    verilerini ilgili motorlara dağıtır.

    Bu sınıf henüz eski scanner.py'yi değiştirmez.
    Bağımsız bir V29 gerçek zamanlı katmandır.
    """

    def __init__(
        self,
        symbols: Iterable[str],
    ) -> None:

        self.symbols = list(symbols)

        self.state = V29StateManager()

        self.volume_engines = {
            symbol: VolumeSpikeEngine()
            for symbol in self.symbols
        }

        self.velocity_engines = {
            symbol: PriceVelocityEngine()
            for symbol in self.symbols
        }

        self.orderbook_engines = {
            symbol: OrderBookImbalanceEngine()
            for symbol in self.symbols
        }

        self.rsi_engines = {
            symbol: DynamicRSIEngine()
            for symbol in self.symbols
        }

        self.ws = BinanceTRRealtimeWS(
            symbols=self.symbols,
            on_event=self._on_event,
        )

    @staticmethod
    def _symbol(event: dict[str, Any]) -> str:
        symbol = event.get("s", "")
        return str(symbol).upper()

    def _on_event(
        self,
        event: dict[str, Any],
    ) -> None:

        event_type = str(
            event.get("e", "")
        )

        symbol = self._symbol(event)

        if not symbol:
            return

        if symbol not in self.state.states:
            self.state.get(symbol)

        if (
            event_type in {
                "trade",
                "aggTrade",
            }
        ):
            self._handle_trade(
                symbol,
                event,
            )

        elif (
            event_type in {
                "depthUpdate",
            }
            or "bids" in event
            or "asks" in event
        ):
            self._handle_depth(
                symbol,
                event,
            )

    def _handle_trade(
        self,
        symbol: str,
        event: dict[str, Any],
    ) -> None:

        try:
            price = float(
                event.get(
                    "p",
                    event.get(
                        "price",
                        0,
                    ),
                )
            )

            quantity = float(
                event.get(
                    "q",
                    event.get(
                        "quantity",
                        0,
                    ),
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return

        timestamp_ms = event.get(
            "T",
            event.get(
                "_received_at_ms"
            ),
        )

        timestamp = None

        try:
            if timestamp_ms is not None:
                timestamp = (
                    float(timestamp_ms)
                    / 1000.0
                )
        except (
            TypeError,
            ValueError,
        ):
            timestamp = None

        volume = self.volume_engines[
            symbol
        ].update(
            quantity,
            timestamp,
        )

        velocity = self.velocity_engines[
            symbol
        ].update(
            price,
            timestamp,
        )

        self.state.update(
            symbol,
            price=price,
            volume_ratio=volume.volume_ratio,
            volume_score=self.volume_engines[
                symbol
            ].calculate_score(volume),
            velocity_score=velocity.score,
            price_velocity=(
                velocity.velocity_pct_per_second
            ),
            price_acceleration=(
                velocity.acceleration
            ),
        )

    def _handle_depth(
        self,
        symbol: str,
        event: dict[str, Any],
    ) -> None:

        bids = event.get(
            "bids",
            event.get("b", []),
        )

        asks = event.get(
            "asks",
            event.get("a", []),
        )

        if not isinstance(bids, list):
            return

        if not isinstance(asks, list):
            return

        result = self.orderbook_engines[
            symbol
        ].update(
            bids,
            asks,
        )

        self.state.update(
            symbol,
            orderbook_score=result.score,
            orderbook_imbalance=result.imbalance,
            bid_ratio=result.bid_ratio,
        )

    def update_rsi(
        self,
        symbol: str,
        rsi: float,
    ) -> None:

        symbol = symbol.upper()

        if symbol not in self.rsi_engines:
            return

        result = self.rsi_engines[
            symbol
        ].update(rsi)

        self.state.update(
            symbol,
            rsi=result.rsi,
            rsi_slope=result.slope,
            rsi_acceleration=(
                result.acceleration
            ),
            rsi_score=result.score,
        )

    def start(self) -> None:
        self.ws.start()

    def stop(self) -> None:
        self.ws.stop()

    def get_state(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        state = self.state.get(symbol)

        return {
            "symbol": state.symbol,
            "price": state.last_price,
            "score": state.last_score,
            "status": state.last_status,
            "signal": state.last_signal,
            **state.values,
        }

    def snapshot(
        self,
    ) -> dict[str, dict[str, Any]]:
        return self.state.snapshot()
      
