from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """All runtime configuration. Everything comes from .env - there is no database."""

    model_config = SettingsConfigDict(
        env_file=("../../.env", "../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (any OpenAI-compatible /chat/completions endpoint: vLLM, Ollama, LM Studio, ...)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    # How the key is presented. Default is the OpenAI convention
    # (Authorization: Bearer sk-...). For gateways that want a raw key header set
    # LLM_AUTH_HEADER=X-API-Key and leave LLM_AUTH_SCHEME empty.
    llm_auth_header: str = "Authorization"
    llm_auth_scheme: str = "Bearer"
    llm_model: str = "qwen2.5:14b-instruct"
    llm_timeout: float = 180.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_max_chunk_chars: int = 9000
    llm_concurrency: int = 3
    llm_max_retries: int = 2
    llm_json_mode: bool = False  # set true only if the endpoint supports response_format
    llm_extra_headers: str = "{}"  # raw JSON, e.g. {"X-Tenant":"audit"}
    #: Raw JSON merged into the request body. The place to switch off a reasoning model's
    #: thinking mode, which no prompt wording can reliably do:
    #:   Qwen3 on vLLM/SGLang: {"chat_template_kwargs": {"enable_thinking": false}}
    #: Also takes sampling knobs the OpenAI schema lacks, e.g. {"top_k": 20}.
    llm_extra_body: str = "{}"
    #: Which layered template in app/prompts/ to use. "entity_scan_lean" is a shorter
    #: variant for models that over-think the full one.
    llm_prompt_template: str = "entity_scan"

    # --- Detection
    entity_categories: str = "PERSON,ORG,LOCATION,EMAIL,PHONE,URL,ID_NUMBER,ACCOUNT,SYSTEM,OTHER"
    rule_detectors: str = "EMAIL,PHONE,URL,INN,OGRN,SNILS,IBAN,CARD"
    inflect_categories: str = "PERSON"  # categories that match Russian inflected forms by default
    case_sensitive: bool = True

    # --- Tags
    tag_prefix: str = "[["
    tag_suffix: str = "]]"

    # --- Storage / limits
    data_dir: Path = Path("./data")
    job_ttl_minutes: int = 240
    max_upload_mb: int = 50
    max_files_per_job: int = 25
    scrub_metadata: bool = True
    redacted_suffix: str = "__redacted"
    restored_suffix: str = "__restored"

    #: Fallback interface language when the browser sends no Accept-Language we know.
    default_language: str = "ru"

    #: Directory holding the layered prompt templates. Point it elsewhere to override
    #: the shipped prompts without editing the package.
    prompt_dir: str = ""

    # --- diagnostics
    #: INFO shows job progress and LLM calls. DEBUG adds every request, the prompt
    #: layers and raw model replies. Set VERBOSE=true as a shorthand for DEBUG.
    log_level: str = "INFO"
    verbose: bool = False
    #: Requests slower than this are logged as SLOW - the ones a proxy or browser may abandon.
    slow_request_ms: int = 2000
    #: Warn when the event loop is blocked this long. 0 disables the monitor.
    loop_lag_warn_ms: int = 400

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- derived helpers -------------------------------------------------
    @property
    def effective_log_level(self) -> str:
        return "DEBUG" if self.verbose else self.log_level.upper()

    @property
    def categories(self) -> list[str]:
        return _csv(self.entity_categories)

    @property
    def detectors(self) -> list[str]:
        return [d.upper() for d in _csv(self.rule_detectors)]

    @property
    def inflect_defaults(self) -> set[str]:
        return {c.upper() for c in _csv(self.inflect_categories)}

    @property
    def origins(self) -> list[str]:
        return _csv(self.cors_origins)

    @property
    def extra_body(self) -> dict:
        try:
            parsed = json.loads(self.llm_extra_body or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, AttributeError):
            return {}

    @property
    def extra_headers(self) -> dict[str, str]:
        try:
            parsed = json.loads(self.llm_extra_headers or "{}")
            return {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, AttributeError):
            return {}

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
