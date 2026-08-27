"""Thin client for any OpenAI-compatible /chat/completions endpoint."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import time
import re

import httpx

from .config import Settings
from .i18n import DEFAULT_LANG, t
from .prompting import build_layered_messages, load_template

log = logging.getLogger(__name__)


def _preview(text: str, limit: int = 200) -> str:
    """Collapse whitespace and truncate, so a log line stays one line."""
    if not isinstance(text, str):
        return repr(text)[:limit]
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


@dataclass(frozen=True)
class Finding:
    """One entity as the model reported it: a canonical spelling plus its other forms."""

    category: str
    value: str
    aliases: tuple[str, ...] | list[str] = ()

class LLMError(RuntimeError):
    """A failed model call.

    Carries the message key and its parameters rather than a finished sentence, because
    the two audiences need different languages: the log is always English (it is grepped,
    pasted into issues and read by whoever operates the service), while the warning shown
    in the interface follows the language the job was started in. Translating at the raise
    site would force one language on both.
    """

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(t(code, "en", **params))

    def localised(self, lang: str) -> str:
        return t(self.code, lang, **self.params)


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
        raise LLMError("llm_unclosed_think")
    return text


def _finish_reason(payload: object) -> str | None:
    body = payload
    if isinstance(body, dict) and "choices" not in body and isinstance(body.get("data"), dict):
        body = body["data"]
    try:
        return body["choices"][0].get("finish_reason")  # type: ignore[index]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


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
        raise LLMError("llm_bad_shape", body=json.dumps(payload)[:300]) from exc
    return content or ""


def _extract_json(raw: str) -> dict:
    text = remove_thinking(raw).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("llm_no_json")
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

    async def _post(self, client: httpx.AsyncClient, chunk: str, tag: str = "-") -> str:
        payload: dict = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": build_layered_messages(
                load_template(self.settings.llm_prompt_template, self.settings.prompt_dir),
                {
                    "document_text": chunk,
                    "categories": ", ".join(self.settings.categories),
                },
            ),
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        # Merged last so it can override anything above - notably chat_template_kwargs,
        # which is how a reasoning model's thinking mode is actually switched off.
        payload.update(self.settings.extra_body)

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        messages = payload["messages"]
        prompt_chars = sum(len(m["content"]) for m in messages)
        log.info(
            "llm[%s] -> POST %s model=%s messages=%d prompt=%d chars (document %d chars) "
            "temp=%.2f max_tokens=%d",
            tag, url, self.settings.llm_model, len(messages), prompt_chars, len(chunk),
            self.settings.llm_temperature, self.settings.llm_max_tokens,
        )
        if log.isEnabledFor(logging.DEBUG):
            for i, message in enumerate(messages, 1):
                log.debug(
                    "llm[%s] message %d/%d role=%s %d chars: %s",
                    tag, i, len(messages), message["role"], len(message["content"]),
                    _preview(message["content"]),
                )

        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                body = response.json()
                took = time.perf_counter() - started
                content = _message_content(body)
                usage = body.get("usage") or (body.get("data") or {}).get("usage") or {}
                reasoning_tokens = (
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                    or usage.get("reasoning_tokens")
                )
                if reasoning_tokens:
                    log.info(
                        "llm[%s] %s of %s completion tokens went on reasoning - set "
                        'LLM_EXTRA_BODY={"chat_template_kwargs":{"enable_thinking":false}} '
                        "to switch it off",
                        tag, reasoning_tokens, usage.get("completion_tokens") or "?",
                    )
                finish = _finish_reason(body)
                if finish == "length":
                    # The reply was cut off mid-generation, so any JSON at the end never
                    # arrived. Without this the failure reads as "no JSON", which points
                    # at the prompt rather than at the token budget.
                    log.warning(
                        "llm[%s] hit the token limit (finish_reason=length, "
                        "max_tokens=%d, %d completion tokens) - the reply was truncated "
                        "before the JSON. Lower LLM_MAX_CHUNK_CHARS or raise LLM_MAX_TOKENS.",
                        tag, self.settings.llm_max_tokens,
                        usage.get("completion_tokens") or -1,
                    )
                log.info(
                    "llm[%s] <- %s in %.1fs, %d chars%s%s",
                    tag, response.status_code, took, len(content),
                    (
                        f", tokens prompt={usage.get('prompt_tokens')} "
                        f"completion={usage.get('completion_tokens')}"
                        if usage else ""
                    ),
                    f" (attempt {attempt + 1})" if attempt else "",
                )
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("llm[%s] raw reply: %s", tag, _preview(content, 600))
                elif not content.strip():
                    log.warning("llm[%s] reply was empty after %.1fs", tag, took)
                return content
            except LLMError as exc:
                # A malformed body is not transient - retrying just repeats it.
                log.error("llm[%s] unusable reply after %.1fs: %s",
                          tag, time.perf_counter() - started, exc)
                raise
            except httpx.ConnectError as exc:
                log.error("llm[%s] cannot connect to %s after %.1fs: %s",
                          tag, self.settings.llm_base_url, time.perf_counter() - started, exc)
                # A refused connection means the endpoint is down, not busy. Retrying it
                # would make every document wait out the full timeout several times over.
                raise LLMError(
                    "llm_unreachable", url=self.settings.llm_base_url, error=exc
                ) from exc
            except httpx.HTTPStatusError as exc:
                log.warning("llm[%s] HTTP %s after %.1fs: %s", tag,
                            exc.response.status_code, time.perf_counter() - started,
                            _preview(exc.response.text, 300))
                if exc.response.status_code in (400, 401, 403, 404, 422):
                    raise LLMError(
                        "llm_http_error",
                        status=exc.response.status_code,
                        body=exc.response.text[:200],
                    ) from exc
                last_error = exc
            except httpx.TimeoutException as exc:
                log.warning(
                    "llm[%s] timed out after %.1fs (LLM_TIMEOUT=%.0fs) - the model may still "
                    "be generating; raise the timeout or lower LLM_MAX_CHUNK_CHARS",
                    tag, time.perf_counter() - started, self.settings.llm_timeout,
                )
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as a job warning
                log.warning("llm[%s] %s after %.1fs: %s", tag, type(exc).__name__,
                            time.perf_counter() - started, exc)
                last_error = exc
            if attempt < self.settings.llm_max_retries:
                delay = 1.5 * (attempt + 1)
                log.info("llm[%s] retrying in %.1fs (attempt %d/%d)", tag, delay,
                         attempt + 2, self.settings.llm_max_retries + 1)
                await asyncio.sleep(delay)
        raise LLMError("llm_failed", error=last_error)

    async def scan_chunks(
        self,
        chunks: list[str],
        label: str = "scan",
        on_chunk: "Callable[[int, int], None] | None" = None,
    ) -> tuple[list["Finding"], list[str]]:
        """Scan text chunks concurrently. Returns findings and any warnings.

        `label` names the document in the log, so concurrent work stays readable.
        `on_chunk(done, total)` fires as each chunk finishes - including failed ones, so
        the caller's progress never sticks on a chunk that will not arrive.
        """
        if not chunks:
            return [], []

        log.info(
            "%s: scanning %d chunk(s), %d chars total, concurrency=%d",
            label, len(chunks), sum(len(c) for c in chunks), self.settings.llm_concurrency,
        )
        finished = 0
        started_all = time.perf_counter()

        results: list[Finding] = []
        warnings: list[str] = []
        # A short connect timeout keeps a dead endpoint from stalling the whole job;
        # the long budget belongs to the read, where the model actually spends its time.
        timeout = httpx.Timeout(
            self.settings.llm_timeout,
            connect=min(10.0, self.settings.llm_timeout),
        )

        async with httpx.AsyncClient(timeout=timeout) as client:

            async def one(index: int, chunk: str) -> None:
                nonlocal finished
                tag = f"{label} {index + 1}/{len(chunks)}"
                async with self._semaphore:
                    raw = ""
                    try:
                        raw = await self._post(client, chunk, tag=tag)
                        parsed = _extract_json(raw)
                    except (LLMError, json.JSONDecodeError) as exc:
                        finished += 1
                        # Include the reply itself: "no JSON" alone cannot distinguish a
                        # refusal from a truncated answer from a wrong model being served.
                        # Logged in English; the warning attached to the job below is
                        # rendered in the language the job was started in.
                        log.warning(
                            "%s: FAILED (%d/%d complete): %s%s",
                            tag, finished, len(chunks), exc,
                            f" | reply was: {_preview(raw, 300)}" if raw else "",
                        )
                        if on_chunk:
                            on_chunk(finished, len(chunks))
                        warnings.append(
                            t(
                                "chunk_failed",
                                self.lang,
                                index=index + 1,
                                total=len(chunks),
                                error=(
                                    exc.localised(self.lang)
                                    if isinstance(exc, LLMError)
                                    else exc
                                ),
                            )
                        )
                        return
                    found = 0
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
                        found += 1

                finished += 1
                log.info(
                    "%s: done (%d/%d complete, %d entity/ies here, %.0fs elapsed)",
                    tag, finished, len(chunks), found, time.perf_counter() - started_all,
                )
                if on_chunk:
                    on_chunk(finished, len(chunks))

            await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks)))

        log.info(
            "%s: all %d chunk(s) in %.1fs, %d raw finding(s), %d warning(s)",
            label, len(chunks), time.perf_counter() - started_all, len(results), len(warnings),
        )
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
