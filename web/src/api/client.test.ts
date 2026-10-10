import { afterEach, describe, expect, it, vi } from "vitest";
import { createTask, retryTask } from "./client";

describe("task API idempotency", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends an idempotency key for create and retry actions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ task: {} }) });
    vi.stubGlobal("fetch", fetchMock);

    await createTask({ mode: "fault_analysis", inputs: { symptom: "overvoltage" } });
    await retryTask("task / 1");

    const createHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>;
    expect(createHeaders["Idempotency-Key"]).toBeTruthy();
    expect(retryHeaders["Idempotency-Key"]).toBeTruthy();
    expect(retryHeaders["Idempotency-Key"]).not.toBe(createHeaders["Idempotency-Key"]);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/tasks/task%20%2F%201/retry");
  });
});
