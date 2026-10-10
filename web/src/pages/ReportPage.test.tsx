import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ReportPage from "./ReportPage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({ getReport: vi.fn() }));

describe("ReportPage", () => {
  it("shows mode result, citation status, evidence drawer, and markdown export", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
      task_id: "task-1", mode: "fault_analysis", completion_status: "partial",
      result: { hypotheses: [{ hypothesis: "制动单元异常", mechanism: "能量无法耗散", confidence: "medium", confidence_basis: "手册线索", supporting_evidence_ids: ["ev-1"], opposing_evidence_ids: [], checks: ["测量母线电压"] }], checks: ["检查制动回路"] },
      claims: [
        { claim_id: "claim-1", text: "制动单元异常", claim_type: "inference", evidence_ids: ["ev-1"], verification_status: "supported" },
        { claim_id: "claim-2", text: "需要更多数据", claim_type: "fact", evidence_ids: [], verification_status: "unverified" },
      ],
      evidence: [{ evidence_id: "ev-1", task_id: "task-1", title: "设备手册", source_type: "document", excerpt: "检查制动单元。", document_id: "doc-1", locator: "page 4", accessed_at: "2026-10-10T00:00:00Z", related_claim_ids: ["claim-1"], source_status: "available" }],
      quality_warnings: ["claim-2 无引用"],
    });
    render(<MemoryRouter initialEntries={["/tasks/task-1/report"]}><Routes><Route path="/tasks/:taskId/report" element={<ReportPage />} /></Routes></MemoryRouter>);
    expect((await screen.findAllByText("制动单元异常")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("缺少证据")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "ev-1" })[0]);
    expect(await screen.findByText("设备手册")).toBeInTheDocument();
    expect(screen.getByText(/page 4/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /导出 Markdown/ })).toBeInTheDocument();
  });

  it("renders research methods, conditions, limitations, and experiment steps", async () => {
    vi.mocked(api.getReport).mockResolvedValue({
      task_id: "research-1", mode: "technical_research", completion_status: "completed",
      result: {
        comparisons: [{ item: "NPC 三电平", method_or_value: "输出 THD 较低", experimental_conditions: "400 V 母线，10 kHz", datasets: ["仿真基准集"], evidence_ids: ["paper-1"] }],
        limitations: ["结果受调制策略影响"], experiment_steps: ["统一开关频率后测量 THD"],
      },
      claims: [{ claim_id: "claim-1", text: "NPC 三电平：输出 THD 较低", claim_type: "fact", evidence_ids: ["paper-1"], verification_status: "supported" }],
      evidence: [{ evidence_id: "paper-1", task_id: "research-1", title: "拓扑对比研究", source_type: "web", excerpt: "仿真比较。", url: "https://example.test/paper", accessed_at: "2026-10-10T00:00:00Z", related_claim_ids: ["claim-1"], source_status: "available" }],
      quality_warnings: [],
    });
    render(<MemoryRouter initialEntries={["/tasks/research-1/report"]}><Routes><Route path="/tasks/:taskId/report" element={<ReportPage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("NPC 三电平")).toBeInTheDocument();
    expect(screen.getByText("400 V 母线，10 kHz")).toBeInTheDocument();
    expect(screen.getByText("结果受调制策略影响")).toBeInTheDocument();
    expect(screen.getByText("统一开关频率后测量 THD")).toBeInTheDocument();
  });
});
