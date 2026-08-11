import { useState } from "react";
import { api, download, paths } from "../api";
import { useI18n } from "../i18n";
import { FileDrop } from "../components/FileDrop";
import type { Job } from "../types";

const ACCEPT = ".docx,.docm,.xlsx,.xlsm,.pptx,.pptm";

export function Deanonymize() {
  const { t, count } = useI18n();
  const [files, setFiles] = useState<File[]>([]);
  const [key, setKey] = useState<File[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const restore = async () => {
    if (!key[0]) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.restore(files, key[0]));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const startOver = async () => {
    if (job) await api.discard(job.id).catch(() => undefined);
    setJob(null);
    setFiles([]);
    setKey([]);
    setError(null);
  };

  const step = job ? 2 : 1;

  return (
    <>
      <ol className="steps">
        {[t("steps.uploadKey"), t("steps.collectOriginals")].map((label, index) => (
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

      {!job && (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>{t("restore.docsTitle")}</h2>
              <span className="eyebrow">{t("restore.docsHint")}</span>
            </div>
            <FileDrop
              accept={ACCEPT}
              files={files}
              onChange={setFiles}
              title={t("restore.dropDocs")}
              hint={t("restore.dropDocsHint")}
            />
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{t("restore.keyTitle")}</h2>
              <span className="eyebrow">{t("restore.keyHint")}</span>
            </div>
            <FileDrop
              accept=".json"
              multiple={false}
              files={key}
              onChange={setKey}
              title={t("restore.dropKey")}
              hint={t("restore.dropKeyHint")}
            />
          </div>

          <div className="actionbar">
            <span className="tally">
              {count(files.length, "document")} · {key.length ? key[0].name : t("restore.noKey")}
            </span>
            <button
              className="btn"
              disabled={!files.length || !key.length || busy}
              onClick={restore}
            >
              {busy ? t("restore.running") : t("restore.run")}
            </button>
          </div>
        </>
      )}

      {job && (
        <>
          {job.warnings.map((warning, index) => (
            <div className="notice" key={index}>
              {warning}
            </div>
          ))}
          <div className="panel">
            <div className="panel-head">
              <h2>{t("restore.resultTitle")}</h2>
              <span className="eyebrow">
                {t("restore.tagsReplaced", {
                  count: job.documents.reduce((sum, d) => sum + d.replacements, 0),
                })}
              </span>
            </div>
            <ul className="outputs">
              {job.documents.map((doc) => (
                <li key={doc.id}>
                  <span className="grow">{doc.output_name ?? doc.filename}</span>
                  <span className="hits">
                    {t("restore.restored", { count: doc.replacements })}
                  </span>
                  <button
                    className="btn-quiet"
                    onClick={() =>
                      download(paths.restoredDoc(job.id, doc.id), doc.output_name ?? doc.filename)
                    }
                  >
                    {t("result.download")}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="actionbar">
            <span className="tally">
              {job.unmapped_tags.length
                ? t("restore.someUnmapped", { count: job.unmapped_tags.length })
                : t("restore.allMatched")}
            </span>
            <div style={{ display: "flex", gap: 10 }}>
              <button className="btn-quiet" onClick={startOver}>
                {t("restore.again")}
              </button>
              <button
                className="btn"
                onClick={() => download(paths.restoredBundle(job.id), "restored.zip")}
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
