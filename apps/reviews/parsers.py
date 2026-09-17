"""Defensive parsing of structured review output."""

import json
import re


class ReviewParseError(ValueError):
    """Raised when an AI response cannot be interpreted safely."""


def parse_review_response(content: str) -> tuple[str, list[dict]]:
    """Parse JSON output, accepting a fenced JSON block."""
    candidate = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ReviewParseError("AI response was not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings", []), list):
        raise ReviewParseError("AI response has an invalid review schema")
    findings = []
    for finding in data.get("findings", []):
        if not isinstance(finding, dict) or not finding.get("file") or not finding.get("message"):
            continue
        findings.append({"file_path": str(finding["file"]), "line_number": finding.get("line"), "comment_type": finding.get("type", "issue"), "severity": finding.get("severity", ""), "content": str(finding["message"])})
    return str(data.get("summary", "")), findings
