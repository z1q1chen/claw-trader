export interface HealthResponse {
  status: string;
  app: string;
  engines: {
    signal_engine: boolean;
    llm_configured: boolean;
    kill_switch: boolean;
    brokers_registered: string[];
  };
}

export interface LLMConfig {
  provider: string;
  model_name: string;
  api_key: string;
  base_url?: string;
}

export interface UsageSummary {
  provider: string;
  model: string;
  request_count: number;
  total_tokens: number;
  total_cost: number;
  avg_latency_ms: number;
  requests_24h: number;
  cost_24h: number;
}

export interface TradeDecision {
  id: number;
  strategy: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  reasoning: string;
  confidence: number;
  risk_check_passed: boolean;
  risk_rejection_reason: string | null;
  executed: boolean;
  execution_id: string | null;
  created_at: string;
}

export interface Order {
  id: number;
  broker: string;
  broker_order_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  limit_price: number | null;
  quantity: number;
  filled_price: number | null;
  filled_quantity: number | null;
  status: string;
  decision_id: number | null;
  created_at: string;
}

export interface Position {
  id: number;
  broker: string;
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  updated_at: string;
}

export interface RiskSnapshot {
  total_exposure_usd: number;
  daily_pnl_usd: number;
  max_drawdown_pct: number;
  var_95_usd: number;
  positions_count: number;
  kill_switch_active: boolean;
}

export interface RiskConfig {
  max_position_usd: number;
  max_daily_loss_usd: number;
  max_portfolio_exposure_usd: number;
  max_single_trade_usd: number;
  max_drawdown_pct: number;
  max_position_concentration_pct: number;
}

export interface Signal {
  id: number;
  symbol: string;
  signal_type: string;
  value: number;
  metadata: string;
  created_at: string;
}

export interface BrokersResponse {
  brokers: string[];
  default: string | null;
}

export interface PolymarketMarket {
  id: string;
  question: string;
  description?: string;
  outcomes?: string[];
  outcomePrices?: string;
  volume24hr?: number;
  liquidity?: number;
  endDate?: string;
  active?: boolean;
  conditionId?: string;
}

export interface PerformanceSummary {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  profit_factor: number;
  sharpe_ratio: number;
}

export interface SignalConfig {
  rsi_period: number;
  rsi_oversold: number;
  rsi_overbought: number;
  macd_fast: number;
  macd_slow: number;
  macd_signal: number;
  volume_spike_ratio: number;
  bb_period: number;
  bb_std_dev: number;
}

export interface PositionSizingConfig {
  method: 'fixed' | 'fixed_fractional' | 'kelly';
  fixed_quantity: number;
  portfolio_fraction: number;
  kelly_win_rate: number;
  kelly_avg_win: number;
  kelly_avg_loss: number;
  max_position_pct: number;
}
