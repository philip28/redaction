# Redaction desk

Hide client names in Word, Excel and PowerPoint documents, work on them safely, then put
the names back. FastAPI + React, no database.

## How it works

**Redact.** Upload one or more `.docx` / `.xlsx` / `.pptx` files. The service reads the text,
sends it to your LLM to find people, organisations and other confidential values, and also
runs deterministic detectors (email, phone, ИНН, ОГРН, СНИЛС, IBAN, card numbers). You get a
grouped list — one row per distinct value with its occurrence count, not one row per
occurrence — and untick anything that should stay readable. Every selected value is then
replaced by regex with a unique tag like `[[PERSON_001]]`. You download the redacted files
plus a key file that maps each tag back to its original text.

**Restore.** Upload the tagged documents and the key file. Tags are found by regex and swapped
back for the originals. Edits made while the document was redacted are preserved.

The LLM only *finds* candidates. Every substitution is a regex, so the operation is
deterministic and reviewable, and the model never rewrites the document.

## Running it

```bash
cp .env.example .env      # then set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173, proxies /api to :8000
```

For production, `npm run build` and serve `frontend/dist` from any static host on the same
origin as the API, or keep them split and set `CORS_ORIGINS`.

Run the backend from `backend/` so the relative `DATA_DIR` resolves as expected.

## Configuration

Everything lives in `.env` — see `.env.example` for the full annotated list. The essentials:

| Variable | Purpose |
| --- | --- |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Any OpenAI-compatible `/chat/completions` endpoint: vLLM, Ollama, LM Studio, an internal gateway |
| `LLM_MAX_CHUNK_CHARS` | Characters of document text per request — lower it for small context windows |
| `RULE_DETECTORS` | Pattern detectors that run with or without an LLM |
| `INFLECT_CATEGORIES` | Categories that match Russian case endings by default |
| `TAG_PREFIX`, `TAG_SUFFIX` | Tag delimiters, `[[` and `]]` by default |
| `JOB_TTL_MINUTES` | How long uploads survive on disk |
| `SCRUB_METADATA` | Also clear document author, last-modified-by and comment/revision names |

With no LLM configured the service still runs, using pattern detectors only, and says so in
the interface.

## Multi-user without login

Each browser generates a random client id, keeps it in `localStorage` and sends it as
`X-Client-Id`. Jobs are scoped to that id, so concurrent users never see each other's
documents. Job state is in memory, uploaded bytes are on disk under `DATA_DIR/<job_id>`, and
everything is deleted after `JOB_TTL_MINUTES`. Restarting the server clears all jobs — that is
the intended trade-off for having no database.

## Things worth knowing

**Split runs.** Word stores an edited sentence as several runs — `Ива` + `н Ив` + `анов` — so a
per-node regex finds almost nothing. `app/ooxml.py` groups text nodes by their logical
paragraph, matches across the joined text, and writes the result back, putting the tag in the
run where the match began so its formatting survives. Only the XML parts that contain text are
rewritten; images, charts, pivot tables, styles and numbering are copied through byte for byte.
The same engine does the restore, so a tag still resolves even if someone's edits split it.

**Russian inflection.** `Иванов` also appears as `Иванову`, `Ивановым`, `Иванова`. For
categories in `INFLECT_CATEGORIES` the generated pattern allows up to three trailing Cyrillic
letters, which catches declined forms without reaching `Ивановский`. It is a per-value toggle
in the review screen — turn it off if it over-matches.

**Overlaps.** All selected values compile into a single alternation ordered longest first, and
replacement is one pass. So `Иванов Иван Иванович` wins over `Иванов`, and nothing is ever
replaced twice.

**Selection is per value, not per document.** A value keeps the same tag across every document
in a run, which is what makes multi-document sets consistent. Unticking it applies everywhere.

**Excel.** Cell text, shared strings, inline strings, comments, chart labels and shapes are
covered. Numbers and formulas are untouched. Sheet names and string literals inside formulas
are deliberately left alone, since renaming them breaks references.

**Legacy formats.** `.doc`, `.xls` and `.ppt` are not OOXML and are rejected — save them in the
modern format first.

**Scale.** Pattern building and replacement are linear in document size but proportional to the
number of selected values. Several hundred values across large documents takes a few seconds;
several thousand will be noticeably slower.

**The key file is the whole secret.** Anyone holding both the redacted documents and
`mapping.json` can reverse the redaction. It is delivered as a separate download for that
reason, and it is the only way to restore the originals later.

## Tests

```bash
pip install -r requirements-dev.txt
python test_engine.py
```

Builds `.docx`, `.xlsx` and `.pptx` fixtures with deliberately shredded runs, redacts them,
restores them, and checks that no original value leaks, that no tag survives the restore, that
numbers and formulas are intact, and that every package part is preserved.

## Layout

```
backend/app/
  config.py     settings from .env
  ooxml.py      the OOXML read/replace engine
  entities.py   entity model, pattern building, inflection, rule detectors
  llm.py        OpenAI-compatible client, chunking, JSON recovery
  pipeline.py   analyse -> tag -> redact -> mapping -> restore
  store.py      in-memory jobs, on-disk files, TTL sweep
  routes.py     HTTP API
frontend/src/
  pages/        Anonymize, Deanonymize
  components/   FileDrop, EntityLedger
```
