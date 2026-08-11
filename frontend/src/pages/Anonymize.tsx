import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, download, paths } from "../api";
import { useI18n } from "../i18n";
import { FileDrop, formatSize } from "../components/FileDrop";
import { EntityLedger } from "../components/EntityLedger";
import type { EntityPatch, Job, ServerConfig } from "../types";

const ACCEPT = ".docx,.docm,.xlsx,.xlsm,.pptx,.pptm";

/** Consecutive lost status polls before the scan is treated as unreachable. */
const MAX_POLL_MISSES = 4;

export function Anonymize({ config }: { config: ServerConfig | null }) {
  const { t, count } = useI18n();
  const [files, setFiles] = useState<File[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [docFilter, setDocFilter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const polling = useRef<number | null>(null);
  const pollMisses = useRef(0);

  const stopPolling = () => {
    if (polling.current) {
      window.clearInterval(polling.current);
      polling.current = null;
    }
  };
  useEffect(() => stopPolling, []);

  const step = !job ? 1 : job.status === "complete" ? 3 : 2;

  const scan = async () => {
    setError(null);
    setBusy(true);
    try {
      const created = await api.scan(files);
      setJob(created);
      stopPolling();
      pollMisses.current = 0;
      polling.current = window.setInterval(async () => {
        try {
          const next = await api.job(created.id);
          pollMisses.current = 0;
          setJob(next);
          if (next.status !== "analyzing" && next.status !== "pending") stopPolling();
        } catch (e) {
          // A single dropped poll is not a failed job - the server may simply have been
          // busy. Only give up once several in a row are lost.
          pollMisses.current += 1;
          console.warn(`[poll] miss ${pollMisses.current}/${MAX_POLL_MISSES}`, e);
          if (pollMisses.current >= MAX_POLL_MISSES) {
            stopPolling();
            setError(`${t("error.pollLost")} (${(e as Error).message})`);
          }
        }
      }, 1500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const patch = useCallback(
    async (patches: EntityPatch[]) => {
      if (!job) return;
      setBusy(true);
      try {
        setJob(await api.patchEntities(job.id, patches));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [job],
  );

  const redact = async () => {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.redact(job.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const startOver = async () => {
    if (job) await api.discard(job.id).catch(() => undefined);
    stopPolling();
    setJob(null);
    setFiles([]);
    setDocFilter(null);
    setError(null);
  };

  const selectedCount = useMemo(
    () => job?.entities.filter((e) => e.selected).length ?? 0,
    [job],
  );
  const selectedHits = useMemo(
    () =>
      job?.entities.filter((e) => e.selected).reduce((sum, e) => sum + e.total_occurrences, 0) ?? 0,
    [job],
  );

  return (
    <>
      <ol className="steps">
        {[t("steps.upload"), t("steps.review"), t("steps.collect")].map((label, index) => (
          <li
            className="step"
            key={label}
            data-state={step === index + 1 ? "active" : step > index + 1 ? "done" : "todo"}
          >
            <span className="num">{index + 1}</span>
            <span>{label}</span>
          </li>
        ))}
      </ol>

      {error && <div className="notice">{error}</div>}

      {/* ------------------------------------------------ step 1 */}
      {!job && (
        <div className="panel">
          <FileDrop
            accept={ACCEPT}
            files={files}
            onChange={setFiles}
            title={t("upload.title")}
            hint={
              config
                ? t("upload.hint", {
                    files: config.max_files_per_job,
                    mb: config.max_upload_mb,
                    minutes: config.job_ttl_minutes,
                  })
                : t("upload.hintShort")
            }
          />
          <div className="actionbar">
            <span className="tally">
              {files.length
                ? `${count(files.length, "file")} · ${formatSize(
                    files.reduce((sum, f) => sum + f.size, 0),
                  )}`
                : t("upload.none")}
            </span>
            <button className="btn" disabled={!files.length || busy} onClick={scan}>
              {busy ? t("upload.scanning") : t("upload.scan")}
            </button>
          </div>
        </div>
      )}

      {/* ------------------------------------------------ scanning */}
      {job && (job.status === "analyzing" || job.status === "pending") && (
        <div className="panel">
          <div className="working">{t("scan.working", { count: job.documents.length })}</div>
        </div>
      )}

      {job && job.status === "error" && (
        <div className="panel">
          <div className="notice">{job.error}</div>
          <button className="btn-quiet" onClick={startOver}>
            {t("error.startOver")}
          </button>
        </div>
      )}

      {/* ------------------------------------------------ step 2 */}
      {job && (job.status === "ready" || job.status === "redacting") && (
        <>
          {job.warnings.map((warning, index) => (
            <div className="notice info" key={index}>
              {warning}
            </div>
          ))}
          <div className="split">
            <div className="panel">
              <div className="panel-head">
                <h2>{t("review.documents")}</h2>
              </div>
              <ul className="rail">
                <li>
                  <button
                    className="railbtn"
                    aria-current={docFilter === null}
                    onClick={() => setDocFilter(null)}
                  >
                    <span className="label">{t("review.allDocuments")}</span>
                    <span className="count">{job.entities.length}</span>
                  </button>
                </li>
                {job.documents.map((doc) => (
                  <li key={doc.id}>
                    <button
                      className="railbtn"
                      aria-current={docFilter === doc.id}
                      onClick={() => setDocFilter(doc.id)}
                      title={doc.filename}
                    >
                      <span className="label">{doc.filename}</span>
                      <span className="count">
                        {job.entities.filter((e) => e.occurrences[doc.id]).length}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 0 }}>
                {t("review.sharedNote")}
              </p>
            </div>

            <div className="panel">
              <div className="panel-head">
                <h2>{t("review.findings")}</h2>
                <span className="eyebrow">{t("review.hint")}</span>
              </div>
              <EntityLedger
                entities={job.entities}
                documentId={docFilter}
                onPatch={patch}
                busy={busy}
              />
            </div>
          </div>

          <div className="actionbar">
            <span className="tally">
              <strong>{count(selectedCount, "value")}</strong> ·{" "}
              <strong>{count(selectedHits, "occurrence")}</strong>
            </span>
            <div style={{ display: "flex", gap: 10 }}>
              <button className="btn-quiet" onClick={startOver} disabled={busy}>
                {t("review.discard")}
              </button>
              <button className="btn" onClick={redact} disabled={busy || !selectedCount}>
                {busy ? t("review.working") : t("review.redact")}
              </button>
            </div>
          </div>
        </>
      )}

      {/* ------------------------------------------------ step 3 */}
      {job && job.status === "complete" && (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>{t("result.documents")}</h2>
              <span className="eyebrow">
                {t("result.replacements", {
                  count: job.documents.reduce((sum, d) => sum + d.replacements, 0),
                })}
              </span>
            </div>
            <ul className="outputs">
              {job.documents.map((doc) => (
                <li key={doc.id}>
                  <span className="grow">{doc.output_name ?? doc.filename}</span>
                  <span className="hits">
                    {t("result.replaced", { count: doc.replacements })}
                  </span>
                  <button
                    className="btn-quiet"
                    onClick={() =>
                      download(paths.redactedDoc(job.id, doc.id), doc.output_name ?? doc.filename)
                    }
                  >
                    {t("result.download")}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{t("result.keyTitle")}</h2>
              <span className="eyebrow">{t("result.keyHint")}</span>
            </div>
            <p style={{ marginTop: 0, color: "var(--muted)", fontSize: 13 }}>
              {t("result.keyWarning")}
            </p>
            <ul className="outputs">
              <li>
                <span className="grow">mapping.json</span>
                <span style={{ color: "var(--muted)" }}>{t("result.keyPurpose")}</span>
                <button
                  className="btn-quiet"
                  onClick={() => download(paths.mapping(job.id), "mapping.json")}
                >
                  {t("result.download")}
                </button>
              </li>
            </ul>
          </div>

          <div className="actionbar">
            <span className="tally">
              {t("result.ttl", { minutes: config?.job_ttl_minutes ?? 240 })}
            </span>
            <div style={{ display: "flex", gap: 10 }}>
              <button className="btn-quiet" onClick={startOver}>
                {t("result.again")}
              </button>
              <button
                className="btn"
                onClick={() => download(paths.bundle(job.id), "redacted.zip")}
              >
                {t("result.downloadAll")}
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
