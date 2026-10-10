import type { CaseImportResult, ConverterReport, TaskAccepted, TaskEvent, TaskRecord } from "../types";

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
      message = body.detail ?? message;
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

export function importCases(file: File): Promise<CaseImportResult> {
  const form = new FormData();
  form.append("file", file);
  return request<CaseImportResult>("/api/cases/import", { method: "POST", body: form });
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
