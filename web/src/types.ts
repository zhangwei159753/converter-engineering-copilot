export type TaskMode = "fault_analysis" | "technical_research" | "case_retrieval";
export type TaskStatus = "queued" | "running" | "completed" | "partial" | "failed";
export type ClaimStatus = "supported" | "conflicting" | "unverified";

export interface TaskRecord {
  task_id: string;
  mode: TaskMode;
  status: TaskStatus;
  inputs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  attempt: number;
  max_attempts: number;
  lease_expires_at: string | null;
  error_code: string | null;
}

export interface TaskEvent {
  event_id: number;
  task_id: string;
  stage: string;
  status: string;
  message: string;
  created_at: string;
}

export type AgentRole = "coordinator" | "evidence_researcher" | "hypothesis_analyst" | "evidence_auditor" | "report_synthesizer";
export type AgentRunStatus = "queued" | "running" | "completed" | "failed" | "skipped" | "incomplete";

export interface AgentRun {
  agent_run_id: string;
  task_id: string;
  role: AgentRole;
  stage: string;
  status: AgentRunStatus;
  parent_run_id: string | null;
  branch_id: string | null;
  input_refs: string[];
  output_refs: string[];
  summary: string;
  error_code: string | null;
  retry_count: number;
  started_at: string;
  completed_at: string | null;
}

export interface HypothesisBranch {
  branch_id: string;
  task_id: string;
  parent_branch_id: string | null;
  hypothesis: string;
  mechanism_summary: string;
  supporting_evidence_ids: string[];
  opposing_evidence_ids: string[];
  applicability: "direct" | "related" | "cross_equipment" | "unknown";
  missing_fields: string[];
  checks: string[];
  gate_status: "pending" | "qualified" | "blocked" | "review_incomplete";
  disposition: "retained" | "omitted" | "needs_more_information";
  ordinal: number;
}

export interface Evidence {
  evidence_id: string;
  task_id: string;
  title: string;
  source_type: string;
  excerpt: string;
  url?: string | null;
  document_id?: string | null;
  locator?: string | null;
  published_at?: string | null;
  accessed_at: string;
  related_claim_ids: string[];
  source_status: "available" | "conflicting" | "unavailable";
  equipment_type?: string | null;
  applicability?: "direct" | "related" | "cross_equipment" | "unknown";
}

export interface Claim {
  claim_id: string;
  text: string;
  claim_type: "fact" | "inference" | "recommendation";
  evidence_ids: string[];
  verification_status: ClaimStatus;
}

export interface FaultHypothesis {
  hypothesis: string;
  mechanism: string;
  confidence: "low" | "medium" | "high";
  confidence_basis: string;
  supporting_evidence_ids: string[];
  opposing_evidence_ids: string[];
  checks: string[];
}

export interface ConverterReport {
  task_id: string;
  mode: TaskMode;
  result: {
    hypotheses?: FaultHypothesis[];
    checks?: string[];
    comparisons?: Array<Record<string, unknown>>;
    limitations?: string[];
    experiment_steps?: string[];
    matches?: Array<Record<string, unknown>>;
  };
  claims: Claim[];
  evidence: Evidence[];
  quality_warnings: string[];
  clarification_questions?: string[];
  completion_status: "completed" | "partial" | "failed";
}

export interface TaskAccepted {
  task: TaskRecord;
}

export interface WorkOrder {
  work_order_id: string;
  equipment_model: string;
  serial_number: string;
  firmware_version: string | null;
  alarm_code: string;
  symptom: string;
  occurred_at: string;
  operating_conditions: Record<string, string | number | null>;
  attachment_names: string[];
  missing_fields: string[];
  severity: "low" | "medium" | "high";
  status: "open" | "investigating" | "resolved";
  synthetic_demo: true;
  analysis_count?: number;
}

export interface WorkOrderReview {
  work_order_id: string;
  task_id: string;
  version: number;
  reviewer: string;
  verdict: "accepted" | "needs_more_information" | "rejected";
  note: string | null;
  reviewed_at: string;
}

export interface WorkOrderAnalysis {
  task: TaskRecord;
  report: ConverterReport | null;
  review_history: WorkOrderReview[];
  latest_review: WorkOrderReview | null;
}

export interface WorkOrderDetail {
  work_order: WorkOrder;
  analyses: WorkOrderAnalysis[];
  synthetic_demo: true;
}

export interface MemoryMatch {
  equipment_type: string;
  symptom: string;
  cause: string;
  source_work_order_id: string;
  source_task_id: string;
  source_review_version: number;
  evidence_ids: string[];
  applicability_differences: string[];
  synthetic_demo: boolean;
}

export interface CaseImportResult {
  document_ids: string[];
  chunk_count: number;
  imported_case_count: number;
  import_id: string;
  reused: boolean;
}

export type ImportStatus = "validated" | "indexing" | "complete" | "failed" | "needs_repair" | "deleting" | "deleted";

export interface ImportSummary {
  import_id: string;
  document_id: string;
  source_name: string;
  file_type: string;
  parser_version: string;
  chunker_version: string;
  expected_chunk_count: number;
  indexed_chunks: number;
  imported_cases: number;
  status: ImportStatus;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  synthetic_demo: boolean;
}

export type EvidenceReviewLabel = "supported" | "unsupported" | "unverified";
export interface EvaluationEvidence { evidence_id: string; excerpt?: string; source_type?: string; locator?: string | null; url?: string | null; source_status?: string; related_claim_ids?: string[]; equipment_type?: string | null; applicability?: "direct" | "related" | "cross_equipment" | "unknown" }
export interface EvaluationClaim { claim_id: string; text: string; evidence_ids?: string[] }
export interface EvaluationExample {
  review_id: string; bundle_id: string; example_id: string; mode: string; dataset_version: string;
  source_type: string; inputs: Record<string, unknown>; task_id: string; claims: EvaluationClaim[];
  evidence: EvaluationEvidence[]; status: "pending" | "reviewed"; reviewer_id: string | null;
  output_context?: { completion_status?: string; quality_warnings?: string[]; clarification_questions?: string[]; failure?: { error_code?: string; error_type?: string; field_paths?: string[] } };
  reviewed_at: string | null; label_version: number;
  evidence_labels: Array<{ evidence_id: string; claim_id: string; label: EvidenceReviewLabel }>;
  expected_retrieval_ids: string[]; note: string | null;
}
export interface EvaluationSummary {
  modes: Record<string, { total: number; pending: number; reviewed: number; source_types: Record<string, number> }>;
  dataset_versions: Array<{ mode: string; dataset_version: string }>;
  retrieval_recall_at_k: number | null; manual_evidence_support_rate: number | null;
  metric_status: Record<string, string>; metric_sample_counts: Record<string, number>;
}
export interface EvaluationReviewEvent {
  review_id: string; label_version: number; reviewer_id: string; reviewed_at: string;
  evidence_labels: EvaluationExample["evidence_labels"]; expected_retrieval_ids: string[]; note: string | null;
}

export interface FaultAnalysisInput {
  equipment_type: string;
  symptom: string;
  operating_conditions: Record<string, string>;
  fault_code?: string;
}
