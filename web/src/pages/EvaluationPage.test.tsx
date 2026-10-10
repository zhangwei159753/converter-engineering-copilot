import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvaluationPage from "./EvaluationPage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({
  getEvaluationSummary: vi.fn(), importReviewBundle: vi.fn(), listEvaluationExamples: vi.fn(),
  saveEvaluationReview: vi.fn(), getEvaluationHistory: vi.fn(),
}));

const example = {
  review_id: "review-1", bundle_id: "bundle-1", example_id: "example-1", mode: "fault_analysis",
  dataset_version: "v1", source_type: "synthetic_demo_fixture", inputs: { symptom: "overvoltage" },
  task_id: "task-1", status: "pending" as const, reviewer_id: null, reviewed_at: null, label_version: 0,
  claims: [{ claim_id: "claim-1", text: "检查直流母线采样链路", evidence_ids: ["ev-1"] }],
  evidence: [{ evidence_id: "ev-1", excerpt: "设备手册摘录内容", locator: "第 3 页", source_type: "local_document" }],
  evidence_labels: [], expected_retrieval_ids: [], note: null,
};
const summary = {
  modes: { fault_analysis: { total: 1, pending: 1, reviewed: 0, source_types: { synthetic_demo_fixture: 1 } } },
  dataset_versions: [{ mode: "fault_analysis", dataset_version: "v1" }],
  retrieval_recall_at_k: null, manual_evidence_support_rate: null,
  metric_status: { retrieval_recall_at_k: "insufficient_labels", manual_evidence_support_rate: "insufficient_labels" },
  metric_sample_counts: { retrieval_labeled_examples: 0, reviewed_evidence_labels: 0 },
};

describe("EvaluationPage", () => {
  beforeEach(() => {
    vi.mocked(api.getEvaluationSummary).mockResolvedValue(summary);
    vi.mocked(api.listEvaluationExamples).mockResolvedValue({ items: [example], total: 1 });
    vi.mocked(api.getEvaluationHistory).mockResolvedValue([]);
  });

  it("shows explicit insufficient-label status and evidence snapshots", async () => {
    render(<MemoryRouter><EvaluationPage /></MemoryRouter>);
    expect(await screen.findByText("检查直流母线采样链路")).toBeInTheDocument();
    expect(screen.getByText("设备手册摘录内容")).toBeInTheDocument();
    expect(screen.getByText(/检索 0 条 · 证据 0 条/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "无法判断" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("来源类型"), { target: { value: "synthetic_demo_fixture" } });
    await waitFor(() => expect(api.listEvaluationExamples).toHaveBeenLastCalledWith(expect.objectContaining({ source_type: "synthetic_demo_fixture" })));
  });

  it("imports a review bundle and saves reviewer labels with history", async () => {
    vi.mocked(api.importReviewBundle).mockResolvedValue({ bundle_id: "bundle-1", imported_count: 1, skipped_count: 0, reused: false });
    vi.mocked(api.saveEvaluationReview).mockResolvedValue({
      review_id: "review-1", label_version: 1, reviewer_id: "reviewer-1", reviewed_at: "2026-10-10T00:00:00Z",
      evidence_labels: [{ evidence_id: "ev-1", claim_id: "claim-1", label: "supported" }], expected_retrieval_ids: ["ev-1"], note: null,
    });
    vi.mocked(api.getEvaluationHistory).mockResolvedValue([{
      review_id: "review-1", label_version: 1, reviewer_id: "reviewer-1", reviewed_at: "2026-10-10T00:00:00Z",
      evidence_labels: [{ evidence_id: "ev-1", claim_id: "claim-1", label: "supported" }], expected_retrieval_ids: ["ev-1"], note: null,
    }]);
    render(<MemoryRouter><EvaluationPage /></MemoryRouter>);
    const input = screen.getByLabelText("导入 review bundle");
    fireEvent.change(input, { target: { files: [new File([JSON.stringify({ bundle_id: "bundle-1" })], "bundle.json", { type: "application/json" })] } });
    expect(await screen.findByText("已导入 1 条待复核样例。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "支持" }));
    fireEvent.change(screen.getByLabelText("复核人"), { target: { value: "reviewer-1" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /保存复核/ }));
    await waitFor(() => expect(api.saveEvaluationReview).toHaveBeenCalledWith("review-1", expect.objectContaining({ reviewer_id: "reviewer-1" })));
    expect(await screen.findByText(/v1 · reviewer-1/)).toBeInTheDocument();
  });
});
