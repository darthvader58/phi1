"""Sandboxed bot executor for PIT WALL.

Runs user-submitted Python strategy functions in a restricted environment:
- No imports except math, random, dataclasses
- 50ms CPU time limit
- No file/network/system access
- Returns a Decision object
"""

import math
import random
import signal
import textwrap
import traceback
from dataclasses import dataclass
from typing import Optional

from RestrictedPython import compile_restricted, safe_globals
from RestrictedPython.Eval import default_guarded_getattr
from RestrictedPython.Guards import (
    guarded_unpack_sequence,
    safer_getattr,
)


# Re-export these so user code can reference them
@dataclass
class SandboxDecision:
    pit: bool
    compound: str


# Allowed builtins for user code
ALLOWED_BUILTINS = {
    "abs": abs,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": lambda *a, **kw: None,  # Silenced print
    "range": range,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
    "isinstance": isinstance,
    "getattr": getattr,
    "hasattr": hasattr,
}

# Timeout handler
class TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("Strategy function exceeded 50ms CPU time limit")


def compile_strategy(code: str) -> Optional[str]:
    """Compile and validate user strategy code.

    Returns error message if compilation fails, None if successful.
    """
    # Wrap user code to ensure it defines my_strategy
    try:
        byte_code = compile_restricted(
            code,
            filename="<user_strategy>",
            mode="exec",
        )
        if byte_code is None:
            return "Compilation failed: RestrictedPython rejected the code"
        return None
    except SyntaxError as e:
        return f"Syntax error: {e}"
    except Exception as e:
        return f"Compilation error: {e}"


def execute_strategy(
    code: str,
    state_dict: dict,
    my_car_dict: dict,
    timeout_ms: int = 50,
) -> dict:
    """Execute a user strategy function in a sandboxed environment.

    Args:
        code: User's Python code (must define `my_strategy(state, my_car)`)
        state_dict: Serialized RaceState as dict
        my_car_dict: Serialized CarState as dict
        timeout_ms: CPU time limit in milliseconds

    Returns:
        {"pit": bool, "compound": str} or {"error": str}
    """
    try:
        byte_code = compile_restricted(
            code,
            filename="<user_strategy>",
            mode="exec",
        )
        if byte_code is None:
            return {"error": "Compilation failed"}
    except Exception as e:
        return {"error": f"Compilation error: {e}"}

    # Build restricted globals
    restricted_globals = safe_globals.copy()
    restricted_globals["__builtins__"] = ALLOWED_BUILTINS
    restricted_globals["_getattr_"] = safer_getattr
    restricted_globals["_getiter_"] = iter
    restricted_globals["_getitem_"] = lambda obj, key: obj[key]
    restricted_globals["_inplacevar_"] = lambda op, x, y: op(x, y)
    restricted_globals["_unpack_sequence_"] = guarded_unpack_sequence
    restricted_globals["_iter_unpack_sequence_"] = guarded_unpack_sequence
    restricted_globals["_write_"] = lambda x: x

    # Inject math and random modules (safe)
    restricted_globals["math"] = math
    restricted_globals["random"] = random

    # Make state and car available as simple namespace objects
    class Namespace:
        def __init__(self, d):
            for k, v in d.items():
                if isinstance(v, dict):
                    setattr(self, k, Namespace(v))
                elif isinstance(v, list):
                    setattr(self, k, [
                        Namespace(item) if isinstance(item, dict) else item
                        for item in v
                    ])
                else:
                    setattr(self, k, v)

        def __getitem__(self, key):
            return getattr(self, key, None)

        def get(self, key, default=None):
            return getattr(self, key, default)

    restricted_globals["state"] = Namespace(state_dict)
    restricted_globals["my_car"] = Namespace(my_car_dict)

    # Execute with timeout
    restricted_locals = {}

    # Set alarm for timeout (Unix only)
    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        # Convert ms to microseconds for setitimer
        signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000.0)
    except (ValueError, AttributeError):
        pass  # Signal not available (Windows, or not main thread)

    try:
        exec(byte_code, restricted_globals, restricted_locals)

        # Find and call my_strategy
        strategy_fn = restricted_locals.get("my_strategy")
        if strategy_fn is None:
            return {"error": "Code must define a function called 'my_strategy'"}

        result = strategy_fn(
            restricted_globals["state"],
            restricted_globals["my_car"],
        )

        # Parse result
        if isinstance(result, dict):
            return {
                "pit": bool(result.get("pit", False)),
                "compound": str(result.get("compound", "MEDIUM")),
            }
        elif hasattr(result, "pit"):
            return {
                "pit": bool(result.pit),
                "compound": str(getattr(result, "compound", "MEDIUM")),
            }
        else:
            return {"error": f"my_strategy must return a dict with 'pit' and 'compound' keys"}

    except TimeoutError:
        return {"error": "Strategy exceeded 50ms CPU time limit"}
    except Exception as e:
        return {"error": f"Runtime error: {type(e).__name__}: {e}"}
    finally:
        # Cancel alarm
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except (ValueError, AttributeError):
            pass


# Default user strategy template
STRATEGY_TEMPLATE = '''\
def my_strategy(state, my_car):
    """Your strategy function.

    Args:
        state: RaceState with fields:
            .lap (int), .total_laps (int), .track (str),
            .weather (str: 'dry'|'damp'|'wet'|'drying'),
            .safety_car (bool), .safety_car_laps_left (int),
            .track_temp (float), .cars (list of CarState)

        my_car: CarState with fields:
            .car_id (str), .position (int), .gap_to_leader (float),
            .compound (str), .tyre_age (int), .fuel_kg (float),
            .pit_count (int), .pit_laps (list), .last_lap_time (float),
            .total_time (float), .retired (bool), .drs_available (bool),
            .compounds_used (list), .beliefs (dict)

    Returns:
        dict with keys: pit (bool), compound (str)
    """
    remaining = state.total_laps - state.lap

    # Example: pit when tyre age > 20 and enough laps remain
    if my_car.tyre_age > 20 and remaining > 5:
        # Choose compound based on remaining distance
        if remaining > 25:
            new_compound = "HARD"
        elif remaining > 15:
            new_compound = "MEDIUM"
        else:
            new_compound = "SOFT"
        return {"pit": True, "compound": new_compound}

    return {"pit": False, "compound": my_car.compound}
'''
