"""Re-run committed manifests and compare replay hashes.

Usage:
  python scripts/verify_determinism.py                          # verify against committed hashes
  python scripts/verify_determinism.py --record --reason "..."  # regenerate them

--record is how a deliberate engine change is accepted. A hash that moves
without --record means the determinism contract broke.

The hashed payload is `asdict(manifest)` plus the simulation output (see
replay.py's replay_bytes), so a golden hash is not purely a function of what
the engine computed -- inert manifest metadata such as python_version sits
inside it too. That is harmless today because the golden manifests are
static, committed inputs. But it is a trap for --record specifically:
regenerating the manifests inside a different interpreter (the 3.11
container, say) or after a dependency bump moves all six hashes for reasons
that have nothing to do with the engine, and a 64-character hex diff cannot
show a reviewer which kind of change happened. --reason exists so that
distinction is written down in prose in golden/CHANGELOG, not left to be
inferred from the hex.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "determinism" / "golden"
CHANGELOG = GOLDEN / "CHANGELOG"


def _git_is_dirty(cwd: Path) -> bool:
    """True if `cwd`'s working tree (any tracked or untracked change) is dirty.

    --record is refused on a dirty tree so a hash bump always lands as its
    own reviewable commit, never mixed into an unrelated engine change where
    the two are impossible to tell apart from the hex alone.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )
    return bool(result.stdout.strip())


def _compute(path: Path) -> str:
    manifest = MatchManifest.from_dict(json.loads(path.read_text()))
    return "sha256:" + hashlib.sha256(replay_from_manifest(manifest)).hexdigest()


def _append_changelog(reason: str, changed: list) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## {stamp} -- {reason}"]
    for stem, old, new in changed:
        lines.append(f"- {stem}: {old} -> {new}")
    with CHANGELOG.open("a") as f:
        f.write("\n".join(lines) + "\n\n")


def main(record: bool, reason: str) -> int:
    if record:
        if _git_is_dirty(ROOT):
            print(
                "refusing to record: the working tree is dirty. Commit or "
                "stash pending changes first, so the hash bump lands as its "
                "own reviewable commit rather than mixed into an engine "
                "change.",
                file=sys.stderr,
            )
            return 2
        if not reason:
            print(
                "refusing to record: --reason \"...\" is required. A moved "
                "golden hash is a 64-character hex diff that no reviewer can "
                "evaluate on its own -- --reason is what makes it "
                "reviewable.",
                file=sys.stderr,
            )
            return 2

    failures = 0
    changed = []
    manifest_paths = sorted(GOLDEN.glob("*.manifest.json"))
    for path in manifest_paths:
        expected_path = path.with_name(path.name.replace(".manifest.json", ".expected.txt"))
        actual = _compute(path)
        if record:
            old = expected_path.read_text().strip() if expected_path.exists() else "(none)"
            if old == actual:
                print(f"unchanged {path.stem}: {actual}")
            else:
                print(f"CHANGED  {path.stem}: {old} -> {actual}")
                changed.append((path.stem, old, actual))
            expected_path.write_text(actual + "\n")
            continue
        expected = expected_path.read_text().strip()
        if actual == expected:
            print(f"ok       {path.stem}")
        else:
            print(f"MISMATCH {path.stem}\n  expected {expected}\n  actual   {actual}")
            failures += 1

    if record:
        print(f"\n{len(changed)} of {len(manifest_paths)} hashes changed.")
        if changed:
            _append_changelog(reason, changed)
        return 0

    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="regenerate committed hashes")
    parser.add_argument("--reason", default=None, help="required with --record; appended to golden/CHANGELOG")
    args = parser.parse_args()
    sys.exit(main(args.record, args.reason))
