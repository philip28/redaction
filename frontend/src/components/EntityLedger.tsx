import { useMemo, useState } from "react";
import { useI18n } from "../i18n";
import type { EntityInfo, EntityPatch } from "../types";

interface Props {
  entities: EntityInfo[];
  documentId: string | null;
  onPatch: (patches: EntityPatch[]) => void;
  busy: boolean;
}

export function EntityLedger({ entities, documentId, onPatch, busy }: Props) {
  const { t, count } = useI18n();
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return entities.filter((entity) => {
      if (documentId && !entity.occurrences[documentId]) return false;
      if (!needle) return true;
      return (
        entity.value.toLowerCase().includes(needle) ||
        entity.category.toLowerCase().includes(needle) ||
        entity.variants.some((v) => v.toLowerCase().includes(needle))
      );
    });
  }, [entities, documentId, query]);

  const grouped = useMemo(() => {
    const map = new Map<string, EntityInfo[]>();
    visible.forEach((entity) => {
      const list = map.get(entity.category) ?? [];
      list.push(entity);
      map.set(entity.category, list);
    });
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [visible]);

  const hits = (entity: EntityInfo) =>
    documentId ? entity.occurrences[documentId] ?? 0 : entity.total_occurrences;

  const openDetail = (entity: EntityInfo) => {
    if (openId === entity.id) {
      setOpenId(null);
      return;
    }
    setOpenId(entity.id);
    setDraft(entity.variants.join(", "));
  };

  const saveVariants = (entity: EntityInfo) => {
    const variants = draft
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    if (variants.join("|") !== entity.variants.join("|")) {
      onPatch([{ id: entity.id, variants }]);
    }
  };

  if (!entities.length) {
    return <div className="empty">{t("review.empty")}</div>;
  }

  return (
    <div className="ledger" data-preview={preview}>
      <div className="toolbar">
        <input
          className="searchbox"
          type="search"
          value={query}
          placeholder={t("review.filter")}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="button"
          className="btn-quiet"
          disabled={busy || !visible.length}
          onClick={() => onPatch(visible.map((e) => ({ id: e.id, selected: true })))}
        >
          {t("review.selectAll")}
        </button>
        <button
          type="button"
          className="btn-quiet"
          disabled={busy || !visible.length}
          onClick={() => onPatch(visible.map((e) => ({ id: e.id, selected: false })))}
        >
          {t("review.clearAll")}
        </button>
        <button
          type="button"
          className="btn-quiet"
          aria-pressed={preview}
          onClick={() => setPreview((on) => !on)}
        >
          {preview ? t("review.showValues") : t("review.preview")}
        </button>
      </div>

      {!visible.length && <div className="empty">{t("review.noMatch")}</div>}

      {grouped.map(([category, items]) => (
        <section className="category" key={category}>
          <div className="category-head">
            <span className="eyebrow">
              {category} · {items.length}
            </span>
            <button
              type="button"
              className="btn-link"
              disabled={busy}
              onClick={() =>
                onPatch(
                  items.map((e) => ({
                    id: e.id,
                    selected: !items.every((i) => i.selected),
                  })),
                )
              }
            >
              {items.every((i) => i.selected) ? t("review.clearGroup") : t("review.selectGroup")}
            </button>
          </div>

          {items.map((entity) => (
            <div className="entity" key={entity.id} data-selected={entity.selected}>
              <div className="entity-main">
                <input
                  type="checkbox"
                  checked={entity.selected}
                  disabled={busy}
                  aria-label={t("review.redactAria", { value: entity.value })}
                  onChange={(e) => onPatch([{ id: entity.id, selected: e.target.checked }])}
                />
                <div className="entity-body">
                  <span className="mark">{entity.value}</span>
                  <div className="entity-meta">
                    <span className="status">
                      {entity.selected ? t("review.willHide") : t("review.willKeep")}
                    </span>
                    <span className="hits">{count(hits(entity), "occurrence")}</span>
                    <span className="origin">
                      {entity.source === "rule" ? t("review.originRule") : t("review.originModel")}
                    </span>
                    {entity.inflect && <span>{t("review.declined")}</span>}
                    {entity.variants.length > 0 && (
                      <span>+{count(entity.variants.length, "form")}</span>
                    )}
                    <button type="button" className="btn-link" onClick={() => openDetail(entity)}>
                      {openId === entity.id ? t("review.close") : t("review.adjust")}
                    </button>
                  </div>
                </div>
              </div>

              {openId === entity.id && (
                <div className="detail">
                  <label className="eyebrow" htmlFor={`variants-${entity.id}`}>
                    {t("review.variantsLabel")}
                  </label>
                  <input
                    id={`variants-${entity.id}`}
                    type="text"
                    value={draft}
                    disabled={busy}
                    placeholder={t("review.variantsPlaceholder")}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => saveVariants(entity)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveVariants(entity);
                    }}
                  />
                  <label className="checkline">
                    <input
                      type="checkbox"
                      checked={entity.inflect}
                      disabled={busy}
                      onChange={(e) => onPatch([{ id: entity.id, inflect: e.target.checked }])}
                    />
                    <span>{t("review.inflect")}</span>
                  </label>
                </div>
              )}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
