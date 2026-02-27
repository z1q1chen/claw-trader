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
  getLLMConfig: () => fetchJSON<any>("/llm/config"),
  updateLLMConfig: (config: {
    provider: string;
    model_name: string;
    api_key: string;
    base_url?: string;
  }) =>
    fetchJSON<any>("/llm/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  // API Usage
  getUsage: (limit = 100) => fetchJSON<any[]>(`/usage?limit=${limit}`),
  getUsageSummary: () => fetchJSON<any[]>("/usage/summary"),

  // Trade Decisions
  getDecisions: (limit = 50) =>
    fetchJSON<any[]>(`/decisions?limit=${limit}`),

  // Orders
  getOrders: (limit = 50) => fetchJSON<any[]>(`/orders?limit=${limit}`),

  // Positions
  getPositions: () => fetchJSON<any[]>("/positions"),

  // Balance
  getBalance: (broker: string) => fetchJSON<any>(`/balance/${broker}`),

  // Risk
  getRiskSnapshot: () => fetchJSON<any>("/risk"),
  getRiskConfig: () => fetchJSON<any>("/risk/config"),
  updateRiskConfig: (config: any) =>
    fetchJSON<any>("/risk/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  toggleKillSwitch: (active: boolean) =>
    fetchJSON<any>("/risk/killswitch", {
      method: "POST",
      body: JSON.stringify({ active }),
    }),

  // Signals
  getSignals: (limit = 100) =>
    fetchJSON<any[]>(`/signals?limit=${limit}`),

  // Health
  getHealth: () => fetchJSON<any>("/health"),
};

export function createWebSocket(
  onMessage: (event: any) => void
): { close: () => void } {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//127.0.0.1:8000/ws`;
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
