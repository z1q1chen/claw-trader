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
  PolymarketMarket,
  PerformanceSummary,
  SignalConfig,
  PositionSizingConfig,
  WebhookConfig,
  JournalEntry,
  PaginatedResponse,
} from "./types";

const API_BASE = "/api";

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (typeof window !== "undefined") {
    const apiKey = localStorage.getItem("claw-trader-api-key");
    if (apiKey) {
      headers["Authorization"] = `Bearer ${apiKey}`;
    }
  }
  return headers;
}

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: getAuthHeaders(),
    ...options,
  });
  if (!res.ok) {
    let detail = `API error: ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // Response wasn't JSON, use status text
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  // API Key Management
  setApiKey: (key: string) => {
    if (typeof window !== "undefined") {
      localStorage.setItem("claw-trader-api-key", key);
    }
  },
  clearApiKey: () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem("claw-trader-api-key");
    }
  },

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
  getOrders: (limit = 50, offset = 0) =>
    fetchJSON<{ data: Order[]; total: number; limit: number; offset: number; has_more: boolean }>(
      `/orders?limit=${limit}&offset=${offset}`
    ),

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
  cancelOrder: (broker: string, orderId: string) =>
    fetchJSON<{ success: boolean }>(`/orders/${orderId}/cancel`, {
      method: "POST",
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ broker }),
    }),

  // Live Risk
  getLiveRisk: () => fetchJSON<RiskSnapshot>("/risk/live"),

  // Markets (Polymarket)
  getTrendingMarkets: (limit = 10) =>
    fetchJSON<PolymarketMarket[]>(`/markets/trending?limit=${limit}`),
  searchMarkets: (query: string, limit = 10) =>
    fetchJSON<PolymarketMarket[]>(`/markets/search?q=${encodeURIComponent(query)}&limit=${limit}`),

  // Risk History
  getRiskHistory: (limit = 100) =>
    fetchJSON<RiskSnapshot[]>(`/risk/history?limit=${limit}`),

  // Broker Orders
  getBrokerOrders: (broker: string, limit = 50) =>
    fetchJSON<Order[]>(`/orders/broker/${broker}?limit=${limit}`),

  // Stats
  getStats: () => fetchJSON<Record<string, unknown>>("/stats"),

  // Performance
  getPerformanceSummary: () => fetchJSON<PerformanceSummary>("/performance/summary"),
  getPerformanceMetrics: (days = 30) =>
    fetchJSON<{ data: unknown[]; period_days: number }>(`/performance/metrics?days=${days}`),

  // Dry-run status
  getDryRunStatus: () => fetchJSON<{ enabled: boolean }>("/config/dry-run"),

  // Signal Config
  getSignalConfig: () => fetchJSON<SignalConfig>("/config/signal"),
  updateSignalConfig: (config: Partial<SignalConfig>) =>
    fetchJSON<{ status: string }>("/config/signal", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  // Position Sizing Config
  getPositionSizingConfig: () => fetchJSON<PositionSizingConfig>("/config/position-sizing"),
  updatePositionSizingConfig: (config: Partial<PositionSizingConfig>) =>
    fetchJSON<{ status: string }>("/config/position-sizing", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  // Strategy Presets
  getStrategyPresets: () => fetchJSON<Record<string, any>>("/presets"),
  applyStrategyPreset: (presetName: string) =>
    fetchJSON<{ status: string; preset: string }>(`/presets/${presetName}/apply`, {
      method: "POST",
    }),

  // Webhooks
  getWebhooks: () => fetchJSON<WebhookConfig[]>("/webhooks"),
  createWebhook: (url: string, eventTypes: string[]) =>
    fetchJSON<{ id: string; status: string }>("/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, event_types: eventTypes }),
    }),
  deleteWebhook: (id: string) =>
    fetchJSON<{ status: string }>(`/webhooks/${id}`, { method: "DELETE" }),
  testWebhook: (id: string) =>
    fetchJSON<{ status: string }>(`/webhooks/${id}/test`, { method: "POST" }),

  // Trade Journal
  getTradeJournal: (limit = 50, offset = 0) =>
    fetchJSON<PaginatedResponse<JournalEntry>>(`/journal?limit=${limit}&offset=${offset}`),
};

export function createWebSocket(
  onMessage: (event: { type: string; data: Record<string, unknown>; timestamp: string }) => void,
  onConnectionChange?: (connected: boolean) => void
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
      onConnectionChange?.(true);
    };

    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {
        console.warn("Failed to parse WS message:", e.data);
      }
    };

    ws.onclose = () => {
      onConnectionChange?.(false);
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
