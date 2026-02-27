import {
  HealthResponse,
  LLMConfig,
  UsageSummary,
  TradeDecision,
  Order,
  Position,
  RiskSnapshot,
  RiskConfig,
  Signal,
  BrokersResponse,
} from "./types";

const API_BASE = "/api";

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  // LLM Config
  getLLMConfig: () => fetchJSON<LLMConfig>("/llm/config"),
  updateLLMConfig: (config: LLMConfig) =>
    fetchJSON<LLMConfig>("/llm/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  // API Usage
  getUsage: (limit = 100) => fetchJSON<UsageSummary[]>(`/usage?limit=${limit}`),
  getUsageSummary: () => fetchJSON<UsageSummary[]>("/usage/summary"),

  // Trade Decisions
  getDecisions: (limit = 50) =>
    fetchJSON<TradeDecision[]>(`/decisions?limit=${limit}`),

  // Orders
  getOrders: (limit = 50) => fetchJSON<Order[]>(`/orders?limit=${limit}`),

  // Positions
  getPositions: () => fetchJSON<Position[]>("/positions"),

  // Balance
  getBalance: (broker: string) => fetchJSON<{ balance: number }>(`/balance/${broker}`),

  // Risk
  getRiskSnapshot: () => fetchJSON<RiskSnapshot>("/risk"),
  getRiskConfig: () => fetchJSON<RiskConfig>("/risk/config"),
  updateRiskConfig: (config: RiskConfig) =>
    fetchJSON<RiskConfig>("/risk/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  toggleKillSwitch: (active: boolean) =>
    fetchJSON<{ kill_switch_active: boolean }>("/risk/killswitch", {
      method: "POST",
      body: JSON.stringify({ active }),
    }),

  // Signals
  getSignals: (limit = 100) =>
    fetchJSON<Signal[]>(`/signals?limit=${limit}`),

  // Health
  getHealth: () => fetchJSON<HealthResponse>("/health"),

  // Brokers
  listBrokers: () => fetchJSON<BrokersResponse>("/brokers"),
  connectBroker: (broker: string) =>
    fetchJSON<BrokersResponse>("/broker/connect", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),
  disconnectBroker: (broker: string) =>
    fetchJSON<BrokersResponse>("/broker/disconnect", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),

  // Manual Trading
  placeTrade: (trade: { symbol: string; side: string; quantity: number; price?: number; broker?: string }) =>
    fetchJSON<TradeDecision>("/trade", {
      method: "POST",
      body: JSON.stringify(trade),
    }),

  // Order Management
  cancelOrder: (orderId: string) =>
    fetchJSON<Order>(`/orders/${orderId}/cancel`, { method: "POST" }),

  // Live Risk
  getLiveRisk: () => fetchJSON<RiskSnapshot>("/risk/live"),

  // Markets (Polymarket)
  getTrendingMarkets: (limit = 10) =>
    fetchJSON<any[]>(`/markets/trending?limit=${limit}`),
  searchMarkets: (query: string, limit = 10) =>
    fetchJSON<any[]>(`/markets/search?q=${encodeURIComponent(query)}&limit=${limit}`),

  // Risk History
  getRiskHistory: (limit = 100) =>
    fetchJSON<RiskSnapshot[]>(`/risk/history?limit=${limit}`),

  // Broker Orders
  getBrokerOrders: (broker: string, limit = 50) =>
    fetchJSON<any[]>(`/orders/broker/${broker}?limit=${limit}`),
};

export function createWebSocket(
  onMessage: (event: any) => void
): { close: () => void } {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${window.location.host}/ws`;
  let ws: WebSocket | null = null;
  let reconnectDelay = 1000;
  let shouldReconnect = true;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connect() {
    ws = new WebSocket(url);

    ws.onopen = () => {
      reconnectDelay = 1000; // Reset backoff on successful connect
    };

    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {
        console.warn("Failed to parse WS message:", e.data);
      }
    };

    ws.onclose = () => {
      if (shouldReconnect) {
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, 30000); // Max 30s
          connect();
        }, reconnectDelay);
      }
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  connect();

  return {
    close: () => {
      shouldReconnect = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    },
  };
}
