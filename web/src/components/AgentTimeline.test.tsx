import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentTimeline from "./AgentTimeline";
import type { AgentRun } from "../types";

const runs: AgentRun[] = [
  {
    agent_run_id: "run-1", task_id: "task-1", role: "coordinator", stage: "intake",
    status: "completed", parent_run_id: null, branch_id: null, input_refs: ["task-input"],
    output_refs: ["query:1"], summary: "生成有限检索计划。", error_code: null, retry_count: 0,
    started_at: "2026-10-10T10:00:00Z", completed_at: "2026-10-10T10:00:01Z",
  },
  {
    agent_run_id: "run-2", task_id: "task-1", role: "evidence_auditor", stage: "audit",
    status: "failed", parent_run_id: null, branch_id: "task-1-branch-1", input_refs: ["ev-1"],
    output_refs: [], summary: "该候选的证据审查未完成。", error_code: "agent_audit_failed", retry_count: 0,
    started_at: "2026-10-10T10:00:02Z", completed_at: "2026-10-10T10:00:03Z",
  },
  {
    agent_run_id: "run-3", task_id: "task-1", role: "hypothesis_analyst", stage: "hypothesize",
    status: "running", parent_run_id: null, branch_id: null, input_refs: ["ev-1"],
    output_refs: [], summary: "正在生成候选原因。", error_code: null, retry_count: 0,
    started_at: "2026-10-10T10:00:04Z", completed_at: null,
  },
  {
    agent_run_id: "run-4", task_id: "task-1", role: "report_synthesizer", stage: "synthesize",
    status: "incomplete", parent_run_id: null, branch_id: null, input_refs: [],
    output_refs: [], summary: "汇总阶段未完成。", error_code: null, retry_count: 0,
    started_at: "2026-10-10T10:00:05Z", completed_at: "2026-10-10T10:00:06Z",
  },
];

describe("AgentTimeline", () => {
  it("shows role lifecycle status and safe summary", () => {
    render(<AgentTimeline runs={runs} />);
    expect(screen.getByText("协调员")).toBeInTheDocument();
    expect(screen.getByText("证据审查员")).toBeInTheDocument();
    expect(screen.getByText("执行失败")).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();
    expect(screen.getByText("未完成")).toBeInTheDocument();
    expect(screen.getByText("该候选的证据审查未完成。")).toBeInTheDocument();
    expect(screen.queryByText(/agent_audit_failed/)).not.toBeInTheDocument();
  });

  it("shows an explicit empty state", () => {
    render(<AgentTimeline runs={[]} />);
    expect(screen.getByText("暂无 Agent 执行记录")).toBeInTheDocument();
  });
});
