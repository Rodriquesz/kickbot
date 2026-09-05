"""Orchestrates one bot run: login, find forgotten tips close to their deadline, fill them in."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .browser import build_driver
from .config import Config
from .kicktipp import (
    dismiss_consent_dialog,
    extract_odds,
    fetch_open_games,
    fill_tip,
    login,
    submit_tips,
)
from .notify import notify
from .predictor import predict_score
from .team_stats import fetch_recent_team_stats, stats_based_lambdas

logger = logging.getLogger("kickbot")

BERLIN = ZoneInfo("Europe/Berlin")


def run(config: Config, dry_run: bool = False) -> list[str]:
    """Run one pass. Returns a list of human-readable descriptions of tips placed."""
    driver = build_driver(headless=config.headless)
    placed: list[str] = []
    missing_odds: list[str] = []

    try:
        login(driver, config)
        driver.get(config.tippabgabe_url)
        dismiss_consent_dialog(driver)
        games = fetch_open_games(driver)
        logger.info("Found %d open games on the tipping page", len(games))

        team_stats = {}
        if config.team_stats_league:
            try:
                team_stats = fetch_recent_team_stats(
                    config.team_stats_league,
                    season=config.team_stats_season,
                    lookback=config.team_stats_lookback,
                )
                logger.debug("Loaded recent form for %d teams", len(team_stats))
            except Exception as exc:  # network hiccup, API change, etc.
                logger.warning("Could not load team form stats, using odds only: %s", exc)

        now = datetime.now(tz=BERLIN)
        lead_time = timedelta(minutes=config.tip_lead_time_minutes)

        for game in games:
            if config.skip_already_tipped and game.already_tipped:
                continue
            if game.kickoff <= now:
                continue
            time_left = game.kickoff - now
            if time_left > lead_time:
                logger.debug(
                    "%s vs %s kicks off in %s, more than the %sm lead time - leaving it for you",
                    game.home_team,
                    game.away_team,
                    time_left,
                    config.tip_lead_time_minutes,
                )
                continue

            odds = extract_odds(game.row)
            if odds is None:
                logger.warning(
                    "No odds found for %s vs %s, skipping", game.home_team, game.away_team
                )
                missing_odds.append(f"{game.home_team} vs {game.away_team}")
                continue

            stats_lambdas = stats_based_lambdas(game.home_team, game.away_team, team_stats)
            home_goals, away_goals = predict_score(
                odds,
                allow_draw=game.allow_draw,
                stats_lambdas=stats_lambdas,
                stats_weight=config.team_stats_weight,
            )
            form_note = " + Formkurve" if stats_lambdas is not None else ""
            description = (
                f"{game.home_team} {home_goals}:{away_goals} {game.away_team} "
                f"(odds 1/X/2 = {odds.home}/{odds.draw}/{odds.away}{form_note}, "
                f"kickoff {game.kickoff.strftime('%d.%m. %H:%M')})"
            )

            if dry_run:
                logger.info("[DRY RUN] Would tip: %s", description)
            else:
                fill_tip(game, home_goals, away_goals)
                logger.info("Tipped: %s", description)

            placed.append(description)

        if missing_odds:
            notify(
                config,
                "kickbot: Quoten fehlen",
                "Konnte fuer folgende faellige Spiele keine Quoten von der "
                "Kicktipp-Seite lesen (moeglicherweise hat sich das Seiten-Layout "
                "geaendert):\n- " + "\n- ".join(missing_odds),
            )

        if placed and not dry_run:
            submit_tips(driver)
            logger.info("Submitted %d tip(s)", len(placed))
        elif not placed:
            logger.info("Nothing to do - no forgotten tips within the lead time window")

        return placed

    finally:
        driver.quit()
