import { afterEach, describe, expect, it, vi } from "vitest";
import { createTask, deleteImport, getAgentRuns, getHypothesisBranches, importCases, listImports, repairImport, retryTask, searchMemory } from "./client";

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

describe("source import API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the import ledger endpoints with encoded identifiers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    await listImports();
    await repairImport("import / 1");
    await deleteImport("import / 1");

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/imports", undefined],
      ["/api/imports/import%20%2F%201/repair", "POST"],
      ["/api/imports/import%20%2F%201", "DELETE"],
    ]);
  });

  it("turns structured validation details into readable messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: { code: "missing_required_field", message: "CSV row 2 is missing symptom.", filename: "cases.csv", row: 2 } }),
    }));

    await expect(importCases(new File(["symptom"], "cases.csv"))).rejects.toThrow("CSV row 2 is missing symptom.");
  });
});

describe("agent collaboration read APIs", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads task-scoped traces and branch snapshots with encoded IDs", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ task_id: "task", items: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await getAgentRuns("task / 1");
    await getHypothesisBranches("task / 1");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/tasks/task%20%2F%201/agents",
      "/api/tasks/task%20%2F%201/hypotheses",
    ]);
  });
});

describe("reviewed memory API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("encodes query fields and bounds results at the caller", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], synthetic_demo: false }) });
    vi.stubGlobal("fetch", fetchMock);
    await searchMemory("过压 & 停机", "CV 300", 5);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/memory?q=%E8%BF%87%E5%8E%8B+%26+%E5%81%9C%E6%9C%BA&equipment_type=CV+300&limit=5");
  });
});
