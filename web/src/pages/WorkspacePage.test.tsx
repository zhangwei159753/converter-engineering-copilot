import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspacePage from "./WorkspacePage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  listTasks: vi.fn(),
}));

describe("WorkspacePage", () => {
  beforeEach(() => {
    vi.mocked(api.listTasks).mockResolvedValue([]);
  });

  it("shows all three modes and disables modes awaiting workflows", async () => {
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    expect(await screen.findByText("故障现象分析")).toBeInTheDocument();
    expect(screen.getByText("技术调研与实验设计")).toBeInTheDocument();
    expect(screen.getByText("检修案例检索")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /技术调研与实验设计/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /检修案例检索/ })).toBeDisabled();
  });

  it("validates the fault form and submits a valid task", async () => {
    vi.mocked(api.createTask).mockResolvedValue({
      task: {
        task_id: "task-1", mode: "fault_analysis", status: "queued",
        inputs: {}, created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
      },
    });
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    expect(await screen.findByText("请填写设备类型和故障现象。" )).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("设备类型"), { target: { value: "光伏逆变器" } });
    fireEvent.change(screen.getByLabelText("故障现象"), { target: { value: "直流母线过压" } });
    fireEvent.change(screen.getByLabelText("运行工况"), { target: { value: "负载 50%，环境温度 35℃" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalled());
  });

  it("renders a visible API error", async () => {
    vi.mocked(api.createTask).mockRejectedValue(new Error("后端暂不可用"));
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("设备类型"), { target: { value: "光伏逆变器" } });
    fireEvent.change(screen.getByLabelText("故障现象"), { target: { value: "直流母线过压" } });
    fireEvent.change(screen.getByLabelText("运行工况"), { target: { value: "负载 50%" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    expect(await screen.findByText("后端暂不可用")).toBeInTheDocument();
  });
});
