from __future__ import annotations

from typing import Any

from app.engines.execution_engine import BrokerAdapter, OrderResult
from app.core.logging import logger


class IBKRAdapter(BrokerAdapter):
    """Interactive Brokers adapter using TWS/Gateway API.

    Requires ib_insync library and a running TWS or IB Gateway instance.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._connected = False
        logger.info(f"IBKRAdapter initialized (host={host}, port={port}, client_id={client_id})")

    async def connect(self) -> bool:
        try:
            from ib_insync import IB
            self._ib = IB()
            await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)
            self._connected = True
            logger.info("Connected to Interactive Brokers TWS/Gateway")
            return True
        except ImportError:
            raise ImportError(
                "ib_insync is required for IBKR integration. Install with: pip install ib_insync"
            )
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {e}")
            self._connected = False
            return False

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        if not self._connected:
            return OrderResult(success=False, error="Not connected to IBKR. Call connect() first.")
        return OrderResult(success=False, error="IBKR order placement not yet implemented")

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        if not self._connected:
            return {}
        return {}

    async def get_balance(self) -> dict[str, float]:
        if not self._connected:
            return {}
        return {}

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        return []

    async def cancel_order(self, order_id: str) -> bool:
        if not self._connected:
            return False
        return False
