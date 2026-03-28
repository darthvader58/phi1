"""Season and points calculation for PIT WALL."""

from typing import Dict, List

# F1-style points distribution
POINTS_TABLE = {
    1: 25, 2: 18, 3: 15, 4: 12, 5: 10,
    6: 8, 7: 6, 8: 4, 9: 2, 10: 1,
}

# Track rotation for a standard season
STANDARD_ROTATION = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]


def points_for_position(position: int) -> int:
    return POINTS_TABLE.get(position, 0)


def compute_constructor_standings(
    player_standings: List[dict],
) -> List[dict]:
    """Aggregate player standings into constructor (team) standings."""
    teams: Dict[str, dict] = {}
    for ps in player_standings:
        team = ps.get("team", "Independent")
        if team not in teams:
            teams[team] = {"team": team, "total_points": 0, "drivers": []}
        teams[team]["total_points"] += ps["total_points"]
        teams[team]["drivers"].append(ps["username"])

    return sorted(teams.values(), key=lambda x: -x["total_points"])
