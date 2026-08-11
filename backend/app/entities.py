"""Turning detected values into the regexes that actually do the replacement.

The LLM only *finds* candidates. Every substitution is done by a regex built here, so
the operation is deterministic, reviewable and reproducible - the same job run twice
produces byte-identical output.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

QUOTES = "«»\u201c\u201d\u2018\u2019\"'"
QUOTE_CLASS = r"[«»\u201c\u201d\u2018\u2019\"']"
DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015"
DASH_CLASS = r"[-\u2010-\u2015]"
CYRILLIC_ENDINGS = r"[а-яёА-ЯЁ]"
VOWEL_TAIL = "аеёиоуыэюяйьъ"

# Words an LLM sometimes returns as "organisations" or "names" but which are generic.
STOPWORDS = {
    "компания", "клиент", "заказчик", "исполнитель", "подрядчик", "сотрудник",
    "руководитель", "директор", "бухгалтер", "аудитор", "пользователь", "система",
    "общество", "организация", "отчет", "отчёт", "документ", "приложение",
    "company", "client", "customer", "vendor", "employee", "manager", "user",
    "system", "report", "document", "appendix", "n/a", "none", "unknown",
}


@dataclass
class Entity:
    """One distinct piece of sensitive data and every surface form it takes."""

    id: str
    category: str
    value: str
    variants: list[str] = field(default_factory=list)
    source: str = "llm"  # "llm" | "rule"
    inflect: bool = False
    selected: bool = True
    occurrences: dict[str, int] = field(default_factory=dict)  # document_id -> count
    tag: str | None = None

    @property
    def total_occurrences(self) -> int:
        return sum(self.occurrences.values())

    def all_forms(self) -> list[str]:
        forms = [self.value, *self.variants]
        seen: set[str] = set()
        unique: list[str] = []
        for form in forms:
            key = form.casefold()
            if form.strip() and key not in seen:
                seen.add(key)
                unique.append(form)
        return unique

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "value": self.value,
            "variants": self.variants,
            "source": self.source,
            "inflect": self.inflect,
            "selected": self.selected,
            "occurrences": self.occurrences,
            "total_occurrences": self.total_occurrences,
            "tag": self.tag,
        }


# --------------------------------------------------------------------------- normalising


def normalise(value: str) -> str:
    """Collapse whitespace and unify quote/dash variants for de-duplication."""
    text = unicodedata.normalize("NFKC", value)
    text = "".join('"' if ch in QUOTES else "-" if ch in DASHES else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def dedup_key(value: str) -> str:
    return normalise(value).casefold()


#: Legal forms that sit in front of (or behind) a company's actual name.
LEGAL_FORMS = {
    "ооо", "оао", "зао", "пао", "ао", "ип", "нао", "нко", "гуп", "муп", "фгуп",
    "пкф", "тд", "чоу", "ано", "фонд",
    "llc", "ltd", "limited", "inc", "corp", "co", "gmbh", "ag", "plc", "jsc",
    "pjsc", "sa", "sas", "bv", "nv", "oy", "ab", "as", "spa", "srl", "kft", "zoo",
}

_PUNCT = re.compile(r"[«»\u201c\u201d\u2018\u2019\"'.,;:()\[\]{}/\\|]+")


def canonical_key(value: str) -> str:
    """Key that ignores quoting, punctuation, case and spacing.

    ПАО "МегаФон", ПАО «МегаФон» and ПАО МегаФон are one organisation written three
    ways. dedup_key() unifies the quote *style* but keeps the quote characters, so it
    treats them as three findings - each getting its own tag, which is wrong in the
    output and confusing in review.
    """
    text = _PUNCT.sub(" ", normalise(value).casefold())
    text = text.replace("\u00a0", " ")
    return " ".join(text.split())


def strip_legal_form(value: str) -> str:
    """The bare name: 'ПАО «МегаФон»' -> 'мегафон'. Empty if nothing else remains."""
    tokens = [t for t in canonical_key(value).split() if t not in LEGAL_FORMS]
    return " ".join(tokens)


def has_legal_form(value: str) -> bool:
    return any(token in LEGAL_FORMS for token in canonical_key(value).split())


def is_cyrillic_word(token: str) -> bool:
    letters = [ch for ch in token if ch.isalpha()]
    if not letters:
        return False
    return all("\u0400" <= ch <= "\u04ff" for ch in letters)


def looks_generic(value: str) -> bool:
    text = normalise(value)
    if len(text) < 2:
        return True
    if text.casefold() in STOPWORDS:
        return True
    if not any(ch.isalnum() for ch in text):
        return True
    return False


# --------------------------------------------------------------------------- patterns


def _token_pattern(token: str, inflect: bool) -> str:
    pieces: list[str] = []
    for ch in token:
        if ch in QUOTES:
            pieces.append(QUOTE_CLASS)
        elif ch in DASHES:
            pieces.append(DASH_CLASS)
        else:
            pieces.append(re.escape(ch))

    if inflect and is_cyrillic_word(token) and len(token) >= 4:
        # Russian names decline: Иванов -> Иванова / Иванову / Ивановым,
        #                       Мария  -> Марии  / Марию  / Марией.
        if token[-1].lower() in VOWEL_TAIL:
            return "".join(pieces[:-1]) + CYRILLIC_ENDINGS + "{1,3}"
        return "".join(pieces) + CYRILLIC_ENDINGS + "{0,3}"
    return "".join(pieces)


def form_pattern(form: str, inflect: bool) -> str:
    """Regex for one surface form: whitespace-tolerant, quote-tolerant, optionally inflected."""
    tokens = [t for t in re.split(r"\s+", form.strip()) if t]
    if not tokens:
        return ""
    body = r"\s+".join(_token_pattern(token, inflect) for token in tokens)
    return rf"(?<!\w){body}(?!\w)"


def entity_pattern(entity: Entity) -> str:
    forms = sorted(entity.all_forms(), key=len, reverse=True)
    parts = [p for p in (form_pattern(f, entity.inflect) for f in forms) if p]
    return "(?:" + "|".join(parts) + ")" if parts else ""


def compile_entity(entity: Entity, case_sensitive: bool = True) -> re.Pattern[str] | None:
    pattern = entity_pattern(entity)
    if not pattern:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def build_master_pattern(
    entities: list[Entity], case_sensitive: bool = True
) -> tuple[re.Pattern[str] | None, dict[str, Entity]]:
    """One alternation over all entities, longest form first.

    A single pass means a value can never be replaced twice, and "Иванов" can never
    win over "Иванов Иван Иванович" - the longer alternative is tried first.
    """
    ranked = sorted(
        entities,
        key=lambda e: max((len(f) for f in e.all_forms()), default=0),
        reverse=True,
    )
    parts: list[str] = []
    lookup: dict[str, Entity] = {}
    for index, entity in enumerate(ranked):
        pattern = entity_pattern(entity)
        if not pattern:
            continue
        group = f"e{index}"
        parts.append(f"(?P<{group}>{pattern})")
        lookup[group] = entity
    if not parts:
        return None, {}
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile("|".join(parts), flags), lookup


# --------------------------------------------------------------------------- variants


def suggest_variants(value: str, category: str, corpus: str) -> list[str]:
    """Extra surface forms worth matching, kept only if they really occur in the text."""
    candidates: list[str] = []
    tokens = [t for t in re.split(r"\s+", normalise(value)) if t]

    if category.upper() == "PERSON" and len(tokens) == 3 and all(is_cyrillic_word(t) for t in tokens):
        surname, first, patronymic = tokens
        initials = f"{first[0]}.{patronymic[0]}."
        candidates += [
            f"{surname} {initials}",
            f"{initials} {surname}",
            f"{surname} {first[0]}. {patronymic[0]}.",
            f"{surname} {first}",
        ]
    elif category.upper() == "PERSON" and len(tokens) == 2:
        first, second = tokens
        candidates += [f"{second} {first}"]

    if category.upper() == "ORG":
        stripped = re.sub(rf"^(?:ООО|АО|ПАО|ЗАО|ИП|ОАО|LLC|Ltd|Inc|GmbH)\s+", "", normalise(value))
        stripped = stripped.strip(QUOTES + " ")
        if stripped and stripped != normalise(value) and len(stripped) >= 3:
            candidates.append(stripped)

    kept: list[str] = []
    for candidate in candidates:
        if dedup_key(candidate) == dedup_key(value):
            continue
        pattern = form_pattern(candidate, inflect=False)
        if pattern and re.search(pattern, corpus):
            kept.append(candidate)
    return kept


# --------------------------------------------------------------------------- rule detectors

RULES: dict[str, tuple[str, str]] = {
    # name: (category, pattern) - group "v" is the captured value when present
    "EMAIL": ("EMAIL", r"(?<![\w.])[\w.+-]+@[\w-]+\.[\w.-]*[a-zA-Z](?![\w-])"),
    "URL": ("URL", r"(?<!\S)(?:https?://|www\.)[^\s<>\"'»]+"),
    "PHONE": (
        "PHONE",
        r"(?<![\d\w])(?:\+7|8|\+\d{1,3})[\s\-(]*\d{2,4}[\s\-)]*\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2,3}(?![\d\w])",
    ),
    "INN": ("ID_NUMBER", r"(?:ИНН|INN)[\s:№.]*(?P<v>\d{10}|\d{12})(?!\d)"),
    "OGRN": ("ID_NUMBER", r"(?:ОГРН(?:ИП)?|OGRN)[\s:№.]*(?P<v>\d{13}|\d{15})(?!\d)"),
    "SNILS": ("ID_NUMBER", r"(?<!\d)\d{3}-\d{3}-\d{3}\s\d{2}(?!\d)"),
    "IBAN": ("ACCOUNT", r"(?<![A-Z0-9])[A-Z]{2}\d{2}[A-Z0-9]{11,30}(?![A-Z0-9])"),
    "CARD": ("ACCOUNT", r"(?<!\d)(?:\d{4}[ -]){3}\d{4}(?!\d)"),
}


def run_detectors(text: str, enabled: list[str]) -> list[tuple[str, str]]:
    """Deterministic finds that never need an LLM. Returns (category, value) pairs."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in enabled:
        rule = RULES.get(name.upper())
        if not rule:
            continue
        category, pattern = rule
        for match in re.finditer(pattern, text):
            value = (match.groupdict().get("v") or match.group(0)).strip(" .,;:")
            key = f"{category}:{dedup_key(value)}"
            if value and key not in seen:
                seen.add(key)
                found.append((category, value))
    return found


# --------------------------------------------------------------------------- merging


def _rank(entity: Entity) -> tuple:
    """Best representative of a duplicate group: the fullest, most official spelling."""
    return (
        has_legal_form(entity.value),          # ПАО «МегаФон» beats МегаФон
        len(normalise(entity.value)),          # then the longer form
        entity.source == "rule",               # then a deterministic find
    )


def _absorb(keeper: Entity, other: Entity) -> None:
    """Fold `other` into `keeper`, keeping every spelling as a matchable variant.

    Variants are de-duplicated by exact string, not by canonical key: matching is
    case-sensitive by default, so dropping «ИВАНОВ ИВАН ИВАНОВИЧ» because it shares a
    canonical key with «Иванов Иван Иванович» would silently stop replacing it.
    """
    known = set(keeper.all_forms())
    for form in other.all_forms():
        if form not in known:
            keeper.variants.append(form)
            known.add(form)
    keeper.inflect = keeper.inflect or other.inflect
    if keeper.source != "rule" and other.source == "rule":
        keeper.source = "rule"
    # A concrete category beats the catch-all.
    if keeper.category.upper() == "OTHER" and other.category.upper() != "OTHER":
        keeper.category = other.category


def merge_duplicates(entities: list[Entity]) -> list[Entity]:
    """Collapse spellings of the same thing into one entity, so it gets one tag.

    Four passes, each guarded against merging things that are merely similar:

      1. Same value ignoring quotes, punctuation, case and spacing. This is what makes
         ПАО "МегаФон", ПАО «МегаФон» and ПАО МегаФон one finding.
      2. A separate finding that is already a known variant of another - the model
         returning both «Иванов Иван Иванович» and «Иванов И.И.» - is folded in, but
         ONLY when exactly one entity claims that spelling.
      3. A bare company name is absorbed into its legal-form spelling, again only when
         exactly one candidate matches.
      4. Any spelling still claimed by two different entities is dropped from both.

    Passes 2-4 exist because a shared spelling is dangerous, not just untidy. Both
    ООО «Ромашка» and ПАО «Ромашка» generate the bare variant «Ромашка»; merging on it
    would hide one company behind the other's tag, and leaving it on both would let the
    master pattern award a bare mention to whichever sorted first. They are different
    legal entities, so the honest answer is to keep them apart and stop guessing at the
    bare mention.
    """
    ordered: list[Entity] = []
    by_value: dict[str, Entity] = {}

    # --- pass 1: same name, different punctuation
    for entity in sorted(entities, key=_rank, reverse=True):
        key = canonical_key(entity.value)
        keeper = by_value.get(key)
        if keeper is None:
            by_value[key] = entity
            ordered.append(entity)
        else:
            _absorb(keeper, entity)

    def variant_claims() -> dict[str, list[Entity]]:
        """Spelling -> entities that would match it, excluding their own value."""
        claims: dict[str, list[Entity]] = defaultdict(list)
        for item in ordered:
            for form in item.variants:
                key = canonical_key(form)
                if key and key != canonical_key(item.value):
                    claims[key].append(item)
        return claims

    # --- pass 2: a finding that is another finding's known variant
    absorbed: set[str] = set()
    claims = variant_claims()
    for entity in list(ordered):
        owners = [o for o in claims.get(canonical_key(entity.value), []) if o is not entity]
        if len(owners) == 1 and owners[0].id not in absorbed:
            _absorb(owners[0], entity)
            absorbed.add(entity.id)
    ordered = [e for e in ordered if e.id not in absorbed]

    # --- pass 3: bare company name into its single legal-form spelling
    by_bare: dict[str, list[Entity]] = defaultdict(list)
    for entity in ordered:
        if entity.category.upper() == "ORG" and has_legal_form(entity.value):
            by_bare[strip_legal_form(entity.value)].append(entity)

    absorbed = set()
    for entity in ordered:
        if entity.category.upper() != "ORG" or has_legal_form(entity.value):
            continue
        candidates = by_bare.get(canonical_key(entity.value), [])
        if len(candidates) == 1 and candidates[0] is not entity:
            _absorb(candidates[0], entity)
            absorbed.add(entity.id)
    ordered = [e for e in ordered if e.id not in absorbed]

    # --- pass 4: no spelling may belong to two entities
    contested = {key for key, owners in variant_claims().items() if len(owners) > 1}
    values = {canonical_key(e.value) for e in ordered}
    for entity in ordered:
        entity.variants = [
            form
            for form in entity.variants
            if canonical_key(form) not in contested or canonical_key(form) in values
        ]

    return ordered
