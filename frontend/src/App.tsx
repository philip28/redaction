import { useEffect, useState } from "react";
import { api, setNetworkMessenger } from "./api";
import { LANGS, useI18n } from "./i18n";
import type { Lang } from "./i18n";
import { Anonymize } from "./pages/Anonymize";
import { Deanonymize } from "./pages/Deanonymize";
import type { ServerConfig } from "./types";

type Mode = "anonymize" | "deanonymize";

const LANG_LABEL: Record<Lang, string> = { ru: "Рус", en: "Eng" };

export default function App() {
  const { t, lang, setLang } = useI18n();
  const [mode, setMode] = useState<Mode>("anonymize");
  const [config, setConfig] = useState<ServerConfig | null>(null);

  // Network failures are raised inside api.ts, which has no access to the React tree.
  useEffect(() => {
    setNetworkMessenger((kind, ms) =>
      t(kind === "down" ? "error.serverDown" : "error.dropped", { ms }),
    );
  }, [t]);

  // Refetched on language change so anything the server phrases arrives translated.
  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, [lang]);

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1>{t("app.title")}</h1>
          <p>{t("app.lead")}</p>
        </div>
        <div className="masthead-actions">
          <div className="langswitch" role="group" aria-label={t("app.lang.label")}>
            {LANGS.map((code) => (
              <button
                key={code}
                type="button"
                className="lang"
                aria-pressed={lang === code}
                onClick={() => setLang(code)}
              >
                {LANG_LABEL[code]}
              </button>
            ))}
          </div>
          <div className="tabs" role="tablist" aria-label={t("app.title")}>
            <button
              className="tab"
              role="tab"
              aria-selected={mode === "anonymize"}
              onClick={() => setMode("anonymize")}
            >
              {t("app.tab.redact")}
            </button>
            <button
              className="tab"
              role="tab"
              aria-selected={mode === "deanonymize"}
              onClick={() => setMode("deanonymize")}
            >
              {t("app.tab.restore")}
            </button>
          </div>
        </div>
      </header>

      {config && !config.llm_configured && (
        <div className="notice" style={{ marginTop: 20 }}>
          {t("app.noLlm")}
        </div>
      )}

      <main style={{ marginTop: 24 }}>
        {mode === "anonymize" ? <Anonymize config={config} /> : <Deanonymize />}
      </main>
    </div>
  );
}
