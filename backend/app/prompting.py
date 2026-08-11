"""Layered prompt assembly, ported from aura's `build_hierarchical_prompt`.

A single wall-of-text prompt with the document embedded in it degrades as the document
grows: rules stated once at the top compete for attention with thousands of tokens of
content, and by the time the model reaches the end it is answering from the text rather
than the instructions. Splitting the prompt into ordered layers - contract, rules, data,
examples, trigger - with an acknowledgement between each keeps the constraints in their
own turns and puts the execution trigger last, immediately before generation.

Templates are YAML so they can be tuned without touching code.
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

#: Fixed layer order. Missing or empty layers are skipped.
LAYER_SCHEMA = ["schema_contract", "business_rules", "context_data", "examples", "task"]

PROMPT_DIR = Path(__file__).parent / "prompts"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

ACKS = {
    "schema_contract": "Schema contract received. Awaiting business rules.",
    "business_rules": "Business rules understood. Ready for context or examples.",
    "context_data": "Context loaded and indexed internally. Awaiting examples or execution trigger.",
    "examples": "Examples received. Ready for the execution trigger.",
    "task": None,
}


class PromptError(RuntimeError):
    pass


@lru_cache(maxsize=8)
def load_template(name: str, directory: str | None = None) -> dict:
    path = Path(directory or PROMPT_DIR) / f"{name}.yaml"
    if not path.exists():
        raise PromptError(f"prompt template not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, list):  # aura ships templates as single-item lists
        data = data[0]
    if not isinstance(data, dict):
        raise PromptError(f"prompt template {path} is not a mapping")
    present = [k for k in LAYER_SCHEMA if str(data.get(k) or "").strip()]
    if not present:
        raise PromptError(f"prompt template {path} has no populated layers")
    log.info("load_template: %s -> layers %s", path.name, present)
    return data


def _substitute(content: str, inputs: dict, layer: str) -> str:
    """Fill {placeholders} by name.

    Deliberately not str.format(): the schema and example layers are full of JSON
    braces, and format() would raise on every one of them. Only names that look like
    {identifier} are touched, so a layer can contain literal JSON and a placeholder
    at the same time.
    """
    names = set(_PLACEHOLDER.findall(content))
    if not names:
        return content
    missing = names - set(inputs)
    if missing:
        raise PromptError(f"layer {layer!r} needs inputs {sorted(missing)}")
    for name in names:
        content = content.replace("{" + name + "}", str(inputs[name]))
    return content


def build_layered_messages(template: dict, inputs: dict | None = None) -> list[dict]:
    """Turn a layered template into a chat message list with [LAYER i/N] headers."""
    started = time.perf_counter()
    inputs = inputs or {}
    layers = [(k, str(template.get(k) or "")) for k in LAYER_SCHEMA
              if str(template.get(k) or "").strip()]

    messages: list[dict] = []
    for index, (key, content) in enumerate(layers, 1):
        content = _substitute(content, inputs, key)
        messages.append({"role": "user", "content": f"[LAYER {index}/{len(layers)}]\n{content}"})
        if index < len(layers) and ACKS.get(key):
            messages.append({"role": "assistant", "content": ACKS[key]})

    log.debug(
        "build_layered_messages: %d layer(s) -> %d message(s), %d chars in %.1fms",
        len(layers),
        len(messages),
        sum(len(m["content"]) for m in messages),
        (time.perf_counter() - started) * 1000,
    )
    return messages
