"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api, createWebSocket } from "@/lib/api";
import type {
  LLMConfig,
  UsageSummary,
  TradeDecision,
  Order,
  Position,
  RiskSnapshot,
  RiskConfig,
  Signal,
  BrokersResponse,
  PolymarketMarket,
  PerformanceSummary,
  SignalConfig,
  PositionSizingConfig,
  WebhookConfig,
  JournalEntry,
} from "@/lib/types";

interface EventLogEntry {
  time: string;
  type: string;
  data: string;
}

export default function Dashboard() {
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('claw-trader-theme');
      if (saved) return saved === 'dark';
      return window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    return false;
  });
  const [health, setHealth] = useState<string>("connecting...");
  const [llmConfig, setLlmConfig] = useState<LLMConfig>({
    provider: "gemini",
    model_name: "gemini-2.0-flash",
    api_key: "",
    base_url: "",
  });
  const [usageSummary, setUsageSummary] = useState<UsageSummary[]>([]);
  const [decisions, setDecisions] = useState<TradeDecision[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [riskSnapshot, setRiskSnapshot] = useState<RiskSnapshot>({
    total_exposure_usd: 0,
    daily_pnl_usd: 0,
    max_drawdown_pct: 0,
    var_95_usd: 0,
    positions_count: 0,
    kill_switch_active: false,
  });
  const [riskConfig, setRiskConfig] = useState<RiskConfig>({
    max_position_usd: 0,
    max_daily_loss_usd: 0,
    max_portfolio_exposure_usd: 0,
    max_single_trade_usd: 0,
    max_drawdown_pct: 0,
    max_position_concentration_pct: 20,
  });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [eventLog, setEventLog] = useState<EventLogEntry[]>([]);
  const [killSwitch, setKillSwitch] = useState(false);
  const [brokers, setBrokers] = useState<BrokersResponse>({ brokers: [], default: null });
  const [connectingBroker, setConnectingBroker] = useState(false);
  const [brokerStatus, setBrokerStatus] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<string | null>(null);
  const [riskStatus, setRiskStatus] = useState<string | null>(null);
  const [marketSearch, setMarketSearch] = useState("");
  const [markets, setMarkets] = useState<PolymarketMarket[]>([]);
  const [marketsLoading, setMarketsLoading] = useState(false);
  const [tradeForm, setTradeForm] = useState({
    symbol: "",
    side: "buy" as "buy" | "sell",
    quantity: 0,
    price: 0,
    broker: undefined as string | undefined,
  });
  const [tradeStatus, setTradeStatus] = useState<string | null>(null);
  const [stats, setStats] = useState<Record<string, any>>({});
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<{ close: () => void } | null>(null);
  const [performanceSummary, setPerformanceSummary] = useState<PerformanceSummary>({
    total_trades: 0,
    winning_trades: 0,
    losing_trades: 0,
    win_rate: 0,
    total_pnl: 0,
    profit_factor: 0,
    sharpe_ratio: 0,
  });
  const [dryRunMode, setDryRunMode] = useState(false);
  const [signalConfig, setSignalConfig] = useState<SignalConfig>({
    rsi_period: 14,
    rsi_oversold: 30,
    rsi_overbought: 70,
    macd_fast: 12,
    macd_slow: 26,
    macd_signal: 9,
    volume_spike_ratio: 2.0,
    bb_period: 20,
    bb_std_dev: 2.0,
  });
  const [signalConfigStatus, setSignalConfigStatus] = useState<string | null>(null);
  const [orderPage, setOrderPage] = useState(0);
  const [orderTotal, setOrderTotal] = useState(0);
  const ORDER_PAGE_SIZE = 20;
  const [isLoading, setIsLoading] = useState(true);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [positionSizingConfig, setPositionSizingConfig] = useState<PositionSizingConfig>({
    method: 'fixed_fractional',
    fixed_quantity: 10,
    portfolio_fraction: 0.02,
    kelly_win_rate: 0.55,
    kelly_avg_win: 1.5,
    kelly_avg_loss: 1.0,
    max_position_pct: 0.10,
  });
  const [positionSizingStatus, setPositionSizingStatus] = useState<string | null>(null);
  const [webhooks, setWebhooks] = useState<WebhookConfig[]>([]);
  const [newWebhookUrl, setNewWebhookUrl] = useState('');
  const [newWebhookEvents, setNewWebhookEvents] = useState<string[]>(['*']);
  const [journalEntries, setJournalEntries] = useState<JournalEntry[]>([]);
  const [journalPage, setJournalPage] = useState(0);
  const [journalTotal, setJournalTotal] = useState(0);
  const JOURNAL_PAGE_SIZE = 50;
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [showApiKeySettings, setShowApiKeySettings] = useState(false);
  const [hasApiKey, setHasApiKey] = useState(false);

  const refreshData = useCallback(async () => {
    try {
      const [h, cfg, usage, dec, ord, pos, risk, rc, sig, brok, st, perf, dryRun, sigCfg, posSizingCfg, webhooks, journal] = await Promise.all([
        api.getHealth().catch(() => ({ status: "error" })),
        api.getLLMConfig().catch(() => null),
        api.getUsageSummary().catch(() => []),
        api.getDecisions(20).catch(() => []),
        api.getOrders(ORDER_PAGE_SIZE, orderPage * ORDER_PAGE_SIZE).catch(() => ({ data: [], total: 0, limit: ORDER_PAGE_SIZE, offset: 0, has_more: false })),
        api.getPositions().catch(() => []),
        api.getRiskSnapshot().catch(() => ({})),
        api.getRiskConfig().catch(() => ({})),
        api.getSignals(50).catch(() => []),
        api.listBrokers().catch(() => ({ brokers: [], default: null })),
        api.getStats().catch(() => ({})),
        api.getPerformanceSummary().catch(() => null),
        api.getDryRunStatus().catch(() => ({ enabled: false })),
        api.getSignalConfig().catch(() => null),
        api.getPositionSizingConfig().catch(() => null),
        api.getWebhooks().catch(() => []),
        api.getTradeJournal(JOURNAL_PAGE_SIZE, journalPage * JOURNAL_PAGE_SIZE).catch(() => ({ data: [], total: 0, limit: JOURNAL_PAGE_SIZE, offset: 0, has_more: false })),
      ]);

      setHealth(h.status === "ok" ? "connected" : "error");
      if (cfg) setLlmConfig((prev) => ({ ...prev, ...cfg }));
      setUsageSummary(usage);
      setDecisions(dec);
      setOrders(ord.data || ord);
      setOrderTotal(ord.total || 0);
      setPositions(pos);
      setRiskSnapshot(risk);
      setRiskConfig(rc);
      setSignals(sig);
      setBrokers(brok);
      setStats(st);
      if (perf) setPerformanceSummary(perf);
      setDryRunMode(dryRun.enabled);
      if (sigCfg) setSignalConfig(sigCfg);
      if (posSizingCfg) setPositionSizingConfig(posSizingCfg);
      setWebhooks(webhooks);
      setJournalEntries(journal.data || []);
      setJournalTotal(journal.total || 0);
      setKillSwitch(!!risk.kill_switch_active);
    } catch {
      setHealth("error");
    } finally {
      setIsLoading(false);
    }
  }, [orderPage, journalPage]);

  useEffect(() => {
    refreshData();
    const pollInterval = wsConnected ? 30000 : 5000;
    const interval = setInterval(refreshData, pollInterval);
    return () => clearInterval(interval);
  }, [refreshData, wsConnected]);

  useEffect(() => {
    const conn = createWebSocket(
      (event) => {
        setEventLog((prev) => [
          {
            time: new Date().toLocaleTimeString(),
            type: event.type,
            data: JSON.stringify(event.data).slice(0, 120),
          },
          ...prev.slice(0, 99),
        ]);

        if (event.type === "order_executed" || event.type === "trade_rejected") {
          refreshData();
        }
      },
      (connected) => setWsConnected(connected)
    );
    wsRef.current = conn;
    return () => conn.close();
  }, [refreshData]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light');
    localStorage.setItem('claw-trader-theme', darkMode ? 'dark' : 'light');
  }, [darkMode]);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const savedKey = localStorage.getItem('claw-trader-api-key');
      setHasApiKey(!!savedKey);
    }
  }, []);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const saveLLMConfig = async () => {
    setLlmStatus(null);
    try {
      // If the key looks masked (contains •), user didn't change it - keep the existing key on the server
      const apiKey = llmConfig.api_key.includes("•") ? "" : llmConfig.api_key;
      if (!apiKey && !llmConfig.api_key.includes("•")) {
        setLlmStatus("Error: API key is required");
        return;
      }
      const payload: Record<string, string> = {
        provider: llmConfig.provider,
        model_name: llmConfig.model_name,
      };
      if (apiKey) {
        payload.api_key = apiKey;
      }
      if (llmConfig.base_url) {
        payload.base_url = llmConfig.base_url;
      }
      await api.updateLLMConfig(payload as any);
      setLlmStatus("Configuration saved");
      refreshData();
    } catch (e: any) {
      setLlmStatus(`Error: ${e.message}`);
    }
  };

  const handleKillSwitch = async () => {
    const newState = !killSwitch;
    await api.toggleKillSwitch(newState);
    setKillSwitch(newState);
  };

  const saveRiskConfig = async () => {
    setRiskStatus(null);
    try {
      await api.updateRiskConfig(riskConfig);
      setRiskStatus("Configuration saved");
      refreshData();
    } catch (e: any) {
      setRiskStatus(`Error: ${e.message}`);
    }
  };

  const saveSignalConfig = async () => {
    setSignalConfigStatus(null);
    try {
      await api.updateSignalConfig(signalConfig);
      setSignalConfigStatus("Configuration saved");
      refreshData();
    } catch (e: any) {
      setSignalConfigStatus(`Error: ${e.message}`);
    }
  };

  const savePositionSizingConfig = async () => {
    setPositionSizingStatus(null);
    try {
      await api.updatePositionSizingConfig(positionSizingConfig);
      setToast({ message: "Position sizing configuration saved", type: "success" });
      refreshData();
    } catch (e: any) {
      setToast({ message: `Error: ${e.message}`, type: "error" });
    }
  };

  const resetSignalConfig = () => {
    setSignalConfig({
      rsi_period: 14,
      rsi_oversold: 30,
      rsi_overbought: 70,
      macd_fast: 12,
      macd_slow: 26,
      macd_signal: 9,
      volume_spike_ratio: 2.0,
      bb_period: 20,
      bb_std_dev: 2.0,
    });
  };

  const connectBroker = async (broker: string) => {
    setConnectingBroker(true);
    setBrokerStatus(null);
    try {
      await api.connectBroker(broker);
      setBrokerStatus(`${broker} connected successfully`);
      refreshData();
    } catch (e: any) {
      setBrokerStatus(`Failed to connect ${broker}: ${e.message}`);
    } finally {
      setConnectingBroker(false);
    }
  };

  const disconnectBroker = async (broker: string) => {
    await api.disconnectBroker(broker);
    refreshData();
  };

  const submitTrade = async () => {
    setTradeStatus(null);
    try {
      const result = await api.placeTrade({
        symbol: tradeForm.symbol,
        side: tradeForm.side,
        quantity: tradeForm.quantity,
        price: tradeForm.price,
        broker: tradeForm.broker,
      });
      setTradeStatus(`Trade executed! Order: ${result.broker_order_id}`);
      setTradeForm({ symbol: "", side: "buy", quantity: 0, price: 0, broker: undefined });
      refreshData();
    } catch (e: any) {
      setTradeStatus(`Error: ${e.message}`);
    }
  };

  const handleCancelOrder = async (orderId: string, broker: string) => {
    setCancellingOrderId(orderId);
    try {
      await api.cancelOrder(broker, orderId);
      setTradeStatus(`Order ${orderId} cancelled successfully`);
      refreshData();
    } catch (e: any) {
      setTradeStatus(`Error: Failed to cancel order ${orderId}: ${e.message}`);
    } finally {
      setCancellingOrderId(null);
    }
  };

  const handleCreateWebhook = async () => {
    if (!newWebhookUrl.trim()) {
      setToast({ message: "URL is required", type: "error" });
      return;
    }
    try {
      await api.createWebhook(newWebhookUrl, newWebhookEvents);
      setToast({ message: "Webhook created successfully", type: "success" });
      setNewWebhookUrl('');
      setNewWebhookEvents(['*']);
      refreshData();
    } catch (e: any) {
      setToast({ message: `Error: ${e.message}`, type: "error" });
    }
  };

  const handleDeleteWebhook = async (id: string) => {
    try {
      await api.deleteWebhook(id);
      setToast({ message: "Webhook deleted", type: "success" });
      refreshData();
    } catch (e: any) {
      setToast({ message: `Error: ${e.message}`, type: "error" });
    }
  };

  const handleTestWebhook = async (id: string) => {
    try {
      await api.testWebhook(id);
      setToast({ message: "Test webhook sent", type: "success" });
    } catch (e: any) {
      setToast({ message: `Error: ${e.message}`, type: "error" });
    }
  };

  const handleSaveApiKey = () => {
    if (apiKeyInput.trim()) {
      api.setApiKey(apiKeyInput);
      setHasApiKey(true);
      setToast({ message: "API key saved", type: "success" });
      setApiKeyInput("");
      setShowApiKeySettings(false);
    } else {
      setToast({ message: "API key cannot be empty", type: "error" });
    }
  };

  const handleClearApiKey = () => {
    api.clearApiKey();
    setHasApiKey(false);
    setToast({ message: "API key cleared", type: "success" });
    setShowApiKeySettings(false);
  };

  const loadTrendingMarkets = async () => {
    setMarketsLoading(true);
    try {
      const data = await api.getTrendingMarkets(10);
      setMarkets(data);
    } catch {
      setMarkets([]);
    } finally {
      setMarketsLoading(false);
    }
  };

  const searchMarkets = async () => {
    if (!marketSearch.trim()) return;
    setMarketsLoading(true);
    try {
      const data = await api.searchMarkets(marketSearch);
      setMarkets(data);
    } catch {
      setMarkets([]);
    } finally {
      setMarketsLoading(false);
    }
  };

  return (
    <>
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          Loading dashboard...
        </div>
      )}
      {!isLoading && (
        <>
          {dryRunMode && (
            <div style={{
              background: '#f59e0b',
              color: '#000',
              textAlign: 'center',
              padding: '8px',
              fontWeight: 'bold',
              fontSize: '14px',
            }}>
              DRY RUN MODE - Simulated trades only, no real money at risk
            </div>
          )}
          {toast && (
            <div style={{
              position: 'fixed', top: 20, right: 20, zIndex: 1000,
              padding: '12px 20px', borderRadius: 8,
              background: toast.type === 'success' ? '#22c55e' : '#ef4444',
              color: '#fff', fontWeight: 500, boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            }}>
              {toast.message}
            </div>
          )}
          <div className="header">
        <h1>Claw Trader</h1>
        <div className="header-controls" style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span>
            <span
              className={`status-dot ${health === "connected" ? "green" : "red"}`}
            />
            {health}
          </span>
          <span style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
            <span
              className={`status-dot ${wsConnected ? "green" : "yellow"}`}
              style={{ width: 8, height: 8 }}
            />
            {wsConnected ? "Live" : "Polling"}
          </span>
          <button
            onClick={() => setShowApiKeySettings(!showApiKeySettings)}
            style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', color: 'var(--text)', fontSize: 12 }}
            title={hasApiKey ? "API Key configured" : "No API Key"}
          >
            {hasApiKey ? "🔒" : "🔓"} API Key
          </button>
          <button
            onClick={() => setDarkMode(!darkMode)}
            style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', color: 'var(--text)' }}
          >
            {darkMode ? '☀️' : '🌙'}
          </button>
          <button
            className={`kill-switch ${killSwitch ? "active" : ""}`}
            onClick={handleKillSwitch}
          >
            {killSwitch ? "KILL SWITCH ON" : "KILL SWITCH"}
          </button>
        </div>
      </div>

      {showApiKeySettings && (
        <div style={{
          background: 'var(--bg-secondary)',
          padding: '16px',
          borderBottom: '1px solid var(--border)',
          display: 'flex', gap: '12px', alignItems: 'center'
        }}>
          <div style={{ flex: 1 }}>
            <input
              className="input"
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="Enter API key..."
              onKeyDown={(e) => e.key === 'Enter' && handleSaveApiKey()}
            />
          </div>
          <button className="btn" onClick={handleSaveApiKey} style={{ whiteSpace: 'nowrap' }}>
            Save
          </button>
          {hasApiKey && (
            <button className="btn" onClick={handleClearApiKey} style={{ background: '#ef4444', whiteSpace: 'nowrap' }}>
              Clear
            </button>
          )}
          <button
            className="btn"
            onClick={() => setShowApiKeySettings(false)}
            style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text)', whiteSpace: 'nowrap' }}
          >
            Close
          </button>
        </div>
      )}

      <div className="dashboard">
        {/* LLM Configuration */}
        <div className="card">
          <h2>LLM Configuration</h2>
          <div className="form-row">
            <label>Provider</label>
            <select
              className="select"
              value={llmConfig.provider}
              onChange={(e) =>
                setLlmConfig({ ...llmConfig, provider: e.target.value })
              }
            >
              <option value="gemini">Google Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="local">Local (OpenAI-compatible)</option>
            </select>
          </div>
          <div className="form-row">
            <label>Model</label>
            <input
              className="input"
              value={llmConfig.model_name}
              onChange={(e) =>
                setLlmConfig({ ...llmConfig, model_name: e.target.value })
              }
              placeholder="gemini-2.0-flash"
            />
          </div>
          <div className="form-row">
            <label>API Key</label>
            <input
              className="input"
              type="password"
              value={llmConfig.api_key}
              onChange={(e) =>
                setLlmConfig({ ...llmConfig, api_key: e.target.value })
              }
              placeholder="Enter API key..."
            />
          </div>
          {(llmConfig.provider === "local" || llmConfig.provider === "anthropic") && (
            <div className="form-row">
              <label>Base URL</label>
              <input
                className="input"
                value={llmConfig.base_url}
                onChange={(e) =>
                  setLlmConfig({ ...llmConfig, base_url: e.target.value })
                }
                placeholder="http://127.0.0.1:1234/v1"
              />
            </div>
          )}
          <button className="btn" onClick={saveLLMConfig}>
            Save Configuration
          </button>
          <hr style={{ margin: "12px 0", borderColor: "var(--border)" }} />
          <h3 style={{ fontSize: 13, marginBottom: 8, color: "var(--text-muted)" }}>Trading Frequency</h3>
          <div className="form-row">
            <label>LLM Call Interval (s)</label>
            <input
              className="input"
              type="number"
              step="0.5"
              min="0.5"
              defaultValue={2}
              onChange={async (e) => {
                const val = parseFloat(e.target.value);
                if (val >= 0.5) {
                  try {
                    await fetch("/api/config/llm-interval", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ interval_s: val }),
                    });
                  } catch {}
                }
              }}
            />
          </div>
          <div className="form-row">
            <label>Signal Cooldown (s)</label>
            <input
              className="input"
              type="number"
              step="5"
              min="1"
              defaultValue={60}
              onChange={async (e) => {
                const val = parseFloat(e.target.value);
                if (val >= 1) {
                  try {
                    await fetch("/api/config/signal-cooldown", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ cooldown_s: val }),
                    });
                  } catch {}
                }
              }}
            />
          </div>
          {llmStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: llmStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {llmStatus}
            </p>
          )}
        </div>

        {/* API Usage */}
        <div className="card">
          <h2>API Usage</h2>
          {usageSummary.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No API calls yet</p>
          ) : (
            <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Requests</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Avg Latency</th>
                </tr>
              </thead>
              <tbody>
                {usageSummary.map((u, i) => (
                  <tr key={i}>
                    <td>{u.provider}</td>
                    <td>{u.model}</td>
                    <td>{u.request_count}</td>
                    <td>{u.total_tokens?.toLocaleString()}</td>
                    <td>${u.total_cost?.toFixed(4)}</td>
                    <td>{u.avg_latency_ms?.toFixed(0)}ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* Risk Overview */}
        <div className="card">
          <h2>Risk Engine</h2>
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">
                ${riskSnapshot.total_exposure_usd?.toFixed(0) || "0"}
              </span>
              <span className="stat-label">Total Exposure</span>
            </div>
            <div className="stat">
              <span
                className={`stat-value ${
                  (riskSnapshot.daily_pnl_usd || 0) >= 0
                    ? "pnl-positive"
                    : "pnl-negative"
                }`}
              >
                ${riskSnapshot.daily_pnl_usd?.toFixed(2) || "0.00"}
              </span>
              <span className="stat-label">Daily P&L</span>
            </div>
            <div className="stat">
              <span className="stat-value">
                {riskSnapshot.max_drawdown_pct?.toFixed(1) || "0.0"}%
              </span>
              <span className="stat-label">Max Drawdown</span>
            </div>
            <div className="stat">
              <span className="stat-value">
                ${riskSnapshot.var_95_usd?.toFixed(0) || "0"}
              </span>
              <span className="stat-label">VaR 95%</span>
            </div>
          </div>
        </div>

        {/* Trade Stats */}
        <div className="card">
          <h2>Trade Statistics</h2>
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">{stats.total_filled_orders || 0}</span>
              <span className="stat-label">Filled Orders</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.total_decisions || 0}</span>
              <span className="stat-label">Decisions</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.rejected_decisions || 0}</span>
              <span className="stat-label">Rejected</span>
            </div>
            <div className="stat">
              <span className="stat-value">
                ${typeof stats.total_api_cost_usd === "number" ? stats.total_api_cost_usd.toFixed(4) : "0.00"}
              </span>
              <span className="stat-label">API Cost</span>
            </div>
          </div>
          <div className="stat-row" style={{ marginTop: 8 }}>
            <div className="stat">
              <span className={`stat-value ${(stats.total_unrealized_pnl || 0) >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                ${typeof stats.total_unrealized_pnl === "number" ? stats.total_unrealized_pnl.toFixed(2) : "0.00"}
              </span>
              <span className="stat-label">Unrealized P&L</span>
            </div>
            <div className="stat">
              <span className={`stat-value ${(stats.total_realized_pnl || 0) >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                ${typeof stats.total_realized_pnl === "number" ? stats.total_realized_pnl.toFixed(2) : "0.00"}
              </span>
              <span className="stat-label">Realized P&L</span>
            </div>
          </div>
        </div>

        {/* Performance */}
        <div className="card">
          <h2>Performance</h2>
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">{performanceSummary.total_trades}</span>
              <span className="stat-label">Total Trades</span>
            </div>
            <div className="stat">
              <span className="stat-value">{performanceSummary.win_rate.toFixed(1)}%</span>
              <span className="stat-label">Win Rate</span>
            </div>
            <div className="stat">
              <span className={`stat-value ${performanceSummary.total_pnl >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                ${performanceSummary.total_pnl.toFixed(2)}
              </span>
              <span className="stat-label">Total P&L</span>
            </div>
            <div className="stat">
              <span className="stat-value">{performanceSummary.profit_factor.toFixed(2)}</span>
              <span className="stat-label">Profit Factor</span>
            </div>
          </div>
          <div className="stat-row" style={{ marginTop: 8 }}>
            <div className="stat">
              <span className="stat-value">{performanceSummary.winning_trades}</span>
              <span className="stat-label">Winning Trades</span>
            </div>
            <div className="stat">
              <span className="stat-value">{performanceSummary.losing_trades}</span>
              <span className="stat-label">Losing Trades</span>
            </div>
          </div>
          {performanceSummary.total_trades > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ display: "flex", height: 24, borderRadius: 4, overflow: "hidden", background: "var(--bg-secondary)" }}>
                <div
                  style={{
                    flex: performanceSummary.winning_trades,
                    background: "#22c55e",
                    minWidth: performanceSummary.winning_trades > 0 ? 4 : 0,
                  }}
                />
                <div
                  style={{
                    flex: performanceSummary.losing_trades,
                    background: "#ef4444",
                    minWidth: performanceSummary.losing_trades > 0 ? 4 : 0,
                  }}
                />
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, textAlign: "center" }}>
                Win/Loss Distribution
              </div>
            </div>
          )}
        </div>

        {/* Risk Configuration */}
        <div className="card">
          <h2>Risk Limits</h2>
          <div className="form-row">
            <label>Max Position ($)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_position_usd || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_position_usd: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <div className="form-row">
            <label>Max Single Trade ($)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_single_trade_usd || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_single_trade_usd: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <div className="form-row">
            <label>Max Daily Loss ($)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_daily_loss_usd || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_daily_loss_usd: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <div className="form-row">
            <label>Max Portfolio Exposure ($)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_portfolio_exposure_usd || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_portfolio_exposure_usd: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <div className="form-row">
            <label>Max Drawdown (%)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_drawdown_pct || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_drawdown_pct: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <div className="form-row">
            <label>Max Position Concentration (%)</label>
            <input
              className="input"
              type="number"
              value={riskConfig.max_position_concentration_pct || ""}
              onChange={(e) =>
                setRiskConfig({ ...riskConfig, max_position_concentration_pct: parseFloat(e.target.value) || 0 })
              }
            />
          </div>
          <button className="btn" onClick={saveRiskConfig}>
            Update Risk Limits
          </button>
          {riskStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: riskStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {riskStatus}
            </p>
          )}
        </div>

        {/* Signal Detection Config */}
        <div className="card">
          <h2>Signal Detection Config</h2>

          <div className="form-row">
            <label>Strategy Presets</label>
            <select
              className="select"
              onChange={async (e) => {
                if (e.target.value) {
                  try {
                    await api.applyStrategyPreset(e.target.value);
                    setToast({ message: `Applied ${e.target.value} preset`, type: "success" });
                    refreshData();
                  } catch (err: any) {
                    setToast({ message: `Error: ${err.message}`, type: "error" });
                  }
                }
              }}
            >
              <option value="">Load Preset...</option>
              <option value="conservative">Conservative</option>
              <option value="balanced">Balanced</option>
              <option value="aggressive">Aggressive</option>
            </select>
          </div>

          <h3 style={{ fontSize: 13, marginTop: 12, marginBottom: 8, color: "var(--text-muted)" }}>RSI Parameters</h3>
          <div className="form-row">
            <label>RSI Period</label>
            <input
              className="input"
              type="number"
              value={signalConfig.rsi_period || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, rsi_period: parseInt(e.target.value) || 14 })
              }
            />
          </div>
          <div className="form-row">
            <label>RSI Oversold Level</label>
            <input
              className="input"
              type="number"
              value={signalConfig.rsi_oversold || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, rsi_oversold: parseFloat(e.target.value) || 30 })
              }
            />
          </div>
          <div className="form-row">
            <label>RSI Overbought Level</label>
            <input
              className="input"
              type="number"
              value={signalConfig.rsi_overbought || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, rsi_overbought: parseFloat(e.target.value) || 70 })
              }
            />
          </div>

          <h3 style={{ fontSize: 13, marginTop: 12, marginBottom: 8, color: "var(--text-muted)" }}>MACD Parameters</h3>
          <div className="form-row">
            <label>MACD Fast Period</label>
            <input
              className="input"
              type="number"
              value={signalConfig.macd_fast || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, macd_fast: parseInt(e.target.value) || 12 })
              }
            />
          </div>
          <div className="form-row">
            <label>MACD Slow Period</label>
            <input
              className="input"
              type="number"
              value={signalConfig.macd_slow || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, macd_slow: parseInt(e.target.value) || 26 })
              }
            />
          </div>
          <div className="form-row">
            <label>MACD Signal Period</label>
            <input
              className="input"
              type="number"
              value={signalConfig.macd_signal || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, macd_signal: parseInt(e.target.value) || 9 })
              }
            />
          </div>

          <h3 style={{ fontSize: 13, marginTop: 12, marginBottom: 8, color: "var(--text-muted)" }}>Volume Parameters</h3>
          <div className="form-row">
            <label>Volume Spike Ratio</label>
            <input
              className="input"
              type="number"
              step="0.1"
              value={signalConfig.volume_spike_ratio || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, volume_spike_ratio: parseFloat(e.target.value) || 2.0 })
              }
            />
          </div>

          <h3 style={{ fontSize: 13, marginTop: 12, marginBottom: 8, color: "var(--text-muted)" }}>Bollinger Bands Parameters</h3>
          <div className="form-row">
            <label>BB Period</label>
            <input
              className="input"
              type="number"
              value={signalConfig.bb_period || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, bb_period: parseInt(e.target.value) || 20 })
              }
            />
          </div>
          <div className="form-row">
            <label>BB Std Dev</label>
            <input
              className="input"
              type="number"
              step="0.1"
              value={signalConfig.bb_std_dev || ""}
              onChange={(e) =>
                setSignalConfig({ ...signalConfig, bb_std_dev: parseFloat(e.target.value) || 2.0 })
              }
            />
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="btn" onClick={saveSignalConfig}>
              Save Configuration
            </button>
            <button
              className="btn"
              style={{ background: "var(--bg-secondary)" }}
              onClick={resetSignalConfig}
            >
              Reset Defaults
            </button>
          </div>
          {signalConfigStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: signalConfigStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {signalConfigStatus}
            </p>
          )}
        </div>

        {/* Position Sizing Config */}
        <div className="card">
          <h2>Position Sizing</h2>
          <div className="form-row">
            <label>Sizing Method</label>
            <div style={{ display: "flex", gap: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="radio"
                  checked={positionSizingConfig.method === 'fixed'}
                  onChange={() => setPositionSizingConfig({ ...positionSizingConfig, method: 'fixed' })}
                />
                Fixed
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="radio"
                  checked={positionSizingConfig.method === 'fixed_fractional'}
                  onChange={() => setPositionSizingConfig({ ...positionSizingConfig, method: 'fixed_fractional' })}
                />
                Fixed Fractional
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="radio"
                  checked={positionSizingConfig.method === 'kelly'}
                  onChange={() => setPositionSizingConfig({ ...positionSizingConfig, method: 'kelly' })}
                />
                Kelly Criterion
              </label>
            </div>
          </div>

          {positionSizingConfig.method === 'fixed' && (
            <>
              <div className="form-row">
                <label>Fixed Quantity</label>
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={positionSizingConfig.fixed_quantity || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, fixed_quantity: parseFloat(e.target.value) || 0 })
                  }
                />
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                Trade a fixed quantity for every signal.
              </p>
            </>
          )}

          {positionSizingConfig.method === 'fixed_fractional' && (
            <>
              <div className="form-row">
                <label>Portfolio Fraction (%)</label>
                <input
                  className="input"
                  type="number"
                  step="0.01"
                  value={positionSizingConfig.portfolio_fraction * 100 || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, portfolio_fraction: parseFloat(e.target.value) / 100 || 0 })
                  }
                />
              </div>
              <div className="form-row">
                <label>Max Position (% of portfolio)</label>
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={positionSizingConfig.max_position_pct * 100 || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, max_position_pct: parseFloat(e.target.value) / 100 || 0 })
                  }
                />
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                Risk a fixed fraction of portfolio on each trade, capped by max position.
              </p>
            </>
          )}

          {positionSizingConfig.method === 'kelly' && (
            <>
              <div className="form-row">
                <label>Win Rate (0-1)</label>
                <input
                  className="input"
                  type="number"
                  step="0.01"
                  min="0"
                  max="1"
                  value={positionSizingConfig.kelly_win_rate || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, kelly_win_rate: parseFloat(e.target.value) || 0 })
                  }
                />
              </div>
              <div className="form-row">
                <label>Avg Win (ratio)</label>
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={positionSizingConfig.kelly_avg_win || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, kelly_avg_win: parseFloat(e.target.value) || 0 })
                  }
                />
              </div>
              <div className="form-row">
                <label>Avg Loss (ratio)</label>
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={positionSizingConfig.kelly_avg_loss || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, kelly_avg_loss: parseFloat(e.target.value) || 0 })
                  }
                />
              </div>
              <div className="form-row">
                <label>Max Position (% of portfolio)</label>
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={positionSizingConfig.max_position_pct * 100 || ""}
                  onChange={(e) =>
                    setPositionSizingConfig({ ...positionSizingConfig, max_position_pct: parseFloat(e.target.value) / 100 || 0 })
                  }
                />
              </div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                Kelly criterion sizing optimizes for long-term growth based on win rate and payoff ratios.
              </p>
            </>
          )}

          <button className="btn" onClick={savePositionSizingConfig} style={{ marginTop: 12 }}>
            Save Configuration
          </button>
          {positionSizingStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: positionSizingStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {positionSizingStatus}
            </p>
          )}
        </div>

        {/* Webhooks */}
        <div className="card dashboard-full">
          <h2>Webhooks</h2>
          {webhooks.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No webhooks registered</p>
          ) : (
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>Event Types</th>
                    <th>Enabled</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((w) => (
                    <tr key={w.id}>
                      <td style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>{w.url}</td>
                      <td>{w.event_types.join(", ")}</td>
                      <td>{w.enabled ? "Yes" : "No"}</td>
                      <td>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "2px 8px", marginRight: 4 }}
                          onClick={() => handleTestWebhook(w.id)}
                        >
                          Test
                        </button>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "2px 8px", background: "#ef4444" }}
                          onClick={() => handleDeleteWebhook(w.id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <hr style={{ margin: "12px 0", borderColor: "var(--border)" }} />
          <h3 style={{ fontSize: 13, marginBottom: 8, color: "var(--text-muted)" }}>Add New Webhook</h3>
          <div className="form-row">
            <label>Webhook URL</label>
            <input
              className="input"
              value={newWebhookUrl}
              onChange={(e) => setNewWebhookUrl(e.target.value)}
              placeholder="https://example.com/webhook"
            />
          </div>
          <div className="form-row">
            <label>Event Types</label>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {["order_executed", "order_failed", "trade_rejected", "order_cancelled"].map((event) => (
                <label key={event} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={newWebhookEvents.includes(event)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setNewWebhookEvents([...newWebhookEvents.filter(e => e !== '*'), event]);
                      } else {
                        setNewWebhookEvents(newWebhookEvents.filter(e => e !== event));
                        if (newWebhookEvents.filter(e => e !== event).length === 0) {
                          setNewWebhookEvents(['*']);
                        }
                      }
                    }}
                  />
                  {event}
                </label>
              ))}
              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <input
                  type="checkbox"
                  checked={newWebhookEvents.includes('*')}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setNewWebhookEvents(['*']);
                    } else {
                      setNewWebhookEvents([]);
                    }
                  }}
                />
                All events (*)
              </label>
            </div>
          </div>
          <button className="btn" onClick={handleCreateWebhook} style={{ marginTop: 12 }}>
            Add Webhook
          </button>
        </div>

        {/* Trade Journal */}
        <div className="card dashboard-full">
          <h2>Trade Journal</h2>
          {journalEntries.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No journal entries yet</p>
          ) : (
            <>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Event</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Price</th>
                      <th>Status</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journalEntries.map((entry) => (
                      <tr key={entry.id}>
                        <td>{entry.created_at}</td>
                        <td>
                          <span
                            style={{
                              padding: "2px 6px",
                              borderRadius: 3,
                              fontSize: 11,
                              background:
                                entry.event_type === "risk_check"
                                  ? "#fbbf24"
                                  : entry.event_type === "order_executed"
                                  ? "#22c55e"
                                  : entry.event_type === "order_failed"
                                  ? "#ef4444"
                                  : "#999",
                              color: "#fff",
                            }}
                          >
                            {entry.event_type}
                          </span>
                        </td>
                        <td>{entry.symbol || "-"}</td>
                        <td>{entry.side || "-"}</td>
                        <td>{entry.quantity || "-"}</td>
                        <td>{entry.price ? `$${entry.price.toFixed(2)}` : "-"}</td>
                        <td>{entry.status || "-"}</td>
                        <td
                          style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", cursor: "pointer" }}
                          title={entry.details}
                        >
                          {entry.details}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, alignItems: "center" }}>
                <button disabled={journalPage === 0} onClick={() => setJournalPage(p => p - 1)}>← Previous</button>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Page {journalPage + 1} of {Math.ceil(journalTotal / JOURNAL_PAGE_SIZE) || 1}</span>
                <button disabled={journalPage * JOURNAL_PAGE_SIZE + JOURNAL_PAGE_SIZE >= journalTotal} onClick={() => setJournalPage(p => p + 1)}>Next →</button>
              </div>
            </>
          )}
        </div>

        {/* Signals */}
        <div className="card dashboard-full">
          <h2>Recent Signals</h2>
          {signals.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <button
                className="btn"
                onClick={() => window.open(`/api/export/signals?format=csv`, '_blank')}
                style={{ fontSize: 12, padding: "4px 12px" }}
              >
                Export CSV
              </button>
            </div>
          )}
          {signals.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No signals yet</p>
          ) : (
            <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Signal Type</th>
                  <th>Value</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s, i) => (
                  <tr key={i}>
                    <td>{s.symbol}</td>
                    <td>{s.signal_type}</td>
                    <td>{typeof s.value === "number" ? s.value.toFixed(2) : s.value}</td>
                    <td>{s.created_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* Broker Connections */}
        <div className="card">
          <h2>Brokers</h2>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <button
              className="btn"
              onClick={() => connectBroker("ibkr")}
              disabled={connectingBroker || brokers.brokers.includes("ibkr")}
            >
              {brokers.brokers.includes("ibkr") ? "IBKR Connected" : "Connect IBKR"}
            </button>
            <button
              className="btn"
              onClick={() => connectBroker("polymarket")}
              disabled={connectingBroker || brokers.brokers.includes("polymarket")}
            >
              {brokers.brokers.includes("polymarket") ? "Polymarket Connected" : "Connect Polymarket"}
            </button>
          </div>
          {brokers.brokers.length > 0 && (
            <div>
              <p style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Connected: {brokers.brokers.join(", ")} (default: {brokers.default})
              </p>
              {brokers.brokers.map((b) => (
                <button
                  key={b}
                  className="btn"
                  style={{ marginRight: 8, marginTop: 4, fontSize: 11 }}
                  onClick={() => disconnectBroker(b)}
                >
                  Disconnect {b}
                </button>
              ))}
            </div>
          )}
          {brokers.brokers.length === 0 && (
            <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
              No brokers connected. Connect IBKR for stocks or Polymarket for prediction markets.
            </p>
          )}
          {brokerStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: brokerStatus.includes("Failed") ? "#ef4444" : "#22c55e" }}>
              {brokerStatus}
            </p>
          )}
        </div>

        {/* Polymarket Markets */}
        {brokers.brokers.includes("polymarket") && (
          <div className="card dashboard-full">
            <h2>Polymarket Markets</h2>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <input
                className="input"
                value={marketSearch}
                onChange={(e) => setMarketSearch(e.target.value)}
                placeholder="Search markets..."
                onKeyDown={(e) => e.key === "Enter" && searchMarkets()}
                style={{ flex: 1 }}
              />
              <button className="btn" onClick={searchMarkets} disabled={marketsLoading}>
                Search
              </button>
              <button className="btn" onClick={loadTrendingMarkets} disabled={marketsLoading}>
                Trending
              </button>
            </div>
            {markets.length === 0 ? (
              <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
                {marketsLoading ? "Loading..." : "Search for markets or click Trending"}
              </p>
            ) : (
              <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>Market</th>
                    <th>Volume 24h</th>
                    <th>Liquidity</th>
                    <th>End Date</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {markets.map((m, i) => (
                    <tr key={i}>
                      <td style={{ maxWidth: 400 }}>{m.question || m.title || "Unknown"}</td>
                      <td>${(m.volume24hr || m.volume || 0).toLocaleString()}</td>
                      <td>${(m.liquidity || 0).toLocaleString()}</td>
                      <td>{m.endDate ? new Date(m.endDate).toLocaleDateString() : "-"}</td>
                      <td>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "2px 8px" }}
                          onClick={() => {
                            setTradeForm({
                              symbol: m.conditionId || m.id || "",
                              side: "buy",
                              quantity: 10,
                              price: 0.5,
                              broker: "polymarket",
                            });
                          }}
                        >
                          Trade
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
        )}

        {/* Manual Trade */}
        <div className="card">
          <h2>Manual Trade</h2>
          <div className="form-row">
            <label>Symbol</label>
            <input
              className="input"
              value={tradeForm.symbol}
              onChange={(e) => setTradeForm({ ...tradeForm, symbol: e.target.value.toUpperCase() })}
              placeholder="AAPL"
            />
          </div>
          <div className="form-row">
            <label>Side</label>
            <select
              className="select"
              value={tradeForm.side}
              onChange={(e) => setTradeForm({ ...tradeForm, side: e.target.value as "buy" | "sell" })}
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </div>
          <div className="form-row">
            <label>Quantity</label>
            <input
              className="input"
              type="number"
              value={tradeForm.quantity || ""}
              onChange={(e) => setTradeForm({ ...tradeForm, quantity: parseFloat(e.target.value) || 0 })}
              placeholder="10"
            />
          </div>
          <div className="form-row">
            <label>Price (est.)</label>
            <input
              className="input"
              type="number"
              value={tradeForm.price || ""}
              onChange={(e) => setTradeForm({ ...tradeForm, price: parseFloat(e.target.value) || 0 })}
              placeholder="150.00"
            />
          </div>
          {brokers.brokers.length > 1 && (
            <div className="form-row">
              <label>Broker</label>
              <select
                className="select"
                value={tradeForm.broker || ""}
                onChange={(e) => setTradeForm({ ...tradeForm, broker: e.target.value || undefined })}
              >
                <option value="">Default ({brokers.default})</option>
                {brokers.brokers.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
            </div>
          )}
          <button
            className="btn"
            onClick={submitTrade}
            disabled={!tradeForm.symbol || tradeForm.quantity <= 0}
          >
            Place Trade
          </button>
          {tradeStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: tradeStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {tradeStatus}
            </p>
          )}
        </div>

        {/* Positions */}
        <div className="card dashboard-full">
          <h2>Positions</h2>
          {positions.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No open positions</p>
          ) : (
            <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Broker</th>
                  <th>Qty</th>
                  <th>Avg Entry</th>
                  <th>Current</th>
                  <th>Unrealized P&L</th>
                  <th>Realized P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.symbol}</td>
                    <td>{p.broker}</td>
                    <td>{p.quantity}</td>
                    <td>${p.avg_entry_price?.toFixed(2)}</td>
                    <td>${p.current_price?.toFixed(2)}</td>
                    <td
                      className={
                        p.unrealized_pnl >= 0 ? "pnl-positive" : "pnl-negative"
                      }
                    >
                      ${p.unrealized_pnl?.toFixed(2)}
                    </td>
                    <td
                      className={
                        p.realized_pnl >= 0 ? "pnl-positive" : "pnl-negative"
                      }
                    >
                      ${p.realized_pnl?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* Trade Decisions */}
        <div className="card dashboard-full">
          <h2>Trade Decisions (LLM)</h2>
          {decisions.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No decisions yet</p>
          ) : (
            <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Strategy</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Confidence</th>
                  <th>Risk</th>
                  <th>Executed</th>
                  <th>Reasoning</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i}>
                    <td>{d.created_at}</td>
                    <td>{d.strategy}</td>
                    <td>{d.symbol}</td>
                    <td>
                      <span className={`badge badge-${d.side}`}>{d.side}</span>
                    </td>
                    <td>{d.quantity}</td>
                    <td>${d.price?.toFixed(2)}</td>
                    <td>{(d.confidence * 100).toFixed(0)}%</td>
                    <td>
                      <span
                        className={`status-dot ${
                          d.risk_check_passed ? "green" : "red"
                        }`}
                      />
                      {d.risk_check_passed ? "Pass" : "Fail"}
                    </td>
                    <td>{d.executed ? "Yes" : "No"}</td>
                    <td
                      style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}
                      title={d.reasoning}
                    >
                      {d.reasoning}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* Order History */}
        <div className="card dashboard-full">
          <h2>Order History</h2>
          {orders.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <button
                className="btn"
                onClick={() => window.open(`/api/export/trades?format=csv`, '_blank')}
                style={{ fontSize: 12, padding: "4px 12px" }}
              >
                Export CSV
              </button>
            </div>
          )}
          {orders.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No orders yet</p>
          ) : (
            <>
              <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Broker</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Type</th>
                    <th>Limit</th>
                    <th>Qty</th>
                    <th>Filled Price</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o, i) => (
                    <tr key={i}>
                      <td>{o.created_at}</td>
                      <td>{o.broker}</td>
                      <td>{o.symbol}</td>
                      <td>
                        <span className={`badge badge-${o.side}`}>{o.side}</span>
                      </td>
                      <td>{o.order_type}</td>
                      <td>{o.limit_price ? `$${o.limit_price.toFixed(2)}` : "-"}</td>
                      <td>{o.quantity}</td>
                      <td>{o.filled_price ? `$${o.filled_price.toFixed(2)}` : "-"}</td>
                      <td>{o.status}</td>
                      <td>
                        {(o.status === "pending" || o.status === "submitted") && (
                          <button
                            className="btn"
                            style={{ fontSize: 11, padding: "2px 8px" }}
                            onClick={() => handleCancelOrder(String(o.id), o.broker)}
                            disabled={cancellingOrderId === String(o.id)}
                          >
                            {cancellingOrderId === String(o.id) ? "Cancelling..." : "Cancel"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, alignItems: 'center' }}>
                <button disabled={orderPage === 0} onClick={() => setOrderPage(p => p - 1)}>← Previous</button>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Page {orderPage + 1} of {Math.ceil(orderTotal / ORDER_PAGE_SIZE) || 1}</span>
                <button disabled={orderPage * ORDER_PAGE_SIZE + ORDER_PAGE_SIZE >= orderTotal} onClick={() => setOrderPage(p => p + 1)}>Next →</button>
              </div>
            </>
          )}
        </div>

        {/* Live Event Log */}
        <div className="card dashboard-full">
          <h2>Live Event Stream</h2>
          <div className="event-log">
            {eventLog.length === 0 ? (
              <p style={{ color: "var(--text-muted)" }}>
                Waiting for events... (WebSocket)
              </p>
            ) : (
              eventLog.map((e, i) => (
                <div key={i} className="event-log-entry">
                  <span className="event-log-time">{e.time}</span>
                  <span className="event-log-type">{e.type}</span>
                  <span>{e.data}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
        </>
      )}
    </>
  );
}
