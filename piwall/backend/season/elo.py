"""ELO rating system for PIT WALL.

Updates ELO for every head-to-head pairing after each race:
    E_A = 1 / (1 + 10^((R_B - R_A) / 400))
    R'_A = R_A + K * (S_A - E_A)

K = 32 for Quick Races, K = 48 for Season races
S_A = 1 if A finished ahead of B, 0.5 if tied, 0 otherwise
"""

from typing import Dict, List, Tuple


def expected_score(rating_a: float, rating_b: float) -> float:
    """Calculate expected score of A vs B."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def compute_elo_updates(
    standings: List[Tuple[str, int, bool]],
    ratings: Dict[str, float],
    k_factor: float = 32.0,
) -> Dict[str, float]:
    """Compute ELO deltas for all players after a race.

    Args:
        standings: List of (player_id, position, retired) sorted by position
        ratings: Current ELO ratings {player_id: rating}
        k_factor: K=32 for quick races, K=48 for season races

    Returns:
        Dict of {player_id: new_rating}
    """
    new_ratings = dict(ratings)

    # For each pair (A, B), compute head-to-head result
    for i, (pid_a, pos_a, ret_a) in enumerate(standings):
        delta_a = 0.0
        ra = ratings.get(pid_a, 1200.0)

        for j, (pid_b, pos_b, ret_b) in enumerate(standings):
            if i == j:
                continue

            rb = ratings.get(pid_b, 1200.0)
            e_a = expected_score(ra, rb)

            # Determine actual score
            if ret_a and ret_b:
                s_a = 0.5  # Both DNF = tie
            elif ret_a:
                s_a = 0.0  # A retired, B didn't
            elif ret_b:
                s_a = 1.0  # B retired, A didn't
            elif pos_a < pos_b:
                s_a = 1.0  # A finished ahead
            elif pos_a > pos_b:
                s_a = 0.0
            else:
                s_a = 0.5

            delta_a += k_factor * (s_a - e_a)

        # Average delta across all opponents (normalize by number of opponents)
        n_opponents = len(standings) - 1
        if n_opponents > 0:
            delta_a /= n_opponents

        new_ratings[pid_a] = ra + delta_a

    return new_ratings
