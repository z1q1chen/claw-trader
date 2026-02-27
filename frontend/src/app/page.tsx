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
} from "@/lib/types";

interface EventLogEntry {
  time: string;
  type: string;
  data: string;
}

export default function Dashboard() {
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
  });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [eventLog, setEventLog] = useState<EventLogEntry[]>([]);
  const [killSwitch, setKillSwitch] = useState(false);
  const [brokers, setBrokers] = useState<BrokersResponse>({ brokers: [], default: null });
  const [connectingBroker, setConnectingBroker] = useState(false);
  const [brokerStatus, setBrokerStatus] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<string | null>(null);
  const [riskStatus, setRiskStatus] = useState<string | null>(null);
  const [tradeForm, setTradeForm] = useState({
    symbol: "",
    side: "buy" as "buy" | "sell",
    quantity: 0,
    price: 0,
    broker: undefined as string | undefined,
  });
  const [tradeStatus, setTradeStatus] = useState<string | null>(null);
  const wsRef = useRef<{ close: () => void } | null>(null);

  const refreshData = useCallback(async () => {
    try {
      const [h, cfg, usage, dec, ord, pos, risk, rc, sig, brok] = await Promise.all([
        api.getHealth().catch(() => ({ status: "error" })),
        api.getLLMConfig().catch(() => null),
        api.getUsageSummary().catch(() => []),
        api.getDecisions(20).catch(() => []),
        api.getOrders(20).catch(() => []),
        api.getPositions().catch(() => []),
        api.getRiskSnapshot().catch(() => ({})),
        api.getRiskConfig().catch(() => ({})),
        api.getSignals(50).catch(() => []),
        api.listBrokers().catch(() => ({ brokers: [], default: null })),
      ]);

      setHealth(h.status === "ok" ? "connected" : "error");
      if (cfg) setLlmConfig((prev) => ({ ...prev, ...cfg }));
      setUsageSummary(usage);
      setDecisions(dec);
      setOrders(ord);
      setPositions(pos);
      setRiskSnapshot(risk);
      setRiskConfig(rc);
      setSignals(sig);
      setBrokers(brok);
      setKillSwitch(!!risk.kill_switch_active);
    } catch {
      setHealth("error");
    }
  }, []);

  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 5000);
    return () => clearInterval(interval);
  }, [refreshData]);

  useEffect(() => {
    const conn = createWebSocket((event) => {
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
    });
    wsRef.current = conn;
    return () => conn.close();
  }, [refreshData]);

  const saveLLMConfig = async () => {
    setLlmStatus(null);
    try {
      await api.updateLLMConfig({
        provider: llmConfig.provider,
        model_name: llmConfig.model_name,
        api_key: llmConfig.api_key,
        base_url: llmConfig.base_url || undefined,
      });
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

  return (
    <>
      <div className="header">
        <h1>Claw Trader</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span>
            <span
              className={`status-dot ${health === "connected" ? "green" : "red"}`}
            />
            {health}
          </span>
          <button
            className={`kill-switch ${killSwitch ? "active" : ""}`}
            onClick={handleKillSwitch}
          >
            {killSwitch ? "KILL SWITCH ON" : "KILL SWITCH"}
          </button>
        </div>
      </div>

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
          {llmConfig.provider === "local" && (
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

        {/* Risk Configuration */}
        <div className="card">
          <h2>Risk Limits</h2>
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
          <button className="btn" onClick={saveRiskConfig}>
            Update Risk Limits
          </button>
          {riskStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: riskStatus.startsWith("Error") ? "#ef4444" : "#22c55e" }}>
              {riskStatus}
            </p>
          )}
        </div>

        {/* Signals */}
        <div className="card dashboard-full">
          <h2>Recent Signals</h2>
          {signals.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No signals yet</p>
          ) : (
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
                    <td>{s.value}</td>
                    <td>{s.created_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
              No brokers connected. Connect IBKR TWS/Gateway to trade.
            </p>
          )}
          {brokerStatus && (
            <p style={{ marginTop: 8, fontSize: 12, color: brokerStatus.includes("Failed") ? "#ef4444" : "#22c55e" }}>
              {brokerStatus}
            </p>
          )}
        </div>

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
          )}
        </div>

        {/* Trade Decisions */}
        <div className="card dashboard-full">
          <h2>Trade Decisions (LLM)</h2>
          {decisions.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No decisions yet</p>
          ) : (
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
          )}
        </div>

        {/* Order History */}
        <div className="card dashboard-full">
          <h2>Order History</h2>
          {orders.length === 0 ? (
            <p style={{ color: "var(--text-muted)" }}>No orders yet</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Broker</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Type</th>
                  <th>Qty</th>
                  <th>Filled Price</th>
                  <th>Status</th>
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
                    <td>{o.quantity}</td>
                    <td>{o.filled_price ? `$${o.filled_price.toFixed(2)}` : "-"}</td>
                    <td>{o.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
  );
}
