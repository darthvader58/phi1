"""Content hash of the pinned runtime dependency set.

Recorded in every match manifest. Two matches sharing a dep_lock_sha256 ran
against byte-identical library versions, so a float difference between them
is a real divergence rather than a dependency bump.
"""

import hashlib
from pathlib import Path

REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"


def dep_lock_sha256() -> str:
    """Hash the pinned requirements, ignoring comments and blank lines.

    Normalising first means a comment edit does not invalidate replay
    history, while any version change does.
    """
    lines = [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    payload = "\n".join(sorted(lines)).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
