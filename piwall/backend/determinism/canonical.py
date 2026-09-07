"""One definition of "byte-identical", shared by manifests and replays.

Every reproducibility claim in this phase is an equality between two byte
strings, so the encoding must be fixed: sorted keys so dict insertion order
cannot leak in, no whitespace so formatting cannot, and no NaN or Infinity
because neither survives a JSON round trip intact.
"""

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def canonical_json(obj: Any) -> bytes:
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True,
    ).encode("utf-8")
