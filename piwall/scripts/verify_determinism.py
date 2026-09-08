"""Re-run committed manifests and compare replay hashes.

Usage:
  python scripts/verify_determinism.py            # verify against committed hashes
  python scripts/verify_determinism.py --record   # regenerate them

--record is how a deliberate engine change is accepted. A hash that moves
without --record means the determinism contract broke.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "determinism" / "golden"


def main(record: bool) -> int:
    failures = 0
    for path in sorted(GOLDEN.glob("*.manifest.json")):
        manifest = MatchManifest.from_dict(json.loads(path.read_text()))
        expected_path = path.with_name(path.name.replace(".manifest.json", ".expected.txt"))
        import hashlib
        actual = "sha256:" + hashlib.sha256(replay_from_manifest(manifest)).hexdigest()
        if record:
            expected_path.write_text(actual + "\n")
            print(f"recorded {path.stem}: {actual}")
            continue
        expected = expected_path.read_text().strip()
        if actual == expected:
            print(f"ok       {path.stem}")
        else:
            print(f"MISMATCH {path.stem}\n  expected {expected}\n  actual   {actual}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main("--record" in sys.argv))
