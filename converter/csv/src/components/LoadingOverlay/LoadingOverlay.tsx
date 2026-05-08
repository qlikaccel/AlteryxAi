import "./LoadingOverlay.css";
import { useEffect, useState } from "react";

interface LoadingOverlayProps {
  isVisible: boolean;
  message?: string;
  onTimerUpdate?: (elapsed: string) => void;
}

export default function LoadingOverlay({
  isVisible,
  message = "Publishing your dataset to Power BI...",
  onTimerUpdate,
}: LoadingOverlayProps) {
  const [elapsed, setElapsed] = useState("00m:00s:00ms");

  useEffect(() => {
    if (!isVisible) return;

    const startTime = Date.now();

    const interval = setInterval(() => {
      const now = Date.now();
      const msTotal = now - startTime;

      const minutes = Math.floor(msTotal / 60000);
      const seconds = Math.floor((msTotal % 60000) / 1000);
      const centis = Math.floor((msTotal % 1000) / 10);

      const pad = (n: number, width = 2) => String(n).padStart(width, "0");
      const formatted = `${pad(minutes)}m:${pad(seconds)}s:${pad(centis)}ms`;

      setElapsed(formatted);
      onTimerUpdate?.(formatted);
    }, 10); // Update frequently for smooth animation

    return () => clearInterval(interval);
  }, [isVisible, onTimerUpdate]);

  if (!isVisible) return null;

  return (
    <div className="loading-overlay">
      <div className="loading-container">
        {/* Animated Hourglass */}
        <div className="loading-icon">
          <div className="hourglass">
            <div className="hourglass-top"></div>
            <div className="hourglass-middle"></div>
            <div className="hourglass-bottom"></div>
          </div>
        </div>

        {/* Message */}
        <h2 className="loading-message">{message}</h2>

        {/* Sub-text */}
        <p className="loading-subtext">This may take a few moments.</p>

        {/* Timer Badge */}
        <div className="loading-timer-badge">
          <span className="timer-icon">⏱️</span>
          <span className="timer-label">Analysing time:</span>
          <span className="timer-value">{elapsed}</span>
        </div>
      </div>
    </div>
  );
}
