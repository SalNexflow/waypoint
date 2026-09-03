"""Natural language -> a structured change. Nothing else.

The whole LLM surface of this project lives behind this one module, and the
boundary is deliberately narrow:

  * The model produces a `DispatchChange` and nothing else. It never sees a
    schedule, never proposes an assignment, never touches the solver. The
    solver is deterministic and testable; this is neither, so it is kept where
    its failure modes are containable.

  * Unparseable input is REJECTED, not guessed at. A dispatcher typing
    something ambiguous gets "I did not understand that" rather than a
    confident wrong change applied to a real day's work.

  * Every change is previewed before commit. That happens in apply.py, but the
    reason belongs here: an LLM will occasionally be wrong, so a human sees the
    diff first.

Two backends, one interface. DeepSeek is the default; Ollama is supported
because it needs no credential and runs locally, which makes the whole feature
demonstrable without a key.

Swapping the hosted provider is a change to THIS FILE ONLY -- add a
`_call_<name>` coroutine returning the model's raw text and a branch in
`parse()`. Everything downstream (JSON extraction, schema validation, change
resolution, preview, commit) is provider-agnostic and stays untouched.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from api.models import DispatchChange

log = logging.getLogger("waypoint.dispatch.parse")


SYSTEM_PROMPT = """\
You convert a dispatcher's shorthand into ONE structured change to a field \
service schedule. You never plan routes and never choose who does what -- a \
constraint solver does that. You only classify what the dispatcher said.

Reply with a single JSON object and nothing else. No prose, no markdown fence.

Schema:
{
  "kind": one of "remove_technician" | "extend_duration" | "change_shift"
                 | "change_priority" | "cancel_job" | "add_job",
  "technician_ref": "T<id>"   // for remove_technician, change_shift
  "technician_name": string   // what the dispatcher called them, if a name
  "job_ref": "J<id>"          // for extend_duration, change_priority, cancel_job
  "minutes": integer          // extend_duration: the DELTA, e.g. 60 for "an hour"
  "new_shift_end": "HH:MM"    // change_shift
  "priority": 1 | 2 | 3       // change_priority; 1 = highest, 3 = lowest
  "customer": string          // add_job
  "lat": number, "lon": number,        // add_job, if a location is known
  "required_skills": [string],         // add_job
  "before": "HH:MM",                   // add_job, if a deadline is stated
  "confidence": 0.0 to 1.0,
  "note": string              // one short sentence on what you understood
}

Omit fields that do not apply. If the message does not map cleanly onto exactly \
one of these kinds, or you cannot tell which technician or job is meant, reply:
{"kind": "unknown", "note": "<why>"}

Available technicians:
%(technicians)s

Available jobs today:
%(jobs)s

Match names case-insensitively and accept partial names ("Ahmad" -> "Ahmad \
Faizal"). If two people could match, that is "unknown". Match a customer to its
job the same way: "cancel the Menara Square job" -> the job_ref listed against
Menara Square above.

Priority counts DOWN: 1 is the most urgent, 3 the least. "Top priority", "urgent"
and "do it first" all mean 1.
"""


@dataclass(frozen=True)
class ParseResult:
    understood: bool
    change: DispatchChange | None
    error: str | None
    raw: str | None
    provider: str


class LLMUnavailable(RuntimeError):
    """No usable backend -- no credential, or the local model is not running."""


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or markdown fences no matter how firmly you ask
    them not to. Rather than fail on that, find the outermost braces. If what
    comes back is not valid JSON, that is a parse failure and is reported as
    one -- never patched up with a guess.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, depth = None, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}" and start is not None:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


DEEPSEEK_BASE_URL = "https://api.deepseek.com"


async def _call_deepseek(prompt: str, user_text: str, model: str) -> str:
    """DeepSeek via the OpenAI client -- their API is OpenAI-compatible.

    Two settings are doing real work here:

      * `response_format={"type": "json_object"}` constrains the model to emit
        a JSON object. It does not guarantee the RIGHT object, so `parse()`
        still validates against the schema -- but it removes the whole class
        of failures where a model wraps its answer in prose or a code fence.
        DeepSeek requires the word "json" to appear in the prompt for this
        mode; SYSTEM_PROMPT says it repeatedly.
      * `temperature=0` because this is a parse, not a creative task. The same
        sentence should produce the same change every time.
    """
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise LLMUnavailable(
            "DEEPSEEK_API_KEY is not set. Put it in .env, or set "
            "LLM_PROVIDER=ollama to use the local model instead."
        )

    # Imported lazily so the whole app does not require the openai package to
    # start. Everything except this one function works without it.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=45.0)
    try:
        resp = await client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=700,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the dispatcher as text
        raise LLMUnavailable(f"DeepSeek call failed: {exc}") from exc
    finally:
        await client.close()

    return resp.choices[0].message.content or ""


async def _call_ollama(prompt: str, user_text: str, model: str, url: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(
                f"{url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_text},
                    ],
                },
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama at {url} is not reachable: {exc}") from exc
    if resp.status_code != 200:
        raise LLMUnavailable(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("message", {}).get("content", "")


def build_prompt(technicians: list[dict], jobs: list[dict]) -> str:
    tech_lines = "\n".join(
        f'  T{t["id"]}: {t["name"]}, shift {t["shift_start"]}-{t["shift_end"]}, '
        f'skills {", ".join(t["skills"]) or "none"}'
        for t in technicians
    ) or "  (none)"
    # Cap the job list: a 200-job day would blow the context and add nothing.
    shown = jobs[:60]
    job_lines = "\n".join(
        f'  J{j["id"]}: {j["customer"]}' for j in shown
    ) or "  (none)"
    if len(jobs) > len(shown):
        job_lines += f"\n  ... and {len(jobs) - len(shown)} more"
    return SYSTEM_PROMPT % {"technicians": tech_lines, "jobs": job_lines}


async def parse(
    text: str,
    technicians: list[dict],
    jobs: list[dict],
    *,
    provider: str | None = None,
    model: str | None = None,
    ollama_url: str | None = None,
) -> ParseResult:
    """Turn a dispatcher's sentence into a validated DispatchChange."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "deepseek")).lower()
    prompt = build_prompt(technicians, jobs)

    try:
        if provider == "deepseek":
            raw = await _call_deepseek(
                prompt, text,
                model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        elif provider == "ollama":
            raw = await _call_ollama(
                prompt, text,
                model or os.environ.get("OLLAMA_MODEL", "llama3.1"),
                ollama_url or os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
            )
        else:
            return ParseResult(False, None, f"unknown LLM provider {provider!r}",
                               None, provider)
    except LLMUnavailable as exc:
        return ParseResult(False, None, str(exc), None, provider)
    except Exception as exc:  # noqa: BLE001
        log.exception("LLM call failed")
        return ParseResult(False, None, f"LLM call failed: {exc}", None, provider)

    data = _extract_json(raw)
    if data is None:
        return ParseResult(
            False, None,
            "the model did not return valid JSON, so the change was not applied",
            raw, provider,
        )

    if data.get("kind") in (None, "unknown", ""):
        return ParseResult(
            False, None,
            data.get("note") or "could not map that onto a supported change",
            raw, provider,
        )

    # Strip nulls so pydantic's defaults apply rather than being overwritten.
    cleaned = {k: v for k, v in data.items() if v is not None}
    try:
        change = DispatchChange.model_validate(cleaned)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "input"
        return ParseResult(
            False, None,
            f"the parsed change was not valid ({where}: {first.get('msg')})",
            raw, provider,
        )

    return ParseResult(True, change, None, raw, provider)
