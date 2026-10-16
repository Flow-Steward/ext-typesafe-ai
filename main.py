#!/usr/bin/env python3
"""One public host envelope in; one bounded, credential-free response out."""

import json
import sys

from jev import MAX_BODY_BYTES, failure, handle_payload


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_BODY_BYTES + 65537)
    try:
        if len(raw) > MAX_BODY_BYTES + 65536:
            raise ValueError("envelope too large")
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        result = failure("invalid_payload", "Expected a bounded UTF-8 JSON envelope.")
    else:
        result = handle_payload(payload)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
