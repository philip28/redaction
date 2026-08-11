"""Thin client for any OpenAI-compatible /chat/completions endpoint."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re

import httpx

from .config import Settings
from .i18n import DEFAULT_LANG, t
from .prompting import build_layered_messages, load_template

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    """One entity as the model reported it: a canonical spelling plus its other forms."""

    category: str
    value: str
    aliases: tuple[str, ...] | list[str] = ()

#: Language for messages raised by the module-level parsing helpers.
_lang = DEFAULT_LANG

#: Name of the layered YAML template in app/prompts/.
PROMPT_TEMPLATE = "entity_scan"


class LLMError(RuntimeError):
    pass


#: Reasoning models wrap their scratchpad in a tag before answering. Vendors disagree on
#: the name, so all three spellings are handled.
_THINK_CLOSE = re.compile(r".*?</(?:think|thinking|reasoning)>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<(?:think|thinking|reasoning)\b", re.IGNORECASE)


def remove_thinking(text: str) -> str:
    """Drop everything up to and including a closing reasoning tag.

    This has to happen before the JSON is located: a scratchpad routinely contains a
    brace ("the output should look like {...}"), and the first-brace-to-last-brace scan
    below would otherwise splice the model's musings into the payload.
    """
    cleaned, n = _THINK_CLOSE.subn("", text)
    if n:
        log.debug(
            "remove_thinking: stripped %d reasoning block(s), %d->%d chars",
            n,
            len(text),
            len(cleaned),
        )
        return cleaned
    if _THINK_OPEN.search(text):
        # Opened but never closed: the model ran out of budget mid-thought, so there is
        # no answer to salvage. Say why, instead of "did not return JSON".
        raise LLMError(t("llm_unclosed_think", _lang))
    return text


def _message_content(payload: object) -> str:
    """Pull the reply text out of a chat-completions response.

    Gateways commonly wrap the OpenAI body in an envelope, so one level of "data" is
    unwrapped before looking for choices. Both shapes work:
        {"choices": [...]}            and  {"data": {"choices": [...]}}
    """
    body = payload
    if isinstance(body, dict) and "choices" not in body and isinstance(body.get("data"), dict):
        body = body["data"]
    try:
        content = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(t("llm_bad_shape", _lang, body=json.dumps(payload)[:300])) from exc
    return content or ""


def _extract_json(raw: str) -> dict:
    text = remove_thinking(raw).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(t("llm_no_json", _lang))
    return json.loads(text[start : end + 1])


class LLMClient:
    def __init__(self, settings: Settings, lang: str = DEFAULT_LANG) -> None:
        self.settings = settings
        self.lang = lang
        self._semaphore = asyncio.Semaphore(max(1, settings.llm_concurrency))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.settings.llm_api_key.strip()
        name = self.settings.llm_auth_header.strip()
        if key and name:
            scheme = self.settings.llm_auth_scheme.strip()
            headers[name] = f"{scheme} {key}" if scheme else key
        # Applied last so LLM_EXTRA_HEADERS can override anything above.
        headers.update(self.settings.extra_headers)
        return headers

    async def _post(self, client: httpx.AsyncClient, chunk: str) -> str:
        payload: dict = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": build_layered_messages(
                load_template(PROMPT_TEMPLATE, self.settings.prompt_dir),
                {
                    "document_text": chunk,
                    "categories": ", ".join(self.settings.categories),
                },
            ),
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                return _message_content(response.json())
            except LLMError:
                # A malformed body is not transient - retrying just repeats it.
                raise
            except httpx.ConnectError as exc:
                # A refused connection means the endpoint is down, not busy. Retrying it
                # would make every document wait out the full timeout several times over.
                raise LLMError(
                    t("llm_unreachable", self.lang, url=self.settings.llm_base_url, error=exc)
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 401, 403, 404, 422):
                    raise LLMError(
                        t(
                            "llm_http_error",
                            self.lang,
                            status=exc.response.status_code,
                            body=exc.response.text[:200],
                        )
                    ) from exc
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as a job warning
                last_error = exc
            if attempt < self.settings.llm_max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(str(last_error))

    async def scan_chunks(self, chunks: list[str]) -> tuple[list["Finding"], list[str]]:
        """Scan text chunks concurrently. Returns (category, value) pairs and any warnings."""
        if not chunks:
            return [], []

        results: list[Finding] = []
        warnings: list[str] = []
        # A short connect timeout keeps a dead endpoint from stalling the whole job;
        # the long budget belongs to the read, where the model actually spends its time.
        timeout = httpx.Timeout(
            self.settings.llm_timeout,
            connect=min(10.0, self.settings.llm_timeout),
        )

        global _lang
        _lang = self.lang

        async with httpx.AsyncClient(timeout=timeout) as client:

            async def one(index: int, chunk: str) -> None:
                async with self._semaphore:
                    try:
                        raw = await self._post(client, chunk)
                        parsed = _extract_json(raw)
                    except (LLMError, json.JSONDecodeError) as exc:
                        log.warning("chunk %s failed: %s", index, exc)
                        warnings.append(
                            t(
                                "chunk_failed",
                                self.lang,
                                index=index + 1,
                                total=len(chunks),
                                error=exc,
                            )
                        )
                        return
                    for item in parsed.get("entities") or []:
                        if not isinstance(item, dict):
                            continue
                        value = str(item.get("text") or "").strip()
                        category = str(item.get("category") or "OTHER").strip().upper()
                        if not value:
                            continue
                        raw_aliases = item.get("aliases")
                        aliases = [
                            str(a).strip()
                            for a in (raw_aliases if isinstance(raw_aliases, list) else [])
                            if str(a).strip()
                        ]
                        results.append(Finding(category=category, value=value, aliases=aliases))

            await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks)))

        return results, warnings


def build_chunks(paragraphs: list[str], max_chars: int) -> list[str]:
    """Group paragraphs into prompt-sized chunks, dropping exact repeats.

    Spreadsheets repeat the same label hundreds of times; scanning each copy wastes
    tokens without finding anything new.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for paragraph in paragraphs:
        key = paragraph.strip()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        unique.append(key)

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in unique:
        piece = paragraph[:max_chars]
        if size + len(piece) > max_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
