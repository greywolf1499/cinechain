"""Stdlib-only healthcheck probe (no curl/wget in the slim runtime image)."""

import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8787/api/health", timeout=3) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
