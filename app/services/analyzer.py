"""OpenAI-powered S&P analysis. Numbers script lines, sends with guidelines,
gets back structured findings JSON."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.config import settings

SYSTEM_PROMPT = """You are a Standards & Practices (S&P) reviewer for an OTT streaming platform's content team.

You will receive:
1. The current S&P guidelines document.
2. A script with every line numbered like `L1: ...`, `L2: ...`.

Your job: find every line (or contiguous span of lines) that VIOLATES or PUSHES THE LIMIT of the guidelines. Be strict but not paranoid — flag real issues, not stylistic quibbles.

Return ONLY valid JSON matching this exact schema (no prose, no markdown):

{
  "findings": [
    {
      "line_start": <int, 1-indexed inclusive>,
      "line_end": <int, 1-indexed inclusive>,
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
- If nothing is problematic, return {"findings": []}.
- Do not invent guidelines. Do not flag things not covered by the guidelines.
- The script may be in Bengali, English, or mixed; respond in English."""


def analyze(script_text: str, guidelines_text: str) -> dict[str, Any]:
    numbered = _number_lines(script_text)
    client = OpenAI(api_key=settings.openai_api_key)

    user_msg = (
        f"=== S&P GUIDELINES ===\n{guidelines_text}\n\n"
        f"=== SCRIPT (line-numbered) ===\n{numbered}\n\n"
        "Return the JSON findings object now."
    )

    resp = client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=8000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = resp.choices[0].message.content or ""
    return _parse_json(raw)


def _number_lines(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(f"L{i+1}: {ln}" for i, ln in enumerate(lines))


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
        return {"findings": [], "_raw": raw, "_error": "no json found"}
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        return {"findings": [], "_raw": raw, "_error": f"json decode: {e}"}
    findings = data.get("findings", [])
    cleaned = [f for f in findings if _valid_finding(f)]
    return {"findings": cleaned}


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
