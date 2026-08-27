import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export const LANGS = ["ru", "en"] as const;
export type Lang = (typeof LANGS)[number];

const STORAGE_KEY = "redactor.lang";

const ru = {
  "app.title": "Обезличивание документов",
  "app.lead":
    "Скройте имена и названия клиентов в документах Office, поработайте с ними безопасно, затем верните оригиналы. Форматирование, таблицы и диаграммы остаются нетронутыми.",
  "app.tab.redact": "Обезличить",
  "app.tab.restore": "Восстановить",
  "app.lang.label": "Язык интерфейса",
  "app.noLlm":
    "Языковая модель не настроена — отработают только детекторы по шаблонам (почта, телефоны, ИНН и подобное). Укажите LLM_BASE_URL и LLM_MODEL в .env, чтобы находить имена и компании.",

  "steps.upload": "Загрузка документов",
  "steps.review": "Проверка находок",
  "steps.collect": "Получение файлов",
  "steps.uploadKey": "Документы и ключ",
  "steps.collectOriginals": "Получение оригиналов",

  "drop.choose": "Выбрать файлы",
  "drop.remove": "Убрать",
  "drop.removeAria": "Убрать {name}",

  "upload.title": "Перетащите сюда файлы Word, Excel или PowerPoint",
  "upload.hint":
    "До {files} файлов по {mb} МБ. Файлы удаляются с сервера через {minutes} мин.",
  "upload.hintShort": "До 25 файлов.",
  "upload.none": "Файлы не выбраны",
  "upload.scan": "Анализировать документы",
  "upload.scanning": "Загрузка…",

  "scan.stage.queued": "Подготовка",
  "scan.stage.reading": "Чтение документа",
  "scan.stage.scanning": "Поиск данных",
  "scan.stage.matching": "Сопоставление найденного",
  "scan.stage.done": "Завершение",
  "scan.chunks": "фрагмент {done} из {total}",
  "scan.docs": "документ {done} из {total}",
  "scan.eta": "осталось примерно {time}",
  "scan.elapsed": "прошло {time}",
  "scan.stalledWarning":
    "Ответа от модели нет уже {time}. Задание не прервано — большие фрагменты обрабатываются долго. Подробности в журнале сервера.",
  "scan.sec": "{n} сек",
  "scan.min": "{n} мин",
  "scan.minSec": "{m} мин {s} сек",
  "scan.working":
    "Читаю документы ({count}) и ищу имена, организации и идентификаторы.",

  "review.documents": "Документы",
  "review.allDocuments": "Все документы",
  "review.sharedNote":
    "Значение получает одну и ту же метку во всех документах, поэтому выбор здесь действует на весь запуск.",
  "review.findings": "Находки",
  "review.hint": "Снимите отметку с того, что должно остаться читаемым",
  "review.filter": "Фильтр по значению или категории",
  "review.selectAll": "Отметить всё",
  "review.clearAll": "Снять всё",
  "review.preview": "Показать результат",
  "review.showValues": "Показать значения",
  "review.selectGroup": "Отметить группу",
  "review.clearGroup": "Снять группу",
  "review.empty":
    "Ничего чувствительного не найдено. Проверьте предупреждения анализа или добавьте значения вручную и запустите заново.",
  "review.noMatch": "Под фильтр ничего не подходит.",
  "review.willHide": "будет скрыто",
  "review.willKeep": "останется как есть",
  "review.originModel": "модель",
  "review.originRule": "шаблон",
  "review.declined": "ловит падежные формы",
  "review.adjust": "Настроить",
  "review.close": "Закрыть",
  "review.variantsLabel": "Другие написания (через запятую)",
  "review.variantsPlaceholder": "Иванов И.И., I. Ivanov",
  "review.inflect":
    "Ловить падежные окончания — найдёт «Иванову» и «Ивановым» наряду с «Иванов». Отключите, если задевает посторонние слова.",
  "review.redactAria": "Заменить {value}",
  "review.discard": "Отменить",
  "review.redact": "Заменить и собрать файлы",
  "review.working": "Обработка…",

  "result.documents": "Обезличенные документы",
  "result.replacements": "замен: {count}",
  "result.replaced": "заменено: {count}",
  "result.download": "Скачать",
  "result.keyTitle": "Файл ключа",
  "result.keyHint": "Храните отдельно от обезличенных документов",
  "result.keyWarning":
    "Тот, у кого есть и обезличенные файлы, и этот ключ, может снять обезличивание. Это единственный способ вернуть оригиналы.",
  "result.keyPurpose": "для восстановления",
  "result.ttl": "Всё удаляется с сервера через {minutes} мин.",
  "result.again": "Обезличить ещё файлы",
  "result.downloadAll": "Скачать всё",

  "restore.docsTitle": "Обезличенные документы",
  "restore.docsHint": "Правки, сделанные в обезличенном виде, сохранятся",
  "restore.dropDocs": "Перетащите сюда документы с метками",
  "restore.dropDocsHint": "Файлы Word, Excel и PowerPoint, в которых остались метки [[TAG]].",
  "restore.keyTitle": "Файл ключа",
  "restore.keyHint": "mapping.json",
  "restore.dropKey": "Перетащите сюда файл ключа",
  "restore.dropKeyHint": "Файл, выгруженный вместе с обезличенными документами.",
  "restore.noKey": "ключ не выбран",
  "restore.run": "Восстановить оригиналы",
  "restore.running": "Восстановление…",
  "restore.resultTitle": "Восстановленные документы",
  "restore.tagsReplaced": "меток заменено: {count}",
  "restore.restored": "восстановлено: {count}",
  "restore.allMatched": "Для всех найденных меток нашлось соответствие в ключе.",
  "restore.someUnmapped": "Меток без соответствия в ключе: {count}. Они оставлены на месте.",
  "restore.again": "Восстановить ещё файлы",

  "error.startOver": "Начать заново",
  "error.render": "Не удалось отобразить этот блок",
  "error.retry": "Показать снова",
  "error.serverDown":
    "Сервер не отвечает (за {ms} мс). Проверьте, что бэкенд запущен, и повторите — файлы задания на сервере сохранены.",
  "error.dropped":
    "Соединение оборвалось до ответа сервера (через {ms} мс). Повторите; если повторяется, посмотрите журнал бэкенда — там строка req= с тем же временем.",
  "error.pollLost":
    "Связь с сервером потеряна во время анализа. Задание могло продолжиться — обновите страницу или начните заново.",
};

const en: Record<keyof typeof ru, string> = {
  "app.title": "Redaction desk",
  "app.lead":
    "Hide client names in Office documents, work on them safely, then put the names back. Formatting, tables and charts stay exactly as they were.",
  "app.tab.redact": "Redact",
  "app.tab.restore": "Restore",
  "app.lang.label": "Interface language",
  "app.noLlm":
    "No language model is configured, so only pattern detectors (emails, phones, ИНН and the like) will run. Set LLM_BASE_URL and LLM_MODEL in .env to find names and companies.",

  "steps.upload": "Upload documents",
  "steps.review": "Review findings",
  "steps.collect": "Collect output",
  "steps.uploadKey": "Upload files and key",
  "steps.collectOriginals": "Collect originals",

  "drop.choose": "Choose files",
  "drop.remove": "Remove",
  "drop.removeAria": "Remove {name}",

  "upload.title": "Drop Word, Excel or PowerPoint files here",
  "upload.hint":
    "Up to {files} files, {mb} MB each. Files are deleted after {minutes} minutes.",
  "upload.hintShort": "Up to 25 files.",
  "upload.none": "No files yet",
  "upload.scan": "Scan documents",
  "upload.scanning": "Uploading…",

  "scan.stage.queued": "Preparing",
  "scan.stage.reading": "Reading document",
  "scan.stage.scanning": "Looking for sensitive data",
  "scan.stage.matching": "Matching findings",
  "scan.stage.done": "Finishing",
  "scan.chunks": "fragment {done} of {total}",
  "scan.docs": "document {done} of {total}",
  "scan.eta": "about {time} left",
  "scan.elapsed": "{time} elapsed",
  "scan.stalledWarning":
    "No response from the model for {time}. The job has not been cancelled — large fragments take a while. See the server log for detail.",
  "scan.sec": "{n} sec",
  "scan.min": "{n} min",
  "scan.minSec": "{m} min {s} sec",
  "scan.working":
    "Reading {count} document(s) and looking for names, organisations and identifiers.",

  "review.documents": "Documents",
  "review.allDocuments": "All documents",
  "review.sharedNote":
    "A value keeps one tag everywhere, so selecting it here applies to every document in this run.",
  "review.findings": "Findings",
  "review.hint": "Untick anything that should stay readable",
  "review.filter": "Filter by value or category",
  "review.selectAll": "Select all shown",
  "review.clearAll": "Clear all shown",
  "review.preview": "Preview redaction",
  "review.showValues": "Show values",
  "review.selectGroup": "Select group",
  "review.clearGroup": "Clear group",
  "review.empty":
    "Nothing sensitive was found. Check the scan warnings, or add values by hand in the document before running again.",
  "review.noMatch": "No values match that filter.",
  "review.willHide": "will be hidden",
  "review.willKeep": "kept as is",
  "review.originModel": "model",
  "review.originRule": "pattern",
  "review.declined": "declined forms matched",
  "review.adjust": "Adjust",
  "review.close": "Close",
  "review.variantsLabel": "Other spellings to catch (comma separated)",
  "review.variantsPlaceholder": "Иванов И.И., I. Ivanov",
  "review.inflect":
    "Match Russian case endings — catches Иванову and Ивановым as well as Иванов. Turn this off if it is catching unrelated words.",
  "review.redactAria": "Redact {value}",
  "review.discard": "Discard",
  "review.redact": "Redact and build files",
  "review.working": "Working…",

  "result.documents": "Redacted documents",
  "result.replacements": "{count} replacements",
  "result.replaced": "{count} replaced",
  "result.download": "Download",
  "result.keyTitle": "Key file",
  "result.keyHint": "Keep this separate from the redacted documents",
  "result.keyWarning":
    "Anyone holding both the redacted files and this key can reverse the redaction. It is the only way to restore the originals later.",
  "result.keyPurpose": "for restoring",
  "result.ttl": "Everything is deleted from the server after {minutes} minutes.",
  "result.again": "Redact more files",
  "result.downloadAll": "Download everything",

  "restore.docsTitle": "Redacted documents",
  "restore.docsHint": "Edits made while redacted are kept",
  "restore.dropDocs": "Drop the tagged documents here",
  "restore.dropDocsHint":
    "Word, Excel and PowerPoint files that still contain [[TAG]] placeholders.",
  "restore.keyTitle": "Key file",
  "restore.keyHint": "mapping.json",
  "restore.dropKey": "Drop the key file here",
  "restore.dropKeyHint": "The file produced alongside the redacted documents.",
  "restore.noKey": "no key yet",
  "restore.run": "Restore originals",
  "restore.running": "Restoring…",
  "restore.resultTitle": "Restored documents",
  "restore.tagsReplaced": "{count} tags replaced",
  "restore.restored": "{count} restored",
  "restore.allMatched": "Every tag found had a match in the key.",
  "restore.someUnmapped": "{count} tag(s) were not in the key and stayed in place.",
  "restore.again": "Restore more files",

  "error.startOver": "Start over",
  "error.render": "This part of the page could not be shown",
  "error.retry": "Try showing it again",
  "error.serverDown":
    "The server is not responding (after {ms} ms). Check that the backend is running and try again — the job's files are still on the server.",
  "error.dropped":
    "The connection dropped before the server answered (after {ms} ms). Try again; if it repeats, check the backend log for a req= line at the same moment.",
  "error.pollLost":
    "Lost contact with the server during the scan. The job may still be running — reload the page or start over.",
};

const DICTS: Record<Lang, Record<string, string>> = { ru, en };

export type TranslateKey = keyof typeof ru;

/** Russian needs three forms where English needs two. */
function ruPlural(n: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(n) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

const COUNTED: Record<string, { ru: [string, string, string]; en: [string, string] }> = {
  occurrence: {
    ru: ["совпадение", "совпадения", "совпадений"],
    en: ["occurrence", "occurrences"],
  },
  file: { ru: ["файл", "файла", "файлов"], en: ["file", "files"] },
  document: { ru: ["документ", "документа", "документов"], en: ["document", "documents"] },
  value: { ru: ["значение", "значения", "значений"], en: ["value", "values"] },
  form: { ru: ["форма", "формы", "форм"], en: ["form", "forms"] },
  tag: { ru: ["метка", "метки", "меток"], en: ["tag", "tags"] },
};

export interface I18n {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: TranslateKey, params?: Record<string, string | number>) => string;
  /** "3 файла" / "3 files" — the number plus its correctly inflected noun. */
  count: (n: number, noun: keyof typeof COUNTED) => string;
}

const Context = createContext<I18n | null>(null);

function readStored(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  return (LANGS as readonly string[]).includes(saved ?? "") ? (saved as Lang) : "ru";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStored);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang;
  }, [lang]);

  const t = useCallback(
    (key: TranslateKey, params?: Record<string, string | number>) => {
      const template = DICTS[lang][key] ?? DICTS.ru[key] ?? key;
      if (!params) return template;
      return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
        name in params ? String(params[name]) : whole,
      );
    },
    [lang],
  );

  const count = useCallback(
    (n: number, noun: keyof typeof COUNTED) => {
      const forms = COUNTED[noun];
      if (lang === "ru") return `${n} ${ruPlural(n, ...forms.ru)}`;
      return `${n} ${n === 1 ? forms.en[0] : forms.en[1]}`;
    },
    [lang],
  );

  const value = useMemo<I18n>(
    () => ({ lang, setLang: setLangState, t, count }),
    [lang, t, count],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(Context);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

/** Read outside React, so api.ts can set Accept-Language on every request. */
export function currentLang(): Lang {
  return readStored();
}
