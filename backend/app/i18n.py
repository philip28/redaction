"""Messages the user actually reads, in both interface languages.

Warnings and errors travel to the browser as finished strings rather than codes, so
the API shape stays plain. The language comes from the Accept-Language header the
frontend sends, and is remembered on the job so background work replies in the same
language.
"""

from __future__ import annotations

DEFAULT_LANG = "ru"
LANGS = ("ru", "en")

MESSAGES: dict[str, dict[str, str]] = {
    # --- request validation
    "missing_client_id": {
        "ru": "Отсутствует заголовок X-Client-Id.",
        "en": "Missing X-Client-Id header.",
    },
    "job_gone": {
        "ru": "Задание больше не существует — возможно, истёк срок хранения.",
        "en": "This job no longer exists. It may have expired.",
    },
    "no_files": {
        "ru": "Приложите хотя бы один документ.",
        "en": "Attach at least one document.",
    },
    "too_many_files": {
        "ru": "За один раз можно обработать не более {max} документов.",
        "en": "Up to {max} documents per run.",
    },
    "unsupported_format": {
        "ru": "{name}: формат не поддерживается. Загрузите .docx, .xlsx или .pptx "
              "(файлы .doc/.xls/.ppt сначала сохраните в современном формате).",
        "en": "{name}: unsupported format. Upload .docx, .xlsx or .pptx "
              "(save legacy .doc/.xls/.ppt in the modern format first).",
    },
    "not_ooxml": {
        "ru": "{name} не является корректным файлом Office. Файлы .doc/.xls/.ppt "
              "сначала нужно сохранить в современном формате.",
        "en": "{name} is not a valid Office file. Legacy .doc/.xls/.ppt must be saved as "
              "the modern format first.",
    },
    "file_too_large": {
        "ru": "{name} превышает {mb} МБ.",
        "en": "{name} is larger than {mb} MB.",
    },
    "file_empty": {
        "ru": "{name} пустой.",
        "en": "{name} is empty.",
    },
    # --- flow
    "scan_running": {
        "ru": "Анализ ещё выполняется.",
        "en": "The scan is still running.",
    },
    "nothing_selected": {
        "ru": "Ничего не выбрано. Отметьте хотя бы одно значение для замены.",
        "en": "Nothing is selected. Choose at least one item to redact.",
    },
    "no_pattern": {
        "ru": "Не удалось построить шаблон замены для текущего выбора.",
        "en": "Could not build a replacement pattern for the current selection.",
    },
    "redaction_failed": {
        "ru": "Не удалось выполнить замену.",
        "en": "Redaction failed.",
    },
    "restore_failed": {
        "ru": "Не удалось восстановить документы.",
        "en": "Restore failed.",
    },
    "file_not_ready": {
        "ru": "Файл ещё не готов.",
        "en": "That file is not ready yet.",
    },
    "doc_not_ready": {
        "ru": "Документ ещё не готов.",
        "en": "That document is not ready yet.",
    },
    # --- mapping / key file
    "mapping_json_only": {
        "ru": "Файл соответствий доступен только в формате JSON.",
        "en": "The key file is available as JSON only.",
    },
    "mapping_unreadable": {
        "ru": "Не удалось прочитать файл соответствий: {error}",
        "en": "Could not read the key file: {error}",
    },
    "mapping_empty": {
        "ru": "В файле соответствий нет ни одной метки.",
        "en": "The key file contains no tags.",
    },
    "mapping_not_json": {
        "ru": "Файл соответствий должен быть в формате .json. Ранее выгружался также "
              "mapping.csv, но Excel искажает в нём длинные числа и значения, "
              "начинающиеся со знака «=», поэтому восстановление из CSV отключено.",
        "en": "The key file must be .json. A mapping.csv used to be produced as well, but "
              "Excel corrupts long numbers and values starting with '=' in it, so restoring "
              "from CSV is no longer supported.",
    },
    "mapping_tag_mismatch": {
        "ru": "В файле соответствий используются метки {prefix}…{suffix}, а сервер "
              "настроен на {server_prefix}…{server_suffix}. Метки могут не найтись.",
        "en": "The key file uses {prefix}…{suffix} tags while this server is configured "
              "for {server_prefix}…{server_suffix}. Tags may not be found.",
    },
    # --- warnings raised during a run
    "no_llm": {
        "ru": "Языковая модель не настроена, отработали только детекторы по шаблонам. "
              "Укажите LLM_BASE_URL и LLM_MODEL в .env для полного покрытия.",
        "en": "No LLM endpoint is configured, so only rule-based detectors ran. "
              "Set LLM_BASE_URL and LLM_MODEL in .env for full coverage.",
    },
    "chunk_failed": {
        "ru": "Фрагмент {index} из {total} не удалось проанализировать: {error}",
        "en": "Chunk {index} of {total} could not be scanned: {error}",
    },
    "unmapped_tags": {
        "ru": "Меток без соответствия в файле ключа: {count}. Они оставлены как есть: {tags}",
        "en": "{count} tag(s) had no entry in the key file and were left in place: {tags}",
    },
    # --- LLM transport
    "llm_unreachable": {
        "ru": "не удалось подключиться к {url}: {error}",
        "en": "cannot reach {url}: {error}",
    },
    "llm_http_error": {
        "ru": "код {status} от эндпоинта модели: {body}",
        "en": "{status} from the model endpoint: {body}",
    },
    "llm_bad_shape": {
        "ru": "неожиданная структура ответа модели: {body}",
        "en": "unexpected response shape from the model endpoint: {body}",
    },
    "llm_no_json": {
        "ru": "модель вернула ответ без JSON",
        "en": "model did not return JSON",
    },
    "llm_unclosed_think": {
        "ru": "блок рассуждений модели не был закрыт — ответ обрезан; увеличьте "
              "LLM_MAX_TOKENS или уменьшите LLM_MAX_CHUNK_CHARS",
        "en": "the model's reasoning block was never closed, so the reply was cut off - "
              "raise LLM_MAX_TOKENS or lower LLM_MAX_CHUNK_CHARS",
    },
}


def normalise_lang(value: str | None) -> str:
    """Pick a supported language from an Accept-Language header value."""
    if not value:
        return DEFAULT_LANG
    for part in value.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **params: object) -> str:
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry[DEFAULT_LANG]
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template
