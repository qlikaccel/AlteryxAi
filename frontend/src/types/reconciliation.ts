export type ValidationStatus = "pass" | "warn" | "fail" | "pending" | "not_applicable";

export type ValidationSeverity = "critical" | "high" | "medium" | "low";

export interface ValidationCheck {
  name: string;
  status: ValidationStatus;
  severity: ValidationSeverity;
  source_value: unknown;
  target_value: unknown;
  details: string;
}

export interface ReconciliationReport {
  source_name: string;
  target_name: string;
  status: ValidationStatus;
  accuracy_score: number;
  checks: ValidationCheck[];
}

export interface ValidationSummary {
  total: number;
  passed: number;
  pending: number;
  notApplicable: number;
  warned: number;
  failed: number;
  criticalFailed: number;
}
