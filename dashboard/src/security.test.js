import { describe, expect, it } from "vitest";
import { capLogEntries, getApiBase, getWebSocketUrl, parseWebSocketMessage } from "./security.js";

describe("dashboard security seams", () => {
  it("accepts only HTTP(S) API origins", () => {
    expect(getApiBase("https://proxy.example.test/")) .toBe("https://proxy.example.test");
    expect(getApiBase("javascript:alert(1)")).toBe("http://localhost:8000");
    expect(getApiBase("https://user:pass@example.test")).toBe("http://localhost:8000");
  });

  it("derives TLS-aware websocket URLs", () => {
    expect(getWebSocketUrl("https://proxy.example.test")).toBe("wss://proxy.example.test/ws");
    expect(getWebSocketUrl("http://localhost:8000/")).toBe("ws://localhost:8000/ws");
  });

  it("rejects malformed, unknown, and oversized socket frames", () => {
    expect(parseWebSocketMessage("not-json")).toBeNull();
    expect(parseWebSocketMessage(JSON.stringify({ type: "unknown" }))).toBeNull();
    expect(parseWebSocketMessage(JSON.stringify({ type: "ping" }))).toBeNull();
    expect(parseWebSocketMessage(JSON.stringify({ type: "error", message: "safe" }))).toEqual({ type: "error", message: "safe" });
    expect(parseWebSocketMessage("x".repeat(1_000_001))).toBeNull();
  });

  it("bounds client-side log retention", () => {
    expect(capLogEntries([1, 2, 3], 2)).toEqual([2, 3]);
  });
});
