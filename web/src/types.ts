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
  completion_status: "completed" | "partial" | "failed";
}

export interface TaskAccepted {
  task: TaskRecord;
}

export interface CaseImportResult {
  document_ids: string[];
  chunk_count: number;
  imported_case_count: number;
}

export interface FaultAnalysisInput {
  equipment_type: string;
  symptom: string;
  operating_conditions: Record<string, string>;
  fault_code?: string;
}
