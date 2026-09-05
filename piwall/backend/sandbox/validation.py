"""Submission-time validation for user strategy code.

Runs before a bot is ever stored or executed. Rejection here produces a clear
error for the player; without it, forbidden names fail at runtime and are
swallowed into a silent no-op decision by the race loop.
"""

import ast
from typing import Optional

from RestrictedPython import compile_restricted

MAX_SOURCE_BYTES = 64_000

FORBIDDEN_NAMES = frozenset({
    "getattr", "hasattr", "setattr", "delattr",
    "eval", "exec", "compile", "open", "input",
    "globals", "locals", "vars", "dir",
    "__import__", "breakpoint", "memoryview",
})


def validate_submission(code: str) -> Optional[str]:
    """Return an error message, or None when the code is acceptable."""
    if len(code.encode("utf-8")) > MAX_SOURCE_BYTES:
        return f"Source is too large (limit {MAX_SOURCE_BYTES} bytes)"

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Syntax error: {exc}"

    for node in ast.walk(tree):
        # Load context only. `dir`, `input`, `vars`, `open` and `compile` are
        # ordinary variable names, and this is the first surface a new player
        # touches: rejecting `dir = 1` as "Use of 'dir' is not allowed" is
        # unexplainable. Binding one of these names shadows the builtin inside
        # the strategy, so a later read of it cannot reach the builtin either.
        if (isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES
                and isinstance(node.ctx, ast.Load)):
            return f"Use of '{node.id}' is not allowed in strategy code"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"Access to dunder attribute '{node.attr}' is not allowed"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "Imports are not allowed; 'math' and 'random' are provided"

    if not any(
        isinstance(node, ast.FunctionDef) and node.name == "my_strategy"
        for node in tree.body
    ):
        return "Code must define a top-level function called 'my_strategy'"

    try:
        if compile_restricted(code, filename="<user_strategy>", mode="exec") is None:
            return "Code was rejected by the sandbox compiler"
    except SyntaxError as exc:
        return f"Sandbox rejected the code: {exc}"

    return None
