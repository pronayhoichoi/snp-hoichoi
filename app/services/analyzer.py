"""OpenAI-powered S&P analysis with chunking for long scripts.

Splits the script into ~500-line chunks (roughly 20 pages), runs each through
GPT-4o in parallel, and merges the findings. Line numbers stay in the original
script's coordinate system so the viewer/DOCX writer see one continuous document.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from openai import OpenAI

from app.config import settings

log = logging.getLogger(__name__)

CHUNK_LINES = 500          # ~20 pages of a typical script
CHUNK_OVERLAP = 20         # lines shared between adjacent chunks to catch violations that span the boundary
MAX_PARALLEL = 2           # concurrent chunk calls (keep memory low on small containers)
PER_CHUNK_MAX_TOKENS = 4000

SYSTEM_PROMPT = """You are a Standards & Practices (S&P) reviewer for an OTT streaming platform's content team.

You will receive:
1. The current S&P guidelines document.
2. A section of a script with every line numbered like `L42: ...`, `L43: ...`. The numbers are the ABSOLUTE line numbers in the full script — DO NOT renumber.

Your job: find every line (or contiguous span of lines) that VIOLATES or PUSHES THE LIMIT of the guidelines. Be strict but not paranoid — flag real issues, not stylistic quibbles.

Return ONLY valid JSON matching this exact schema (no prose, no markdown):

{
  "findings": [
    {
      "line_start": <int, absolute line number, 1-indexed inclusive>,
      "line_end": <int, absolute line number, 1-indexed inclusive>,
      "excerpt": "<verbatim substring of the flagged text, max 300 chars>",
      "severity": "high" | "medium" | "low",
      "guideline_ref": "<short quote or section name from the guidelines>",
      "reason": "<one sentence explaining the violation>",
      "suggestion": "<one sentence: how to make it compliant, or 'remove' if unfixable>"
    }
  ]
}

Rules:
- severity: "high" = clear violation, must fix; "medium" = borderline, needs producer sign-off; "low" = mild, note only.
- Every finding MUST cite a guideline_ref that appears in the guidelines document.
- If nothing is problematic in this section, return {"findings": []}.
- Do not invent guidelines. Do not flag things not covered by the guidelines.
- The script may be in Bengali, English, or mixed; respond in English."""


def analyze(script_text: str, guidelines_text: str) -> dict[str, Any]:
    lines = script_text.splitlines()
    total = len(lines)
    chunks = _make_chunks(lines)
    log.info("analyzer: %d lines, %d chunks", total, len(chunks))

    client = OpenAI(api_key=settings.openai_api_key)
    all_findings: list[dict] = []

    if len(chunks) == 1:
        start, end = chunks[0]
        result = _analyze_chunk(client, lines, start, end, guidelines_text)
        all_findings.extend(result.get("findings", []))
    else:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = {
                ex.submit(_analyze_chunk, client, lines, s, e, guidelines_text): (s, e)
                for s, e in chunks
            }
            for fut in as_completed(futures):
                s, e = futures[fut]
                try:
                    res = fut.result()
                    log.info("chunk L%d–L%d: %d findings", s + 1, e, len(res.get("findings", [])))
                    all_findings.extend(res.get("findings", []))
                except Exception as exc:
                    log.exception("chunk L%d–L%d FAILED: %s", s + 1, e, exc)

    merged = _dedupe(all_findings)
    merged.sort(key=lambda f: (f["line_start"], f["line_end"]))
    return {"findings": merged, "_meta": {"chunks": len(chunks), "total_lines": total}}


def _make_chunks(lines: list[str]) -> list[tuple[int, int]]:
    n = len(lines)
    if n <= CHUNK_LINES:
        return [(0, n)]
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + CHUNK_LINES, n)
        chunks.append((start, end))
        if end >= n:
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _analyze_chunk(
    client: OpenAI,
    all_lines: list[str],
    start: int,
    end: int,
    guidelines_text: str,
) -> dict[str, Any]:
    numbered = "\n".join(f"L{start + i + 1}: {ln}" for i, ln in enumerate(all_lines[start:end]))
    total = len(all_lines)
    user_msg = (
        f"=== S&P GUIDELINES ===\n{guidelines_text}\n\n"
        f"=== SCRIPT SECTION (lines L{start + 1}–L{end} of {total} total) ===\n{numbered}\n\n"
        "Return the JSON findings object now."
    )
    resp = client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=PER_CHUNK_MAX_TOKENS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = resp.choices[0].message.content or ""
    return _parse_json(raw)


def _parse_json(raw: str) -> dict[str, Any]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return {"findings": []}
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return {"findings": []}
    findings = data.get("findings", [])
    return {"findings": [f for f in findings if _valid_finding(f)]}


def _valid_finding(f: dict) -> bool:
    try:
        return (
            isinstance(f.get("line_start"), int)
            and isinstance(f.get("line_end"), int)
            and f["line_end"] >= f["line_start"]
            and f.get("severity") in ("high", "medium", "low")
            and isinstance(f.get("reason"), str)
        )
    except Exception:
        return False


def _dedupe(findings: list[dict]) -> list[dict]:
    """Remove duplicate findings that emerge from chunk overlap regions."""
    seen: dict[tuple, dict] = {}
    for f in findings:
        key = (f["line_start"], f["line_end"], f.get("guideline_ref", ""))
        if key not in seen:
            seen[key] = f
    return list(seen.values())
