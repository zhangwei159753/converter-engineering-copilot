import type { AgentRun, CaseImportResult, ConverterReport, EvaluationExample, EvaluationReviewEvent, EvaluationSummary, HypothesisBranch, ImportSummary, MemoryMatch, TaskAccepted, TaskEvent, TaskRecord, WorkOrder, WorkOrderDetail, WorkOrderReview } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      const detail = body.detail;
      message = typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : message;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function createTask(payload: { mode: "fault_analysis" | "technical_research" | "case_retrieval"; inputs: object }): Promise<TaskAccepted> {
  return request<TaskAccepted>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify(payload),
  });
}

export function listTasks(): Promise<TaskRecord[]> {
  return request<TaskRecord[]>("/api/tasks?limit=50");
}

export function getTask(taskId: string): Promise<TaskRecord> {
  return request<TaskRecord>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function retryTask(taskId: string): Promise<TaskAccepted> {
  return request<TaskAccepted>(`/api/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
  });
}

export function getReport(taskId: string): Promise<ConverterReport> {
  return request<ConverterReport>(`/api/tasks/${encodeURIComponent(taskId)}/report`);
}

export function getAgentRuns(taskId: string): Promise<{ task_id: string; items: AgentRun[] }> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/agents`);
}

export function getHypothesisBranches(taskId: string): Promise<{ task_id: string; items: HypothesisBranch[] }> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/hypotheses`);
}

export function importCases(file: File): Promise<CaseImportResult> {
  const form = new FormData();
  form.append("file", file);
  return request<CaseImportResult>("/api/cases/import", { method: "POST", body: form });
}

export function listImports(): Promise<ImportSummary[]> {
  return request<ImportSummary[]>("/api/imports");
}

export function repairImport(importId: string): Promise<ImportSummary> {
  return request<ImportSummary>(`/api/imports/${encodeURIComponent(importId)}/repair`, { method: "POST" });
}

export function deleteImport(importId: string): Promise<ImportSummary> {
  return request<ImportSummary>(`/api/imports/${encodeURIComponent(importId)}`, { method: "DELETE" });
}

export function getEvaluationSummary(): Promise<EvaluationSummary> {
  return request<EvaluationSummary>("/api/evaluations");
}

export function importReviewBundle(bundle: unknown): Promise<{ bundle_id: string; imported_count: number; skipped_count: number; reused: boolean }> {
  return request("/api/evaluations/review-bundles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(bundle) });
}

export function listEvaluationExamples(params: { mode?: string; status?: string; source_type?: string; limit?: number; offset?: number } = {}): Promise<{ items: EvaluationExample[]; total: number }> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
  return request(`/api/evaluations/examples?${query}`);
}

export function saveEvaluationReview(reviewId: string, payload: { reviewer_id: string; evidence_labels: EvaluationExample["evidence_labels"]; expected_retrieval_ids: string[]; note?: string }): Promise<EvaluationReviewEvent> {
  return request(`/api/evaluations/examples/${encodeURIComponent(reviewId)}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export function getEvaluationHistory(reviewId: string): Promise<EvaluationReviewEvent[]> {
  return request(`/api/evaluations/examples/${encodeURIComponent(reviewId)}/history`);
}

export function listWorkOrders(params: { q?: string; status?: string } = {}): Promise<{ items: WorkOrder[]; total: number; synthetic_demo: true }> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value); });
  return request(`/api/work-orders?${query}`);
}

export function getWorkOrder(workOrderId: string): Promise<WorkOrderDetail> {
  return request(`/api/work-orders/${encodeURIComponent(workOrderId)}`);
}

export function createWorkOrderAnalysis(workOrderId: string): Promise<{ task: TaskRecord; reused: boolean; work_order_id: string }> {
  return request(`/api/work-orders/${encodeURIComponent(workOrderId)}/analyses`, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
  });
}

export function saveWorkOrderReview(workOrderId: string, taskId: string, payload: { reviewer: string; verdict: WorkOrderReview["verdict"]; note?: string }): Promise<WorkOrderReview> {
  return request(`/api/work-orders/${encodeURIComponent(workOrderId)}/analyses/${encodeURIComponent(taskId)}/review`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function searchMemory(query: string, equipmentType: string, limit = 10): Promise<{ items: MemoryMatch[]; synthetic_demo: false }> {
  const params = new URLSearchParams({ q: query, equipment_type: equipmentType, limit: String(limit) });
  return request(`/api/memory?${params}`);
}

const pause = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve) => {
  const timer = window.setTimeout(resolve, milliseconds);
  signal.addEventListener("abort", () => {
    window.clearTimeout(timer);
    resolve();
  }, { once: true });
});

export async function streamTaskEvents(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let lastEventId = 0;
  let delay = 400;
  while (!signal.aborted) {
    try {
      const response = await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(taskId)}/events`, {
        headers: lastEventId ? { "Last-Event-ID": String(lastEventId) } : {},
        signal,
      });
      if (!response.ok || !response.body) throw new Error("进度连接暂时中断");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminal = false;
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const id = frame.match(/^id:\s*(\d+)/m)?.[1];
          const data = frame.match(/^data:\s*(.+)$/m)?.[1];
          if (!data) continue;
          const event = JSON.parse(data) as TaskEvent;
          if (id) lastEventId = Number(id);
          onEvent(event);
          if (["completed", "partial", "failed"].includes(event.status)) terminal = true;
        }
      }
      if (terminal || signal.aborted) return;
    } catch (error) {
      if (signal.aborted) return;
      onEvent({
        event_id: lastEventId,
        task_id: taskId,
        stage: "reconnecting",
        status: "reconnecting",
        message: "进度连接中断，正在从上次事件继续连接。",
        created_at: new Date().toISOString(),
      });
    }
    await pause(delay, signal);
    delay = Math.min(delay * 1.6, 4000);
  }
}
