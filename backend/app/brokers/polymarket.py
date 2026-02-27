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
        self._api_key = settings.polymarket_api_key
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

    async def get_market_prices(self, condition_id: str) -> dict[str, float]:
        """Get current YES/NO prices for a market."""
        try:
            market = await self.get_market(condition_id)
            tokens = market.get("tokens", [])

            if len(tokens) < 2:
                return {}

            yes_price = float(tokens[0].get("price", 0.5))
            no_price = float(tokens[1].get("price", 0.5))

            return {
                "yes_price": yes_price,
                "no_price": no_price,
            }
        except Exception:
            return {}

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        order_type: str = "MARKET", limit_price: float | None = None,
    ) -> OrderResult:
        """
        Place an order on Polymarket via CLOB API.

        symbol: condition_id for the market
        side: "buy" or "sell"
        quantity: amount to trade
        limit_price: price for limit orders
        """
        if not self._api_key:
            return OrderResult(
                success=False,
                error="Missing polymarket_api_key in configuration",
            )

        try:
            market = await self.get_market(symbol)
            if not market:
                return OrderResult(
                    success=False,
                    error=f"Market not found: {symbol}",
                )

            tokens = market.get("tokens", [])
            if not tokens or len(tokens) < 2:
                return OrderResult(
                    success=False,
                    error=f"Invalid market structure for {symbol}",
                )

            if side.lower() == "buy":
                token_id = tokens[0].get("token_id")
                clob_side = "BUY"
            elif side.lower() == "sell":
                token_id = tokens[1].get("token_id")
                clob_side = "SELL"
            else:
                return OrderResult(
                    success=False,
                    error=f"Invalid side: {side}. Use 'buy' or 'sell'",
                )

            price = limit_price if limit_price is not None else 0.5

            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            order_body = {
                "tokenID": token_id,
                "price": price,
                "size": quantity,
                "side": clob_side,
            }

            response = await self._http.post(
                f"{CLOB_API_BASE}/order",
                json=order_body,
                headers=headers,
            )

            if response.status_code == 201:
                result = response.json()
                return OrderResult(
                    success=True,
                    broker_order_id=result.get("id"),
                    filled_price=price,
                    filled_quantity=quantity,
                )
            elif response.status_code in (400, 401, 403):
                error_text = response.text[:200]
                return OrderResult(
                    success=False,
                    error=f"CLOB API error ({response.status_code}): {error_text}",
                )
            else:
                return OrderResult(
                    success=False,
                    error=f"CLOB API returned {response.status_code}",
                )

        except Exception as e:
            return OrderResult(
                success=False,
                error=f"Order placement failed: {str(e)}",
            )

    async def get_positions(self) -> dict[str, dict[str, Any]]:
        """Query CLOB API for user positions."""
        if not self._api_key:
            return {}

        try:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            response = await self._http.get(
                f"{CLOB_API_BASE}/positions",
                headers=headers,
            )

            if response.status_code == 200:
                positions_data = response.json()
                if isinstance(positions_data, list):
                    positions = {}
                    for pos in positions_data:
                        condition_id = pos.get("condition_id")
                        if condition_id:
                            positions[condition_id] = {
                                "token_id": pos.get("token_id"),
                                "quantity": float(pos.get("quantity", 0)),
                                "avg_cost": float(pos.get("avg_price", 0)),
                                "market_value": float(pos.get("market_value", 0)),
                            }
                    return positions
                else:
                    return positions_data if isinstance(positions_data, dict) else {}
            else:
                return {}

        except Exception as e:
            return {}

    async def get_balance(self) -> dict[str, float]:
        """Get wallet balance including POL and USDC.e on Polygon."""
        w3 = await self._get_web3()
        if w3 is None or not self._private_key:
            return {}

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            account = w3.eth.account.from_key(self._private_key)
            address = account.address

            pol_balance = await loop.run_in_executor(
                None, lambda: w3.eth.get_balance(address)
            )
            pol_balance_float = float(w3.from_wei(pol_balance, "ether"))

            usdc_balance = await self._get_usdc_balance(w3, address)

            return {
                "address": address,
                "POL": pol_balance_float,
                "USDC.e": usdc_balance,
            }
        except Exception:
            return {}

    async def _get_usdc_balance(self, w3, address: str) -> float:
        """Query USDC.e (0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174) balance."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            erc20_abi = [
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function",
                }
            ]

            contract = w3.eth.contract(address=w3.to_checksum_address(USDC_E), abi=erc20_abi)
            balance = await loop.run_in_executor(
                None,
                lambda: contract.functions.balanceOf(w3.to_checksum_address(address)).call()
            )

            return float(balance) / (10 ** 6)
        except Exception:
            return 0.0

    async def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._api_key:
            return []
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            response = await self._http.get(
                f"{CLOB_API_BASE}/orders",
                headers=headers,
                params={"limit": limit},
            )
            if response.status_code == 200:
                return response.json() if isinstance(response.json(), list) else []
            return []
        except Exception:
            return []

    async def cancel_order(self, order_id: str) -> bool:
        if not self._api_key:
            return False
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            response = await self._http.delete(
                f"{CLOB_API_BASE}/order/{order_id}",
                headers=headers,
            )
            return response.status_code in (200, 204)
        except Exception:
            return False
