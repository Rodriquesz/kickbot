"""Plain-HTTP interactions with kicktipp.de: login, reading open games, submitting tips.

No browser needed: the login form and the tipping page are both ordinary
server-rendered HTML forms (verified directly against kicktipp.de - no
CSRF token, no JS challenge, session state is a plain cookie). This talks
to them with `requests` + BeautifulSoup instead of driving a headless
Chrome, which is what made this heavy enough to be a problem on something
like a Raspberry Pi.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

from .config import Config
from .predictor import Odds

logger = logging.getLogger("kickbot")

BERLIN = ZoneInfo("Europe/Berlin")

LOGIN_ACTION_URL = "https://www.kicktipp.de/info/profil/loginaction"

# Kicktipp marks knockout-stage matches (extra time / penalties) with text
# like "n.V." or "i.E.". A draw tip is rejected by Kicktipp for those.
NO_DRAW_MARKERS = ("n.v.", "n.e.", "i.e.", "elfmeterschie")


class LoginError(Exception):
    pass


class TippingError(Exception):
    pass


@dataclass
class OpenGame:
    home_team: str
    away_team: str
    kickoff: datetime
    home_field: str
    away_field: str
    already_tipped: bool
    allow_draw: bool
    row: Tag


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        }
    )
    return session


def login(session, config: Config) -> None:
    # A plain GET first, mirroring a real browser visit - this is also
    # where the session cookie gets set.
    session.get(config.LOGIN_URL, timeout=20)

    response = session.post(
        LOGIN_ACTION_URL,
        data={
            "kennung": config.username,
            "passwort": config.password,
            "submitbutton": "Anmelden",
        },
        timeout=20,
    )

    if "profil/login" in response.url:
        raise LoginError("Still on login page after submitting - check credentials")

    logger.info("Logged in as %s", config.username)


def _parse_kickoff(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        naive = datetime.strptime(text, "%d.%m.%y %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=BERLIN)


def _disallows_draw(row_text: str) -> bool:
    normalized = row_text.casefold()
    return any(marker in normalized for marker in NO_DRAW_MARKERS)


def _has_class(tag: Tag, needle: str) -> bool:
    classes = tag.get("class") or []
    return any(needle in c for c in classes)


def fetch_open_games(session, config: Config) -> tuple[list[OpenGame], dict[str, str], str]:
    """Fetch the tipping page and return (games, form_data, submit_url).

    `form_data` holds every field of the tipping form as it currently is
    (all matches, not just the ones we touch) - Kicktipp expects the whole
    form back on submit, same as a browser would send it. `fill_tip()`
    below just overwrites the relevant entries in that dict before it's
    posted.
    """
    response = session.get(config.tippabgabe_url, timeout=20)
    response.raise_for_status()
    return _parse_tippabgabe_html(response.text, response.url, config.tippabgabe_url)


def _parse_tippabgabe_html(
    html: str, page_url: str, fallback_action: str
) -> tuple[list[OpenGame], dict[str, str], str]:
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find(id="tippabgabeSpiele")
    if table is None:
        raise TippingError("Tipping table (#tippabgabeSpiele) not found")

    form = table.find_parent("form")
    if form is None:
        raise TippingError("No <form> found around the tipping table")

    submit_url = urljoin(page_url, form.get("action") or fallback_action)
    form_data = _extract_form_fields(form)

    body = table.find("tbody") or table
    rows = body.find_all("tr", recursive=False) or body.find_all("tr")

    games: list[OpenGame] = []
    last_kickoff: datetime | None = None

    for row in rows:
        cells = row.find_all("td", recursive=False) or row.find_all("td")
        if len(cells) < 3:
            # Not a game row (e.g. a matchday/date separator, if present).
            continue

        time_text = cells[0].get_text(strip=True)
        parsed = _parse_kickoff(time_text)
        if parsed is not None:
            last_kickoff = parsed
        kickoff = last_kickoff
        if kickoff is None:
            logger.debug("Skipping row with no known kickoff time yet")
            continue

        home_team = cells[1].get_text(strip=True)
        away_team = cells[2].get_text(strip=True)
        if not home_team or not away_team:
            continue

        home_input = row.find("input", attrs={"name": re.compile("heimTipp")})
        away_input = row.find("input", attrs={"name": re.compile("gastTipp")})
        if home_input is None or away_input is None:
            # Game already finished or otherwise not tippable.
            continue

        home_field = home_input.get("name")
        away_field = away_input.get("name")
        already_tipped = bool(home_input.get("value")) and bool(away_input.get("value"))

        games.append(
            OpenGame(
                home_team=home_team,
                away_team=away_team,
                kickoff=kickoff,
                home_field=home_field,
                away_field=away_field,
                already_tipped=already_tipped,
                allow_draw=not _disallows_draw(row.get_text(" ", strip=True)),
                row=row,
            )
        )

    return games, form_data, submit_url


def _extract_form_fields(form: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}

    for input_tag in form.find_all("input"):
        name = input_tag.get("name")
        if not name:
            continue
        input_type = (input_tag.get("type") or "text").lower()
        if input_type in ("checkbox", "radio"):
            if input_tag.has_attr("checked"):
                fields[name] = input_tag.get("value", "on")
            continue
        if input_type in ("submit", "button", "image", "reset"):
            continue
        fields[name] = input_tag.get("value", "")

    for select_tag in form.find_all("select"):
        name = select_tag.get("name")
        if not name:
            continue
        selected = select_tag.find("option", selected=True) or select_tag.find("option")
        if selected is not None:
            fields[name] = selected.get("value", selected.get_text(strip=True))

    for textarea in form.find_all("textarea"):
        name = textarea.get("name")
        if name:
            fields[name] = textarea.get_text()

    submit_button = form.find("button", attrs={"name": "submitbutton"})
    if submit_button is not None:
        fields["submitbutton"] = submit_button.get("value", "")

    return fields


def extract_odds(row: Tag) -> Odds | None:
    container = row.find("div", class_=lambda c: c and "tippabgabe-quoten" in c)
    if container is None:
        container = row.find("td", class_=lambda c: c and "quoten" in c)
    if container is None:
        return None

    quote_elements = [
        el
        for el in container.find_all(class_=lambda c: c and "quote" in c)
        if el.find("span", class_=lambda c: c and "quote-label" in c)
    ]

    mapping: dict[str, str] = {}
    for element in quote_elements:
        label_el = element.find("span", class_=lambda c: c and "quote-label" in c)
        text_el = element.find("span", class_=lambda c: c and "quote-text" in c)
        if label_el is None or text_el is None:
            continue
        label = label_el.get_text(strip=True)
        value = text_el.get_text(strip=True)
        if label and value:
            mapping[label] = value

    if not {"1", "X", "2"} <= mapping.keys():
        return None

    def to_float(raw: str) -> float:
        return float(raw.replace(",", "."))

    try:
        return Odds(
            home=to_float(mapping["1"]), draw=to_float(mapping["X"]), away=to_float(mapping["2"])
        )
    except ValueError:
        return None


def fill_tip(form_data: dict[str, str], game: OpenGame, home_goals: int, away_goals: int) -> None:
    form_data[game.home_field] = str(home_goals)
    form_data[game.away_field] = str(away_goals)


def submit_tips(session, submit_url: str, form_data: dict[str, str]) -> None:
    response = session.post(submit_url, data=form_data, timeout=20)
    response.raise_for_status()

    if "nicht alle gesendeten tipps waren korrekt" in response.text.casefold():
        raise TippingError("Kicktipp rejected the submitted tips - check the tipping page manually")
