from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import websocket


class BinanceTRRealtimeWS:
    """
    V29 gerçek zamanlı piyasa veri bağlantısı.

    WebSocket üzerinden:
    - trade
    - depth@100ms

    verilerini dinler.

    Not:
    Bu sınıf sinyal üretmez.
    Sadece gerçek zamanlı veriyi alıp callback'e iletir.
    """

    BASE_URL = "wss://stream-cloud.binance.tr"

    def __init__(
        self,
        symbols: Iterable[str],
        on_event: Callable[[dict[str, Any]], None],
        streams: tuple[str, ...] = (
            "trade",
            "depth@100ms",
        ),
        reconnect_delay: float = 3.0,
    ) -> None:
        self.symbols = [
            self._normalize_symbol(symbol)
            for symbol in symbols
        ]

        self.on_event = on_event
        self.streams = streams
        self.reconnect_delay = reconnect_delay

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: websocket.WebSocketApp | None = None

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return (
            symbol
            .replace("_", "")
            .replace("/", "")
            .replace("-", "")
            .lower()
        )

    def _build_stream_names(self) -> list[str]:
        streams: list[str] = []

        for symbol in self.symbols:
            for stream in self.streams:
                streams.append(
                    f"{symbol}@{stream}"
                )

        return streams

    def _build_url(self) -> str:
        stream_names = self._build_stream_names()

        streams = "/".join(stream_names)

        return (
            f"{self.BASE_URL}"
            f"/stream?streams={streams}"
        )

    def _handle_message(
        self,
        _ws: websocket.WebSocketApp,
        message: str,
    ) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        if not isinstance(payload, dict):
            return

        data = payload.get(
            "data",
            payload,
        )

        if not isinstance(data, dict):
            return

        event = dict(data)

        event["_stream"] = payload.get(
            "stream"
        )

        event["_received_at_ms"] = (
            time.time_ns() // 1_000_000
        )

        try:
            self.on_event(event)
        except Exception:
            # Callback içindeki hata WebSocket'i
            # durdurmamalıdır.
            return

    def _run(self) -> None:
        while not self._stop_event.is_set():

            try:
                self._ws = websocket.WebSocketApp(
                    self._build_url(),
                    on_message=self._handle_message,
                )

                self._ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                )

            except Exception:
                pass

            if self._stop_event.is_set():
                break

            time.sleep(
                self.reconnect_delay
            )

    def start(self) -> None:
        """
        WebSocket dinleyicisini başlatır.
        """

        if (
            self._thread is not None
            and self._thread.is_alive()
        ):
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="binance-tr-v29-ws",
            daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        """
        WebSocket bağlantısını kapatır.
        """

        self._stop_event.set()

        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

        if (
            self._thread is not None
            and self._thread.is_alive()
        ):
            self._thread.join(
                timeout=2.0
            )


if __name__ == "__main__":
    def print_event(
        event: dict[str, Any],
    ) -> None:
        print(event)


    symbols = [
        "HEMI_TRY",
    ]

    ws = BinanceTRRealtimeWS(
        symbols=symbols,
        on_event=print_event,
    )

    try:
        ws.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        ws.stop()
      
