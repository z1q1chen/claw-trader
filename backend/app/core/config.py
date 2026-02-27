from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    app_name: str = "Claw Trader"
    debug: bool = False
    log_format: str = "text"  # "text" or "json"

    # CORS
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])

    # Database
    database_url: str = "sqlite+aiosqlite:///claw_trader.db"

    # LLM defaults (overridden via dashboard)
    default_llm_provider: str = "gemini"
    gemini_api_key: str = ""
    openai_api_key: str = ""

    # IBKR
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497  # 7497=paper, 7496=live
    ibkr_client_id: int = 1

    # Polymarket
    polymarket_private_key: str = ""
    polymarket_api_key: str = ""
    polygon_rpc_url: str = ""
    polymarket_condition_ids: list[str] = Field(default_factory=list)

    # LLM
    anthropic_api_key: str = ""

    # Risk limits
    max_position_usd: float = 10000.0
    max_daily_loss_usd: float = 5000.0
    max_portfolio_exposure_usd: float = 50000.0
    max_single_trade_usd: float = 2000.0
    max_drawdown_pct: float = 10.0
    max_position_concentration_pct: float = 20.0

    # Signal engine
    signal_scan_interval_ms: int = 500
    signal_cooldown_s: float = 60.0
    price_feed_symbols: list[str] = Field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "AMZN", "SPY"])

    # LLM
    llm_min_call_interval_s: float = 2.0

    # Portfolio sync
    portfolio_sync_interval_s: float = 30.0

    # Rate limiting
    rate_limit_rpm: int = 120  # requests per minute per IP

    # Dry-run mode
    dry_run_mode: bool = True  # Default to dry-run for safety

    model_config = {"env_file": ".env", "env_prefix": "CT_"}


settings = Settings()
