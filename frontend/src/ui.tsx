// Small shared UI pieces: loading / error / job progress states, metric tiles, formatting.
import type { ReactNode } from "react";
import type { JobProgress } from "./api";

export const fmtInt = (n: number) => Math.round(n).toLocaleString("en-IN");
export const fmtKL = (litres: number) => `${fmtInt(litres / 1000)} kL`;
export const pct = (ratio: number, digits = 0) => `${(ratio * 100).toFixed(digits)}%`;

export function Loading({ what }: { what: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden /> Loading {what}…
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <strong>Something went wrong.</strong> {(error as Error)?.message ?? String(error)}
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/** Progress for an async AWS job; mentions the cold start after 10 s. */
export function JobStatus({ progress, label, coldHint }: { progress: JobProgress; label: string; coldHint: string }) {
  return (
    <div className="state progress" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <div>
        <div>
          {label} <span className="muted">({progress.elapsedS} s)</span>
        </div>
        {progress.elapsedS >= 10 && <div className="muted small">{coldHint}</div>}
      </div>
    </div>
  );
}

export function Metric({ label, value, sub, tone }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}

export function SimNote({ children }: { children: ReactNode }) {
  return <p className="simnote">{children}</p>;
}
