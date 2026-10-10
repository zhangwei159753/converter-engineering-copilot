import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspacePage from "./WorkspacePage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  listTasks: vi.fn(),
  listImports: vi.fn(),
  importCases: vi.fn(),
  repairImport: vi.fn(),
  deleteImport: vi.fn(),
}));

const importRecord = {
  import_id: "import-1", document_id: "import-1", source_name: "demo-cases.csv", file_type: "csv",
  parser_version: "csv-v1", chunker_version: "locator-chunk-v1", expected_chunk_count: 4,
  indexed_chunks: 0, imported_cases: 0, status: "needs_repair" as const,
  created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
  error_code: "vectors_missing", synthetic_demo: true,
};

describe("WorkspacePage", () => {
  beforeEach(() => {
    vi.mocked(api.listTasks).mockResolvedValue([]);
    vi.mocked(api.listImports).mockResolvedValue([]);
  });

  it("shows all three implemented workflows as available", async () => {
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    expect(await screen.findByText("故障现象分析")).toBeInTheDocument();
    expect(screen.getByText("技术调研与实验设计")).toBeInTheDocument();
    expect(screen.getByText("检修案例检索")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /技术调研与实验设计/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /检修案例检索/ })).toBeEnabled();
  });

  it("submits a technical research question", async () => {
    vi.mocked(api.createTask).mockResolvedValue({ task: {
      task_id: "research-1", mode: "technical_research", status: "queued", inputs: {},
      created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
      attempt: 0, max_attempts: 3, lease_expires_at: null, error_code: null,
    } });
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "技术调研与实验设计" }));
    fireEvent.change(screen.getByLabelText("调研主题"), { target: { value: "两电平与三电平拓扑对照" } });
    fireEvent.change(screen.getByLabelText("技术范围"), { target: { value: "并网光伏" } });
    fireEvent.click(screen.getByRole("button", { name: "开始调研" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalledWith(expect.objectContaining({ mode: "technical_research" })));
  });

  it("submits a case retrieval request with optional equipment and operating context", async () => {
    vi.mocked(api.createTask).mockResolvedValue({ task: {
      task_id: "case-task-1", mode: "case_retrieval", status: "queued", inputs: {},
      created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
      attempt: 0, max_attempts: 3, lease_expires_at: null, error_code: null,
    } });
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "检修案例检索" }));
    fireEvent.change(screen.getByLabelText("故障现象"), { target: { value: "启动后过流" } });
    fireEvent.change(screen.getByLabelText("设备类型"), { target: { value: "光伏逆变器" } });
    fireEvent.change(screen.getByLabelText("运行工况"), { target: { value: "负载 40%，环境温度 30℃" } });
    fireEvent.click(screen.getByRole("button", { name: "检索相似案例" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalledWith(expect.objectContaining({ mode: "case_retrieval" })));
  });

  it("validates the fault form and submits a valid task", async () => {
    vi.mocked(api.createTask).mockResolvedValue({
      task: {
        task_id: "task-1", mode: "fault_analysis", status: "queued",
        inputs: {}, created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z",
        attempt: 0, max_attempts: 3, lease_expires_at: null, error_code: null,
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

  it("shows import health, synthetic attribution, and repairs an incomplete import", async () => {
    vi.mocked(api.listImports).mockResolvedValue([importRecord]);
    vi.mocked(api.repairImport).mockResolvedValue({ ...importRecord, status: "complete", indexed_chunks: 4, error_code: null });
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);

    expect(await screen.findByText("demo-cases.csv")).toBeInTheDocument();
    expect(screen.getByText("合成演示数据，不是现场记录")).toBeInTheDocument();
    expect(screen.getByText(/向量索引缺失/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修复导入" }));

    await waitFor(() => expect(api.repairImport).toHaveBeenCalledWith("import-1"));
    expect(await screen.findByText("导入完成")).toBeInTheDocument();
  });

  it("shows duplicate upload notice and readable validation errors", async () => {
    vi.mocked(api.importCases).mockResolvedValue({
      import_id: "import-1", document_ids: ["import-1"], chunk_count: 4, imported_case_count: 2, reused: true,
    });
    const { container, rerender } = render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const upload = new File(["symptom"], "cases.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [upload] } });
    expect(await screen.findByText(/已存在相同内容，复用现有导入记录/)).toBeInTheDocument();

    vi.mocked(api.importCases).mockRejectedValue(new Error("CSV 第 2 行缺少故障现象字段。"));
    rerender(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    fireEvent.change(input, { target: { files: [upload] } });
    expect(await screen.findByText("CSV 第 2 行缺少故障现象字段。")).toBeInTheDocument();
  });

  it("requires browser confirmation before deleting an import", async () => {
    vi.mocked(api.listImports).mockResolvedValue([importRecord]);
    vi.mocked(api.deleteImport).mockResolvedValue({ ...importRecord, status: "deleted" });
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirm);
    render(<MemoryRouter><WorkspacePage /></MemoryRouter>);
    const deleteButton = await screen.findByRole("button", { name: "删除导入" });

    fireEvent.click(deleteButton);
    expect(confirm).toHaveBeenCalled();
    expect(api.deleteImport).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(deleteButton);
    await waitFor(() => expect(api.deleteImport).toHaveBeenCalledWith("import-1"));
    vi.unstubAllGlobals();
  });
});
