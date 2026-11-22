"""Read a short answer to a failed-check question.

    python scripts/answer.py "About this question on the plan: ...

    ok fix"
"""

from __future__ import annotations

import json
import re
import sys

_ABOUT = re.compile(
    r"About this question on the plan:\s*(.*?)\s*(?:\n\s*){2,}(.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_NAMED = re.compile(r"[\"“]([^\"”]{2,80})[\"”]")
_FIX = re.compile(
    r"^(?:ok(?:ay)?\s+)?(?:fix(?:\s+it)?|yes|yeah|yep|do\s+it|go\s+ahead|please|sure|"
    r"relax(?:\s+it)?|change(?:\s+(?:it|the\s+plan))?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def answer(message: str) -> dict:
    """``{"fix": bool, "check": str, "relax": bool}``. A long new request is not an answer."""
    quoted, reply = "", (message or "").strip()
    found = _ABOUT.search(reply)
    if found:
        quoted, reply = found.group(1).strip(), found.group(2).strip()
    if not _FIX.match(reply):
        return {"fix": False, "check": "", "relax": False}
    named = _NAMED.search(quoted or message or "")
    return {
        "fix": True,
        "check": named.group(1).strip() if named else "",
        "relax": re.search(r"\brelax\b", reply, re.IGNORECASE) is not None,
    }


def main() -> None:
    print(json.dumps(answer(" ".join(sys.argv[1:])), ensure_ascii=False))


if __name__ == "__main__":
    main()
