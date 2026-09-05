"""Turns bookmaker odds (as shown on the Kicktipp tipping page) into a scoreline.

Approach: convert odds to win/draw/win probabilities, then fit a bivariate
Poisson model (independent home/away goal counts) whose match-outcome
probabilities match those targets as closely as possible. The predicted
score is the most likely (mode) scoreline under that fitted model. This
gives more realistic and varied scorelines than a fixed lookup table
(e.g. distinguishing a 60% vs. 90% favorite as 2:1 vs. 3:0 rather than
lumping both into "2:0").
"""

import math
from dataclasses import dataclass
from functools import lru_cache

MAX_GOALS = 8


@dataclass(frozen=True)
class Odds:
    home: float
    draw: float
    away: float


def implied_probabilities(odds: Odds) -> tuple[float, float, float]:
    """Convert decimal odds to normalized win/draw/win probabilities.

    Bookmaker odds always sum to more than 100% implied probability (the
    overround/vig). Normalizing removes that margin so the three
    probabilities sum to 1.
    """
    raw_home = 1 / odds.home
    raw_draw = 1 / odds.draw
    raw_away = 1 / odds.away
    total = raw_home + raw_draw + raw_away
    return raw_home / total, raw_draw / total, raw_away / total


@lru_cache(maxsize=None)
def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _outcome_probabilities(lambda_home: float, lambda_away: float) -> tuple[float, float, float]:
    """P(home win), P(draw), P(away win) for independent Poisson goal counts."""
    p_home = p_draw = p_away = 0.0
    for i in range(MAX_GOALS + 1):
        pi = _poisson_pmf(i, lambda_home)
        for j in range(MAX_GOALS + 1):
            p = pi * _poisson_pmf(j, lambda_away)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def _fit_lambdas(
    target_home: float, target_draw: float, target_away: float
) -> tuple[float, float]:
    """Find expected goals (lambda_home, lambda_away) whose Poisson outcome
    probabilities best match the odds-implied targets, via a coarse-to-fine
    grid search (keeps this dependency-free, no numpy/scipy needed)."""

    def error(lh: float, la: float) -> float:
        ph, pd, pa = _outcome_probabilities(lh, la)
        return (ph - target_home) ** 2 + (pd - target_draw) ** 2 + (pa - target_away) ** 2

    def search(
        lo_h: float, hi_h: float, lo_a: float, hi_a: float, step: float
    ) -> tuple[float, float]:
        best = (math.inf, lo_h, lo_a)
        lh = lo_h
        while lh <= hi_h + 1e-9:
            la = lo_a
            while la <= hi_a + 1e-9:
                err = error(round(lh, 3), round(la, 3))
                if err < best[0]:
                    best = (err, round(lh, 3), round(la, 3))
                la += step
            lh += step
        return best[1], best[2]

    coarse_h, coarse_a = search(0.1, 5.0, 0.1, 5.0, 0.1)
    fine_h, fine_a = search(
        max(0.05, coarse_h - 0.15),
        coarse_h + 0.15,
        max(0.05, coarse_a - 0.15),
        coarse_a + 0.15,
        0.02,
    )
    return fine_h, fine_a


def _most_likely_score_in_category(
    lambda_home: float, lambda_away: float, category: str
) -> tuple[int, int]:
    """Most likely scoreline restricted to one outcome category.

    Picking the single most likely cell across the *whole* grid is biased
    towards draws: when lambda_home and lambda_away are reasonably close,
    a single diagonal cell (e.g. 1:1) often outscores every individual
    off-diagonal cell even though, summed up, home-win cells clearly have
    more total probability than draw cells. Restricting the search to the
    outcome category that's actually most likely (per the odds) avoids
    that bias.
    """
    best_prob = -1.0
    best_score = (0, 0)
    for i in range(MAX_GOALS + 1):
        pi = _poisson_pmf(i, lambda_home)
        for j in range(MAX_GOALS + 1):
            if category == "home" and not i > j:
                continue
            if category == "away" and not i < j:
                continue
            if category == "draw" and not i == j:
                continue
            p = pi * _poisson_pmf(j, lambda_away)
            if p > best_prob:
                best_prob = p
                best_score = (i, j)
    return best_score


def _blend_with_team_stats(
    lambda_home_fit: float,
    lambda_away_fit: float,
    stats_lambdas: tuple[float, float],
    stats_weight: float,
) -> tuple[float, float]:
    """Shift the *total* expected goals towards team-specific attack/defense
    form, while preserving the home/away *ratio* implied by the odds fit.

    The odds already fully determine that ratio (it's what makes the win
    probabilities come out right) - two equations, two unknowns, no slack
    left to also encode "good strikers, bad defense" there. What the odds
    can't tell apart is the overall goal level: a 55%-favorite grinding out
    a 1:0 and a 55%-favorite in a high-scoring 3:2 look identical in 1/X/2
    terms. Recent goals-scored/conceded form is a signal for exactly that,
    so it only ever nudges the total, never who's considered the favorite.
    """
    ratio = lambda_home_fit / lambda_away_fit if lambda_away_fit > 1e-6 else lambda_home_fit / 1e-6

    total_fit = lambda_home_fit + lambda_away_fit
    total_stats = sum(stats_lambdas)
    blended_total = (1 - stats_weight) * total_fit + stats_weight * total_stats

    lambda_away = blended_total / (1 + ratio)
    lambda_home = blended_total - lambda_away
    return lambda_home, lambda_away


def predict_score(
    odds: Odds,
    allow_draw: bool = True,
    stats_lambdas: tuple[float, float] | None = None,
    stats_weight: float = 0.35,
) -> tuple[int, int]:
    """Predict a scoreline from bookmaker odds via a fitted Poisson model.

    The outcome category (home win / draw / away win) is taken directly
    from the odds - whichever is most likely. The Poisson fit is only used
    to pick the most plausible scoreline *within* that category (e.g. a
    strong favorite wins 3:0, a slight one 1:0).

    If `stats_lambdas` (expected goals for each side from recent
    goals-scored/conceded form, see team_stats.py) is given, it nudges the
    total goals level - see _blend_with_team_stats - without changing
    which side is considered the favorite.
    """
    p_home, p_draw, p_away = implied_probabilities(odds)

    if allow_draw and p_draw >= p_home and p_draw >= p_away:
        category = "draw"
    elif p_home >= p_away:
        category = "home"
    else:
        category = "away"

    lambda_home, lambda_away = _fit_lambdas(p_home, p_draw, p_away)

    if stats_lambdas is not None:
        lambda_home, lambda_away = _blend_with_team_stats(
            lambda_home, lambda_away, stats_lambdas, stats_weight
        )

    return _most_likely_score_in_category(lambda_home, lambda_away, category)
