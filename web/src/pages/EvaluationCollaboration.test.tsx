import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import EvaluationPage from "./EvaluationPage";
import * as api from "../api/client";

vi.mock("../api/client", () => ({
  getEvaluationSummary: vi.fn().mockResolvedValue({ modes: {}, dataset_versions: [], retrieval_recall_at_k: null, manual_evidence_support_rate: null, metric_status: {}, metric_sample_counts: {} }),
  importReviewBundle: vi.fn(), listEvaluationExamples: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  saveEvaluationReview: vi.fn(), getEvaluationHistory: vi.fn().mockResolvedValue([]),
}));

describe("EvaluationPage agent collaboration summary", () => {
  it("imports paired strategy JSON and keeps human-quality metrics unavailable without labels", async () => {
    render(<MemoryRouter><EvaluationPage /></MemoryRouter>);
    const report = {
      mode: "fault_analysis", dataset_version: "sha256:demo-v3", dataset: "evals/synthetic_fault_quality_v1.jsonl",
      paired_comparison: {
        strategy_order: ["baseline", "collaborative"], paired_example_count: 20,
        metric_deltas_second_minus_first: { schema_pass_rate: 0.05, manual_evidence_support_rate: null },
        mean_latency_delta_ms_second_minus_first: 120,
        interpretation: "Synthetic fixtures validate workflow behavior only.",
      },
      strategies: Object.fromEntries(["baseline", "collaborative"].map((agent_strategy) => [agent_strategy, {
        workflow_version: "agent-collaboration-v1", agent_strategy, example_count: 20, synthetic_example_count: 20,
        schema_pass_rate: 1, retrieval_recall_at_k: null, citation_coverage: 0.8, citation_integrity_rate: 1,
        manual_evidence_support_rate: null, metric_sample_counts: { retrieval_labeled_examples: 0, reviewed_evidence_labels: 0 },
        branch_metrics: { evidence_id_integrity_rate: 1, support_opposition_coverage: 0.9, unsupported_candidate_share: 0.1, applicability_warning_share: 0.2, incomplete_review_share: 0.05, human_labeled_branch_quality: null, sample_counts: { branches: 60 } },
        agent_latency_ms: { mean: 85, sample_count: 60 }, agent_run_counts: { completed: 40, skipped: 2 },
        budget_exhaustion_count: 1, provider_usage_unavailable_reason: "Provider usage unavailable in fixture.",
      }])) ,
    };
    fireEvent.change(screen.getByLabelText("导入 Agent 离线评测结果"), {
      target: { files: [new File([JSON.stringify(report)], "comparison.json", { type: "application/json" })] },
    });
    expect(await screen.findByText(/配对样例 20 条/)).toBeInTheDocument();
    expect(screen.getByText("baseline")).toBeInTheDocument();
    expect(screen.getByText("collaborative")).toBeInTheDocument();
    expect(screen.getAllByText("不可用").length).toBeGreaterThan(0);
    expect(screen.getByText(/合成数据只验证工作流行为/)).toBeInTheDocument();
    expect(screen.getByText(/原始评测文件未上传到服务端/)).toBeInTheDocument();
    expect(api.importReviewBundle).not.toHaveBeenCalled();
  });
});
