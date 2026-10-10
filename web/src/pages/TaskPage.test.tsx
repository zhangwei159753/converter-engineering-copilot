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
    });
    vi.mocked(api.streamTaskEvents).mockResolvedValue(undefined);
    vi.mocked(api.retryTask).mockRejectedValue(new Error("重试次数已达上限"));
    render(<MemoryRouter initialEntries={["/tasks/task-1"]}><Routes><Route path="/tasks/:taskId" element={<TaskPage />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "重试任务" }));
    await waitFor(() => expect(screen.getByText("重试次数已达上限")).toBeInTheDocument());
  });
});
