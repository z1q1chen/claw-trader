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


@dataclass
class TradeAction:
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    reasoning: str
    confidence: float  # 0.0 to 1.0
    strategy: str


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

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config={
                    "system_instruction": system_prompt,
                    "response_mime_type": "application/json",
                },
            ),
        )

        latency = (time.monotonic() - start) * 1000
        usage = response.usage_metadata
        return LLMResponse(
            content=response.text,
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

        response = await client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

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
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
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

        response = await client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

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
  "reasoning": "<brief explanation of your decision>"
}

Rules:
- Only trade when you have high confidence (>0.7)
- Consider the signal type and strength
- Factor in current positions to avoid overexposure
- "hold" means do nothing
- quantity should be in number of shares (stocks) or USD amount (prediction markets)
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
        self._min_call_interval_s: float = 2.0

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

    async def decide(self, signal_event: Event) -> TradeAction | None:
        if self._provider is None:
            logger.warning("LLM Brain: No provider configured, skipping")
            return None

        now = time.monotonic()
        if now - self._last_call_time < self._min_call_interval_s:
            return None
        self._last_call_time = now

        data = signal_event.data
        user_prompt = (
            f"Market signal detected:\n"
            f"  Symbol: {data['symbol']}\n"
            f"  Signal: {data['signal_type']}\n"
            f"  Value: {data['value']}\n"
            f"  Current Price: {data.get('price', 'N/A')}\n"
            f"  Details: {json.dumps(data.get('metadata', {}))}\n\n"
            f"Based on this signal, what is your trade decision?"
        )

        try:
            response = await self._provider.complete(
                TRADE_DECISION_SYSTEM_PROMPT, user_prompt
            )

            await log_api_usage(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                cost_usd=self._estimate_cost(response),
                request_type="trade_decision",
            )

            decision = json.loads(response.content)

            await event_bus.publish(Event(
                type="llm_decision",
                data={"decision": decision, "signal": data,
                      "latency_ms": response.latency_ms}
            ))

            if decision.get("action") == "hold":
                return None

            return TradeAction(
                symbol=decision["symbol"],
                side=decision["action"],
                quantity=float(decision.get("quantity", 0)),
                reasoning=decision.get("reasoning", ""),
                confidence=float(decision.get("confidence", 0)),
                strategy="llm_signal_response",
            )
        except Exception as e:
            logger.error(f"LLM Brain error: {e}")
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
