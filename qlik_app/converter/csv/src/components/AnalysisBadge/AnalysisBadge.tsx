import "./AnalysisBadge.css";
import { useWizard } from "../../context/WizardContext";
import type { FC } from "react";

const AnalysisBadge: FC<{ target: string }> = ({ target }) => {
  const { getLastElapsed } = useWizard();
  const last = getLastElapsed?.(target) || null;

  if (!last) return null;

  return <div className="analysis-badge">AnalysisTime - {last}</div>;
};

export default AnalysisBadge;
