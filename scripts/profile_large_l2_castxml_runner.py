#!/usr/bin/env python3
"""Decode and execute the temporary compressed large-L2 profiler payload."""
from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name("profile_large_l2_castxml.py")
text = payload_path.read_text(encoding="utf-8")
match = re.search(r'_PAYLOAD = r"""(.*?)"""', text, flags=re.DOTALL)
if match is None:
    raise SystemExit(f"compressed payload not found in {payload_path}")
encoded = "".join(match.group(1).split())
source = zlib.decompress(base64.b85decode(encoded))
exec(compile(source, str(payload_path), "exec"))
