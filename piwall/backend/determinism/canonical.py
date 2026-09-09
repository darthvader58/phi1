"""One definition of "byte-identical", shared by manifests and replays.

Every reproducibility claim in this phase is an equality between two byte
strings, so the encoding must be fixed: sorted keys so dict insertion order
cannot leak in, no whitespace so formatting cannot, and no NaN or Infinity
because neither survives a JSON round trip intact.

Dataclass unwrapping is shallow and top-level only: canonical_json() calls
asdict() when the argument itself is a dataclass instance, but a dataclass
nested inside a plain dict or list is left as-is and raises TypeError from
json.dumps. Callers that nest a dataclass inside another structure must call
dataclasses.asdict() on it themselves before passing the structure in.
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
