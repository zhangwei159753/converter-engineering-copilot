import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HypothesisTree from "./HypothesisTree";
import type { HypothesisBranch } from "../types";

const branches: HypothesisBranch[] = [{
  branch_id: "task-1-branch-1", task_id: "task-1", parent_branch_id: null,
  hypothesis: "检查制动回路。", mechanism_summary: "再生能量未能耗散。",
  supporting_evidence_ids: ["ev-1", "missing-ev"], opposing_evidence_ids: ["ev-2"],
  applicability: "direct", missing_fields: ["母线波形"], checks: ["检查制动电阻回路"],
  gate_status: "qualified", disposition: "retained", ordinal: 0,
}];

describe("HypothesisTree", () => {
  it("separates supporting and opposing evidence and flags unknown IDs", () => {
    render(<HypothesisTree branches={branches} evidenceIds={new Set(["ev-1", "ev-2"])} onEvidence={vi.fn()} />);
    expect(screen.getByText("检查制动回路。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ev-1" })).toBeInTheDocument();
    expect(screen.getByText("missing-ev · 来源缺失")).toBeInTheDocument();
    expect(screen.getByText("ev-2")).toBeInTheDocument();
    expect(screen.getByText("母线波形")).toBeInTheDocument();
  });

  it("opens known evidence and explains an empty hypothesis tree", () => {
    const onEvidence = vi.fn();
    render(<HypothesisTree branches={branches} evidenceIds={new Set(["ev-1", "ev-2"])} onEvidence={onEvidence} />);
    fireEvent.click(screen.getByRole("button", { name: "ev-1" }));
    expect(onEvidence).toHaveBeenCalledWith("ev-1");
    render(<HypothesisTree branches={[]} evidenceIds={new Set()} onEvidence={onEvidence} />);
    expect(screen.getByText("没有候选原因通过证据门禁")).toBeInTheDocument();
  });
});
