"""Environment-based configuration."""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    username: str
    password: str
    community: str
    tip_lead_time_minutes: int
    skip_already_tipped: bool
    headless: bool
    ntfy_topic: Optional[str]
    ntfy_url: str
    webhook_url: Optional[str]
    team_stats_league: Optional[str]
    team_stats_season: Optional[int]
    team_stats_weight: float
    team_stats_lookback: int

    LOGIN_URL = "https://www.kicktipp.de/info/profil/login"

    @property
    def tippabgabe_url(self) -> str:
        return f"https://www.kicktipp.de/{self.community}/tippabgabe"

    @classmethod
    def load(cls) -> "Config":
        username = os.getenv("KICKTIPP_USERNAME")
        password = os.getenv("KICKTIPP_PASSWORD")
        community = os.getenv("KICKTIPP_COMMUNITY")

        missing = [
            name
            for name, value in (
                ("KICKTIPP_USERNAME", username),
                ("KICKTIPP_PASSWORD", password),
                ("KICKTIPP_COMMUNITY", community),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )

        return cls(
            username=username,
            password=password,
            community=community,
            tip_lead_time_minutes=int(os.getenv("TIP_LEAD_TIME_MINUTES", "90")),
            skip_already_tipped=_bool("SKIP_ALREADY_TIPPED", True),
            headless=_bool("HEADLESS", True),
            ntfy_topic=os.getenv("NTFY_TOPIC"),
            ntfy_url=os.getenv("NTFY_URL", "https://ntfy.sh"),
            webhook_url=os.getenv("WEBHOOK_URL"),
            team_stats_league=os.getenv("TEAM_STATS_LEAGUE") or None,
            team_stats_season=(
                int(os.getenv("TEAM_STATS_SEASON"))
                if os.getenv("TEAM_STATS_SEASON")
                else None
            ),
            team_stats_weight=float(os.getenv("TEAM_STATS_WEIGHT", "0.35")),
            team_stats_lookback=int(os.getenv("TEAM_STATS_LOOKBACK", "6")),
        )
