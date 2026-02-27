from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.database import log_api_usage
from app.core.events import Event, event_bus
from app.core.logging import logger


async def _retry_with_backoff(
    coro_func,
    max_retries: int = 2,
    base_wait_s: float = 2.0,
) -> Any:
    """Retry async function with exponential backoff on rate limit errors."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_func()
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = (
                "429" in str(e) or
                "rate limit" in error_str or
                "too many requests" in error_str
            )
            if not is_rate_limit or attempt == max_retries:
                raise
            last_error = e
            wait_time = base_wait_s * (2 ** attempt)
            logger.warning(
                f"Rate limit error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(wait_time)
    raise last_error if last_error else Exception("Max retries exceeded")


@dataclass
class TradeAction:
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    reasoning: str
    confidence: float  # 0.0 to 1.0
    strategy: str
    order_type: str = "MARKET"
    limit_price: float | None = None


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    provider: str
    latency_ms: float


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        ...


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        client = await self._get_client()
        start = time.monotonic()

        async def _make_request():
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=self.model,
                        contents=user_prompt,
                        config={
                            "system_instruction": system_prompt,
                            "response_mime_type": "application/json",
                        },
                    ),
                ),
                timeout=settings.llm_request_timeout_s,
            )

        try:
            response = await _retry_with_backoff(_make_request, max_retries=2, base_wait_s=2.0)
        except asyncio.TimeoutError:
            logger.warning(f"Gemini API request timed out after {settings.llm_request_timeout_s}s")
            return None

        latency = (time.monotonic() - start) * 1000
        usage = response.usage_metadata
        return LLMResponse(
            content=response.text or "",
            prompt_tokens=usage.prompt_token_count or 0,
            completion_tokens=usage.candidates_token_count or 0,
            model=self.model,
            provider="gemini",
            latency_ms=latency,
        )


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str = "gpt-4o",
        base_url: str | None = None, provider_name: str = "openai"
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.provider_name = provider_name
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        client = await self._get_client()
        start = time.monotonic()

        async def _make_request():
            return await client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

        try:
            response = await asyncio.wait_for(
                _retry_with_backoff(_make_request, max_retries=2, base_wait_s=2.0),
                timeout=settings.llm_request_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(f"OpenAI-compatible API request timed out after {settings.llm_request_timeout_s}s")
            return None

        latency = (time.monotonic() - start) * 1000
        usage = response.usage
        return LLMResponse(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            model=self.model,
            provider=self.provider_name,
            latency_ms=latency,
        )


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022") -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        client = await self._get_client()
        start = time.monotonic()

        async def _make_request():
            return await client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

        try:
            response = await asyncio.wait_for(
                _retry_with_backoff(_make_request, max_retries=2, base_wait_s=2.0),
                timeout=settings.llm_request_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Anthropic API request timed out after {settings.llm_request_timeout_s}s")
            return None

        latency = (time.monotonic() - start) * 1000
        content = response.content[0].text if response.content else ""
        return LLMResponse(
            content=content,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            model=self.model,
            provider="anthropic",
            latency_ms=latency,
        )


TRADE_DECISION_SYSTEM_PROMPT = """You are an autonomous quantitative trading agent.
You receive market signals and must decide whether to trade.

You MUST respond with valid JSON in this exact format:
{
  "action": "buy" | "sell" | "hold",
  "symbol": "<ticker>",
  "quantity": <number>,
  "confidence": <0.0 to 1.0>,
  "order_type": "MARKET" | "LIMIT",
  "limit_price": <price or null>,
  "reasoning": "<brief explanation of your decision>"
}

Rules:
- Only trade when you have high confidence (>0.7)
- Consider the signal type and strength
- Factor in current positions to avoid overexposure
- "hold" means do nothing
- quantity should be in number of shares (stocks) or USD amount (prediction markets)
- You can specify "order_type": "MARKET" or "LIMIT"
- For LIMIT orders, include "limit_price": <price>
"""

POLYMARKET_SYSTEM_PROMPT = """You are an autonomous prediction market trading agent.
You receive signals about prediction markets and must decide whether to trade.

You MUST respond with valid JSON in this exact format:
{
  "action": "buy" | "sell" | "hold",
  "symbol": "<condition_id>",
  "quantity": <number in USD>,
  "confidence": <0.0 to 1.0>,
  "order_type": "LIMIT",
  "limit_price": <price between 0.01 and 0.99>,
  "reasoning": "<brief explanation of your decision>"
}

Rules:
- Only trade when you have high confidence (>0.7)
- Prices represent probabilities (0.01 = 1% likely, 0.99 = 99% likely)
- Buy YES tokens if you think the event is MORE likely than the current price suggests
- Sell (buy NO) if you think the event is LESS likely than the current price suggests
- quantity is in USD amount
- Always use LIMIT orders with a price between 0.01 and 0.99
- "hold" means do nothing
- Consider your current positions to avoid overexposure
"""


class LLMBrain:
    """
    LLM-powered trade decision engine.

    Receives signals from the signal engine, constructs prompts with
    market context, and asks the configured LLM for trade decisions.
    """

    def __init__(self) -> None:
        self._provider: LLMProvider | None = None
        self._provider_name: str = ""
        self._model_name: str = ""
        self._last_call_time: float = 0
        self._min_call_interval_s: float = settings.llm_min_call_interval_s
        self._positions: dict[str, float] = {}
        self._daily_pnl: float = 0.0
        self._total_exposure: float = 0.0
        self._call_semaphore = asyncio.Semaphore(1)
        self._last_call_success: bool = False
        self._last_call_error: str | None = None

    def configure(self, provider: str, model: str, api_key: str, base_url: str | None = None) -> None:
        self._provider_name = provider
        self._model_name = model
        if provider == "gemini":
            self._provider = GeminiProvider(api_key=api_key, model=model)
        elif provider in ("openai", "local"):
            self._provider = OpenAICompatibleProvider(
                api_key=api_key, model=model, base_url=base_url,
                provider_name=provider,
            )
        elif provider == "anthropic":
            self._provider = AnthropicProvider(api_key=api_key, model=model)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def set_portfolio_context(self, positions: dict[str, float], daily_pnl: float, total_exposure: float) -> None:
        self._positions = positions
        self._daily_pnl = daily_pnl
        self._total_exposure = total_exposure

    async def health_check(self) -> dict:
        """Return health status of the LLM brain."""
        if self._provider is None:
            return {
                "status": "not_configured",
                "configured": False,
            }

        return {
            "status": "ok" if self._last_call_success else "degraded",
            "configured": True,
            "provider": self._provider_name,
            "model": self._model_name,
            "last_call_success": self._last_call_success,
            "last_call_error": self._last_call_error,
        }

    async def decide(self, signal_event: Event) -> TradeAction | None:
        if self._provider is None:
            logger.warning("LLM Brain: No provider configured, skipping")
            return None

        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._min_call_interval_s:
            logger.debug(f"LLM call skipped (cooldown): {elapsed:.1f}s < {self._min_call_interval_s}s interval")
            return None

        data = signal_event.data
        portfolio_context = ""
        if self._positions:
            positions_str = ", ".join(f"{sym}: ${exp:.0f}" for sym, exp in self._positions.items())
            portfolio_context = (
                f"\nCurrent Portfolio:\n"
                f"  Positions: {positions_str}\n"
                f"  Total Exposure: ${self._total_exposure:.0f}\n"
                f"  Daily P&L: ${self._daily_pnl:.2f}\n"
            )

        user_prompt = (
            f"Market signal detected:\n"
            f"  Symbol: {data['symbol']}\n"
            f"  Signal: {data['signal_type']}\n"
            f"  Value: {data['value']}\n"
            f"  Current Price: {data.get('price', 'N/A')}\n"
            f"  Details: {json.dumps(data.get('metadata', {}))}\n"
            f"{portfolio_context}\n"
            f"Based on this signal, what is your trade decision?"
        )

        try:
            # Use Polymarket prompt if the signal looks like a prediction market
            is_prediction_market = any(
                term in data.get("signal_type", "").lower()
                for term in ("polymarket", "prediction")
            ) or len(data.get("symbol", "")) > 20  # condition IDs are long hex strings

            system_prompt = POLYMARKET_SYSTEM_PROMPT if is_prediction_market else TRADE_DECISION_SYSTEM_PROMPT
            async with self._call_semaphore:
                self._last_call_time = time.monotonic()
                response = await self._provider.complete(
                    system_prompt, user_prompt
                )

            if response is None:
                self._last_call_success = False
                self._last_call_error = "LLM request timed out"
                return None

            await log_api_usage(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                cost_usd=self._estimate_cost(response),
                request_type="trade_decision",
            )

            try:
                decision = json.loads(response.content)
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"LLM returned invalid JSON: {e}. Raw: {response.content[:200]}")
                self._last_call_success = False
                self._last_call_error = f"Invalid JSON response: {str(e)}"
                return None

            await event_bus.publish(Event(
                type="llm_decision",
                data={"decision": decision, "signal": data,
                      "latency_ms": response.latency_ms}
            ))

            if decision.get("action") == "hold":
                return None

            quantity = float(decision.get("quantity", 0))
            confidence = float(decision.get("confidence", 0))
            symbol = decision.get("symbol", "").strip()

            if quantity <= 0:
                logger.warning(f"LLM output validation failed: quantity must be > 0, got {quantity}")
                self._last_call_success = False
                self._last_call_error = f"Invalid quantity: {quantity}"
                return None

            if not (0 <= confidence <= 1):
                logger.warning(f"LLM output validation failed: confidence must be between 0 and 1, got {confidence}")
                self._last_call_success = False
                self._last_call_error = f"Invalid confidence: {confidence}"
                return None

            if not symbol:
                logger.warning("LLM output validation failed: symbol must be non-empty")
                self._last_call_success = False
                self._last_call_error = "Invalid symbol: empty"
                return None

            self._last_call_success = True
            self._last_call_error = None

            return TradeAction(
                symbol=symbol,
                side=decision["action"],
                quantity=quantity,
                reasoning=decision.get("reasoning", ""),
                confidence=confidence,
                strategy="llm_signal_response",
                order_type=decision.get("order_type", "MARKET"),
                limit_price=decision.get("limit_price"),
            )
        except Exception as e:
            logger.error(f"LLM Brain error: {e}")
            self._last_call_success = False
            self._last_call_error = str(e)
            return None

    def _estimate_cost(self, response: LLMResponse) -> float:
        rates = {
            "gemini": {"prompt": 0.075 / 1_000_000, "completion": 0.30 / 1_000_000},
            "openai": {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
            "anthropic": {"prompt": 3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
            "local": {"prompt": 0.0, "completion": 0.0},
        }
        rate = rates.get(response.provider, rates["openai"])
        return (
            response.prompt_tokens * rate["prompt"]
            + response.completion_tokens * rate["completion"]
        )
