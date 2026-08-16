const MAX_WS_FRAME_BYTES = 1_000_000;
const ALLOWED_WS_TYPES = new Set([
  "init_workflows",
  "workflow_update",
  "log_event",
  "error",
]);

/** Resolve a configured API origin without accepting script/data URLs. */
export function getApiBase(configured = import.meta.env?.VITE_API_BASE) {
  const raw = (configured || "http://localhost:8000").trim();
  try {
    const url = new URL(raw);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error("Invalid API origin");
    }
    return url.toString().replace(/\/$/, "");
  } catch {
    return "http://localhost:8000";
  }
}

export function getWebSocketUrl(apiBase) {
  const url = new URL(getApiBase(apiBase));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return `${url.toString().replace(/\/$/, "")}/ws`;
}

/** Parse only bounded JSON object frames from the authenticated socket. */
export function parseWebSocketMessage(raw) {
  if (typeof raw !== "string" || new TextEncoder().encode(raw).length > MAX_WS_FRAME_BYTES) return null;
  try {
    const message = JSON.parse(raw);
    if (!message || typeof message !== "object" || Array.isArray(message)) return null;
    if (typeof message.type !== "string" || !ALLOWED_WS_TYPES.has(message.type)) return null;
    return message;
  } catch {
    return null;
  }
}

export function capLogEntries(entries, max = 300) {
  return entries.slice(-max);
}
