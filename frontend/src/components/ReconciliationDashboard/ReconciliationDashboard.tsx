import { useMemo, useState } from "react";
import type {
  ReconciliationReport,
  ValidationCheck,
  ValidationStatus,
  ValidationSummary,
} from "../../types/reconciliation";
import "./ReconciliationDashboard.css";

interface ReconciliationDashboardProps {
  report: ReconciliationReport;
  explanation?: string;
  onExplain?: () => void;
  explainLoading?: boolean;
}

const statusLabels: Record<ValidationStatus, string> = {
  pass: "Pass",
  warn: "Warn",
  fail: "Fail",
  pending: "Pending",
  not_applicable: "N/A",
};

export function ReconciliationDashboard({
  report,
  explanation,
  onExplain,
  explainLoading = false,
}: ReconciliationDashboardProps) {
  const [filter, setFilter] = useState<ValidationStatus | "all">("all");
  const summary = useMemo(() => summarizeChecks(report.checks), [report.checks]);
  const filteredChecks = useMemo(
    () => report.checks.filter((check) => filter === "all" || check.status === filter),
    [filter, report.checks],
  );

  return (
    <section className="recon-shell" aria-label="Data reconciliation">
      <header className="recon-header">
        <div>
          <p className="recon-kicker">Data reconciliation</p>
          <h1>{report.source_name} to {report.target_name}</h1>
        </div>
        <div className={`recon-score recon-score--${report.status}`}>
          <span>{statusLabels[report.status]}</span>
          <strong>{report.status === "pending" ? "N/A" : `${report.accuracy_score.toFixed(2)}%`}</strong>
        </div>
      </header>

      <div className="recon-metrics" aria-label="Validation summary">
        <Metric label="Checks" value={summary.total} />
        <Metric label="Passed" value={summary.passed} tone="pass" />
        <Metric label="Pending" value={summary.pending} tone="pending" />
        <Metric label="Warnings" value={summary.warned} tone="warn" />
        <Metric label="Failed" value={summary.failed} tone="fail" />
        <Metric label="Critical failures" value={summary.criticalFailed} tone="fail" />
      </div>

      <div className="recon-toolbar" role="toolbar" aria-label="Filter validation checks">
        {(["all", "pending", "fail", "warn", "pass", "not_applicable"] as const).map((item) => (
          <button
            key={item}
            type="button"
            className={filter === item ? "active" : ""}
            onClick={() => setFilter(item)}
          >
            {item === "all" ? "All" : statusLabels[item]}
          </button>
        ))}
        {onExplain && (
          <button
            type="button"
            className="recon-explain-button"
            onClick={onExplain}
            disabled={explainLoading}
          >
            {explainLoading ? "Explaining" : "Explain gaps"}
          </button>
        )}
      </div>

      <div className="recon-table-wrap">
        <table className="recon-table">
          <thead>
            <tr>
              <th>Check</th>
              <th>Status</th>
              <th>Severity</th>
              <th>Source</th>
              <th>Target</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {filteredChecks.map((check) => (
              <CheckRow key={`${check.name}-${check.severity}`} check={check} />
            ))}
          </tbody>
        </table>
      </div>

      {explanation && (
        <aside className="recon-explanation">
          <h2>Investigation notes</h2>
          <p>{explanation}</p>
        </aside>
      )}
    </section>
  );
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`recon-metric recon-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CheckRow({ check }: { check: ValidationCheck }) {
  return (
    <tr>
      <td>{formatCheckName(check.name)}</td>
      <td><span className={`recon-status recon-status--${check.status}`}>{statusLabels[check.status]}</span></td>
      <td>{check.severity}</td>
      <td>{formatValue(check.source_value)}</td>
      <td>{formatValue(check.target_value)}</td>
      <td>{check.details}</td>
    </tr>
  );
}

function summarizeChecks(checks: ValidationCheck[]): ValidationSummary {
  return checks.reduce(
    (summary, check) => {
      summary.total += 1;
      if (check.status === "pass") summary.passed += 1;
      if (check.status === "pending") summary.pending += 1;
      if (check.status === "warn") summary.warned += 1;
      if (check.status === "fail") summary.failed += 1;
      if (check.status === "not_applicable") summary.notApplicable += 1;
      if (check.status === "fail" && check.severity === "critical") summary.criticalFailed += 1;
      return summary;
    },
    { total: 0, passed: 0, pending: 0, notApplicable: 0, warned: 0, failed: 0, criticalFailed: 0 },
  );
}

function formatCheckName(name: string) {
  return name.replace(/\./g, " / ").replace(/_/g, " ");
}

function formatValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (value === null || value === undefined || value === "") {
    return "Unavailable";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
