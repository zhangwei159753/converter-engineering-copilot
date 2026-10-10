import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TaskPage from "./TaskPage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({ getTask: vi.fn(), retryTask: vi.fn(), streamTaskEvents: vi.fn() }));

describe("TaskPage", () => {
  it("shows progress stages and report-ready navigation", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      task_id: "task-1", mode: "fault_analysis", status: "completed", inputs: {},
      created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:01:00Z",
      attempt: 1, max_attempts: 3, lease_expires_at: null, error_code: null,
    });
    vi.mocked(api.streamTaskEvents).mockImplementation(async (_id, onEvent) => {
      onEvent({ event_id: 5, task_id: "task-1", stage: "complete", status: "completed", message: "报告已生成。", created_at: "2026-10-10T00:01:00Z" });
    });
    render(<MemoryRouter initialEntries={["/tasks/task-1"]}><Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("报告已生成。" )).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看分析报告" })).toHaveAttribute("href", "/tasks/task-1/report");
  });

  it("shows a failed task, retries it, and displays errors", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      task_id: "task-1", mode: "fault_analysis", status: "failed", inputs: {},
      created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:01:00Z",
      attempt: 1, max_attempts: 3, lease_expires_at: null, error_code: "workflow_error",
    });
    vi.mocked(api.streamTaskEvents).mockResolvedValue(undefined);
    vi.mocked(api.retryTask).mockRejectedValue(new Error("重试次数已达上限"));
    render(<MemoryRouter initialEntries={["/tasks/task-1"]}><Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "重试任务" }));
    await waitFor(() => expect(screen.getByText("重试次数已达上限")).toBeInTheDocument());
  });

  it("shows attempt history, recovery events, and safe guidance for an exhausted task", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      task_id: "task-recovered", mode: "fault_analysis", status: "failed", inputs: {},
      created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:01:00Z",
      attempt: 3, max_attempts: 3, lease_expires_at: null, error_code: "attempt_limit",
    });
    vi.mocked(api.streamTaskEvents).mockImplementation(async (_id, onEvent) => {
      onEvent({ event_id: 6, task_id: "task-recovered", stage: "recovered", status: "queued", message: "检测到执行租约过期，任务已重新排队。", created_at: "2026-10-10T00:01:00Z" });
      onEvent({ event_id: 7, task_id: "task-recovered", stage: "failed", status: "failed", message: "任务达到最大执行次数，已停止自动恢复。", created_at: "2026-10-10T00:02:00Z" });
    });
    render(<MemoryRouter initialEntries={["/tasks/task-recovered"]}><Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText("执行尝试 3 / 3")).toBeInTheDocument();
    expect(screen.getByText("检测到执行租约过期，任务已重新排队。")).toBeInTheDocument();
    expect(screen.getByText("任务连续恢复次数已达上限，请检查本地服务日志后手动重试。")).toBeInTheDocument();
    expect(screen.queryByText(/worker-|secret/i)).not.toBeInTheDocument();
  });
});
