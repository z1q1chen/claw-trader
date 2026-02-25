from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import settings
from app.engines.execution_engine import BrokerAdapter, OrderResult


GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Polygon contract addresses
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"


class PolymarketAdapter(BrokerAdapter):
    """
    Polymarket prediction market adapter.

    Uses the Gamma API for market data and the CLOB API for order execution.
    On-chain execution via Polygon's Conditional Token Framework.
    """

    def __init__(self) -> None:
        self._private_key = settings.polymarket_private_key
        self._rpc_url = settings.polygon_rpc_url
        self._http = httpx.AsyncClient(timeout=30.0)
        self._web3 = None

    async def _get_web3(self):
        if self._web3 is None and self._rpc_url:
            from web3 import Web3
            self._web3 = Web3(Web3.HTTPProvider(self._rpc_url))
        return self._web3

    async def get_trending_markets(self, limit: int = 10) -> list[dict]:
        resp = await self._http.get(
            f"{GAMMA_API_BASE}/markets",
            params={"limit": limit, "order": "volume24hr", "ascending": "false", "active": "true"},
        )
        resp.raise_for_status()
        return resp.json()

    async def search_markets(self, query: str, limit: int = 10) -> list[dict]:
        resp = await self._http.get(
            f"{GAMMA_API_BASE}/markets",
            params={"limit": limit, "tag": query, "active": "true"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> dict:
        resp = await self._http.get(f"{GAMMA_API_BASE}/markets/{condition_id}")
        resp.raise_for_status()
        return resp.json()

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        # symbol = condition_id for Polymarket
        # side = "buy_yes" or "buy_no"
        # quantity = USD amount
        # TODO: Implement CTF split + CLOB sell mechanism
        return OrderResult(
            success=False,
            error="Polymarket execution not yet implemented. Use PolyClaw or manual trading.",
        )

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        # TODO: Query on-chain CTF token balances for the wallet
        return {}

    async def get_balance(self) -> dict[str, float]:
        w3 = await self._get_web3()
        if w3 is None or not self._private_key:
            return {}

        try:
            account = w3.eth.account.from_key(self._private_key)
            pol_balance = w3.eth.get_balance(account.address)
            # TODO: Query USDC.e balance via ERC20 contract
            return {
                "address": account.address,
                "POL": float(w3.from_wei(pol_balance, "ether")),
                "USDC.e": 0.0,  # TODO
            }
        except Exception as e:
            return {"error": str(e)}

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        # TODO: Query CLOB API for order history
        return []

    async def cancel_order(self, order_id: str) -> bool:
        # TODO: Cancel via CLOB API
        return False
