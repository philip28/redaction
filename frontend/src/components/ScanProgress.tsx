import { useI18n } from "../i18n";
import type { JobProgress } from "../types";

/** Past this many seconds without an observable change, say so rather than look frozen. */
const STALL_WARNING_SECONDS = 90;

function useDuration() {
  const { t } = useI18n();
  return (seconds: number): string => {
    const total = Math.max(Math.round(seconds), 0);
    if (total < 60) return t("scan.sec", { n: total });
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    return rest ? t("scan.minSec", { m: minutes, s: rest }) : t("scan.min", { n: minutes });
  };
}

export function ScanProgress({
  progress,
  documentCount,
}: {
  progress: JobProgress | undefined;
  documentCount: number;
}) {
  const { t } = useI18n();
  const duration = useDuration();

  // A backend older than this field sends no `progress`. Reading through it would throw
  // during render, and an unhandled throw unmounts the whole React tree - a blank page
  // rather than a missing bar. Fall back to the indeterminate message instead.
  if (!progress) {
    return <div className="working">{t("scan.working", { count: documentCount })}</div>;
  }

  // Chunks are the finer measure and where the time actually goes; documents alone jump
  // from 0% to 50% on a two-file job. Within a document, weight by chunks so the bar
  // advances steadily instead of standing still through a long scan.
  const docs = progress.documents_total || documentCount || 1;
  const withinDoc =
    progress.chunks_total > 0 ? progress.chunks_done / progress.chunks_total : 0;
  const fraction =
    progress.stage === "done"
      ? 1
      : Math.min((progress.documents_done + withinDoc) / docs, 0.99);
  const percent = Math.round(fraction * 100);

  const stalled = progress.stalled_seconds >= STALL_WARNING_SECONDS;

  return (
    <div className="progress" aria-live="polite">
      <div className="progress-head">
        <span className="progress-stage">{t(`scan.stage.${progress.stage}`)}</span>
        <span className="progress-percent mono">{percent}%</span>
      </div>

      {progress.document && <div className="progress-doc">{progress.document}</div>}

      <div
        className="progress-track"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="progress-meta">
        {progress.chunks_total > 1 && (
          <span className="mono">
            {t("scan.chunks", { done: progress.chunks_done, total: progress.chunks_total })}
          </span>
        )}
        {progress.documents_total > 1 && (
          <span className="mono">
            {t("scan.docs", {
              done: progress.documents_done,
              total: progress.documents_total,
            })}
          </span>
        )}
        <span>{t("scan.elapsed", { time: duration(progress.elapsed_seconds) })}</span>
        {progress.eta_seconds !== null && progress.eta_seconds > 1 && (
          <span>{t("scan.eta", { time: duration(progress.eta_seconds) })}</span>
        )}
      </div>

      {stalled && (
        <div className="progress-stall">
          {t("scan.stalledWarning", { time: duration(progress.stalled_seconds) })}
        </div>
      )}
    </div>
  );
}
