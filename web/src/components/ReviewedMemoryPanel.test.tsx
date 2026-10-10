import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { searchMemory } from "../api/client";
import ReviewedMemoryPanel from "./ReviewedMemoryPanel";

vi.mock("../api/client", () => ({ searchMemory: vi.fn() }));

describe("ReviewedMemoryPanel", () => {
  beforeEach(() => vi.mocked(searchMemory).mockReset());

  it("shows accepted source provenance, applicability limits and navigable evidence", async () => {
    vi.mocked(searchMemory).mockResolvedValue({ synthetic_demo: false, items: [{
      equipment_type: "CV-300", symptom: "直流母线过压", cause: "制动回路异常",
      source_work_order_id: "WO-REVIEWED-9", source_task_id: "task-reviewed-9", source_review_version: 2,
      evidence_ids: ["ev-reviewed-9"], applicability_differences: ["Equipment model differs from the reviewed source case."], synthetic_demo: false,
    }] });
    render(<MemoryRouter><ReviewedMemoryPanel workOrderId="WO-CURRENT" equipmentType="CV-300" symptom="直流母线过压" /></MemoryRouter>);
    expect(await screen.findByText("制动回路异常")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.className === "wo-memory-provenance" && element.textContent?.includes("WO-REVIEWED-9") === true)).toBeInTheDocument();
    expect(screen.getByText(/设备型号与已复核案例不同/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /ev-reviewed-9/ })).toHaveAttribute("href", "/tasks/task-reviewed-9/report?workOrderId=WO-REVIEWED-9");
    expect(screen.getByText(/检索相关性仅用于查找历史记录/)).toBeInTheDocument();
    expect(searchMemory).toHaveBeenCalledWith("直流母线过压", "CV-300", 5);
  });

  it("explains when no accepted memory matches", async () => {
    vi.mocked(searchMemory).mockResolvedValue({ synthetic_demo: false, items: [] });
    render(<MemoryRouter><ReviewedMemoryPanel workOrderId="WO-EMPTY" equipmentType="CV-300" symptom="启动失败" /></MemoryRouter>);
    expect(await screen.findByText(/暂无符合条件的已复核历史案例/)).toBeInTheDocument();
  });

  it("does not display synthetic matches even if an upstream response is misconfigured", async () => {
    vi.mocked(searchMemory).mockResolvedValue({ synthetic_demo: true, items: [{
      equipment_type: "CV-300", symptom: "启动失败", cause: "合成原因",
      source_work_order_id: "WO-SYNTHETIC", source_task_id: "task-synthetic", source_review_version: 1,
      evidence_ids: ["ev-synthetic"], applicability_differences: [], synthetic_demo: true,
    }] } as never);
    render(<MemoryRouter><ReviewedMemoryPanel workOrderId="WO-SYNTH" equipmentType="CV-300" symptom="启动失败" /></MemoryRouter>);
    expect(await screen.findByText(/暂无符合条件的已复核历史案例/)).toBeInTheDocument();
    expect(screen.queryByText("合成原因")).not.toBeInTheDocument();
  });
});
