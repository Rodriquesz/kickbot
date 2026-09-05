"""Recent goals-scored/conceded form per team, via the free OpenLigaDB API.

Covers German Bundesliga / 2. Bundesliga (and similar OpenLigaDB leagues).
Used to distinguish a match "1:0 because both sides are defensive" from
"1:0 despite both sides scoring a lot, this time it just landed narrow" -
something the bookmaker win/draw/away odds alone can't tell apart (see
predictor.py). Entirely best-effort: any lookup or name-matching failure
just means the caller falls back to odds-only, never a hard error.
"""

import logging
import re
from datetime import date, datetime

import requests

logger = logging.getLogger("kickbot")

OPENLIGADB_URL = "https://api.openligadb.de/getmatchdata/{league}/{season}"

# Tokens that don't help identify a club (legal form, sponsor prefixes,
# founding year digits) and differ between Kicktipp's short names and
# OpenLigaDB's official names.
_NOISE_TOKENS = (
    "1.", "fc", "sv", "tsv", "vfl", "vfb", "sc", "sg", "spvgg", "tsg",
    "borussia", "bayer", "rasenballsport", "rb", "fsv", "eintracht",
)


def _current_season(today: date | None = None) -> int:
    """OpenLigaDB seasons are labeled by the year they start in (the
    2025/26 season is "2025"). The German season runs roughly Jul-Jun."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def _normalize_team_name(name: str) -> str:
    normalized = name.lower().replace(".", "").replace("-", " ")
    normalized = re.sub(r"\d+", "", normalized)
    for token in _NOISE_TOKENS:
        normalized = re.sub(rf"\b{re.escape(token.rstrip('.'))}\b", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def match_team_name(kicktipp_name: str, candidates: list[str]) -> str | None:
    """Best-effort match between a Kicktipp team name and an OpenLigaDB
    team name, e.g. "Gladbach" <-> "Borussia Mönchengladbach". Returns
    None (rather than a guess) when there's no confident match."""
    target = _normalize_team_name(kicktipp_name)
    if not target:
        return None

    best_candidate = None
    best_score = 0.0
    for candidate in candidates:
        candidate_norm = _normalize_team_name(candidate)
        if not candidate_norm:
            continue
        if target in candidate_norm or candidate_norm in target:
            score = 1.0
        else:
            import difflib

            score = difflib.SequenceMatcher(None, target, candidate_norm).ratio()
        if score > best_score:
            best_score = score
            best_candidate = candidate

    return best_candidate if best_score >= 0.6 else None


def fetch_recent_team_stats(
    league: str, season: int | None = None, lookback: int = 6, min_games: int = 3
) -> dict[str, tuple[float, float]]:
    """Returns {team_name: (avg_goals_scored, avg_goals_conceded)} over
    each team's last `lookback` finished league matches this season.
    Teams with fewer than `min_games` finished matches (e.g. very early
    in the season) are left out rather than guessed at."""
    season = season if season is not None else _current_season()
    url = OPENLIGADB_URL.format(league=league, season=season)

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    matches = response.json()

    per_team: dict[str, list[tuple[datetime, int, int]]] = {}
    for match in matches:
        if not match.get("matchIsFinished"):
            continue
        final = next(
            (
                r
                for r in match.get("matchResults", [])
                if r.get("resultTypeKind") == "After90Minutes"
            ),
            None,
        )
        if final is None:
            continue

        home_name = match["team1"]["teamName"]
        away_name = match["team2"]["teamName"]
        home_goals = final["pointsTeam1"]
        away_goals = final["pointsTeam2"]
        try:
            kickoff = datetime.fromisoformat(match["matchDateTime"])
        except (KeyError, ValueError):
            continue

        per_team.setdefault(home_name, []).append((kickoff, home_goals, away_goals))
        per_team.setdefault(away_name, []).append((kickoff, away_goals, home_goals))

    stats: dict[str, tuple[float, float]] = {}
    for team, games in per_team.items():
        games.sort(key=lambda g: g[0])
        recent = games[-lookback:]
        if len(recent) < min_games:
            continue
        avg_scored = sum(g[1] for g in recent) / len(recent)
        avg_conceded = sum(g[2] for g in recent) / len(recent)
        stats[team] = (avg_scored, avg_conceded)

    return stats


def stats_based_lambdas(
    home_team: str,
    away_team: str,
    stats: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Expected goals for this specific matchup, from recent team form:
    a team's expected goals = average of its own recent scoring rate and
    the opponent's recent conceding rate. Returns None if either team
    couldn't be confidently matched or has no recent-form data yet."""
    if not stats:
        return None

    candidates = list(stats.keys())
    home_match = match_team_name(home_team, candidates)
    away_match = match_team_name(away_team, candidates)
    if home_match is None or away_match is None:
        return None

    home_scored, home_conceded = stats[home_match]
    away_scored, away_conceded = stats[away_match]
    lambda_home = (home_scored + away_conceded) / 2
    lambda_away = (away_scored + home_conceded) / 2
    return lambda_home, lambda_away
