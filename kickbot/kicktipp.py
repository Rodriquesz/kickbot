"""Selenium interactions with kicktipp.de: login, reading open games, submitting tips."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import Config
from .predictor import Odds

logger = logging.getLogger("kickbot")

BERLIN = ZoneInfo("Europe/Berlin")

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
    home_input: WebElement
    away_input: WebElement
    already_tipped: bool
    allow_draw: bool
    row: WebElement


def dismiss_consent_dialog(driver, timeout: float = 10) -> None:
    """Dismiss the Sourcepoint cookie/ad-consent overlay if it's showing.

    Kicktipp shows this on every fresh browser profile/session. Left in
    place it can block clicks on elements underneath it (e.g. the tip
    submit button) and can prevent the ad-served quote widgets from
    loading at all. Persisting the Chrome profile (see browser.py) means
    this is normally only needed once, but we check on every page load
    since consent can be reset or shown again.

    The consent script itself loads asynchronously after the page has
    otherwise finished loading, so the timeout here is intentionally
    generous - a run happens at most every few minutes via cron, so a
    few extra seconds of waiting is cheap compared to missing the frame
    and leaving the overlay in place.
    """
    try:
        iframe = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'iframe[id*="sp_message_iframe"]'))
        )
    except TimeoutException:
        return

    try:
        driver.switch_to.frame(iframe)
        accept_button = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    '//button[contains(., "Akzeptieren") or contains(., "Zustimmen") '
                    'or contains(., "Einverstanden")]',
                )
            )
        )
        accept_button.click()
        logger.debug("Dismissed cookie/ad consent dialog")
    except TimeoutException:
        logger.debug("Consent iframe present but no accept button found")
    finally:
        driver.switch_to.default_content()


def login(driver, config: Config) -> None:
    driver.get(config.LOGIN_URL)
    wait = WebDriverWait(driver, 15)

    try:
        wait.until(EC.presence_of_element_located((By.ID, "kennung")))
    except TimeoutException as exc:
        raise LoginError("Login page did not load (kennung field not found)") from exc

    dismiss_consent_dialog(driver)

    driver.find_element(By.ID, "kennung").send_keys(config.username)
    driver.find_element(By.ID, "passwort").send_keys(config.password)
    driver.find_element(By.NAME, "submitbutton").click()

    try:
        wait.until(lambda d: "profil/login" not in d.current_url)
    except TimeoutException as exc:
        raise LoginError("Still on login page after submitting - check credentials") from exc

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


def fetch_open_games(driver) -> list[OpenGame]:
    wait = WebDriverWait(driver, 15)
    try:
        wait.until(EC.presence_of_element_located((By.ID, "tippabgabeSpiele")))
    except TimeoutException as exc:
        raise TippingError("Tipping table (#tippabgabeSpiele) not found") from exc

    rows = driver.find_elements(By.XPATH, '//*[@id="tippabgabeSpiele"]/tbody/tr')

    games: list[OpenGame] = []
    last_kickoff: datetime | None = None

    for row in rows:
        row_class = row.get_attribute("class") or ""
        if "datarow" not in row_class:
            continue

        try:
            time_text = row.find_element(By.XPATH, "./td[1]").text
        except NoSuchElementException:
            time_text = ""

        parsed = _parse_kickoff(time_text)
        if parsed is not None:
            last_kickoff = parsed
        kickoff = last_kickoff
        if kickoff is None:
            logger.debug("Skipping row with no known kickoff time yet")
            continue

        try:
            home_team = row.find_element(By.XPATH, "./td[2]").text.strip()
            away_team = row.find_element(By.XPATH, "./td[3]").text.strip()
        except NoSuchElementException:
            continue

        try:
            home_input = row.find_element(By.XPATH, './/input[contains(@name, "heimTipp")]')
            away_input = row.find_element(By.XPATH, './/input[contains(@name, "gastTipp")]')
        except NoSuchElementException:
            # Game already finished or otherwise not tippable.
            continue

        already_tipped = bool(home_input.get_attribute("value")) and bool(
            away_input.get_attribute("value")
        )

        games.append(
            OpenGame(
                home_team=home_team,
                away_team=away_team,
                kickoff=kickoff,
                home_input=home_input,
                away_input=away_input,
                already_tipped=already_tipped,
                allow_draw=not _disallows_draw(row.text),
                row=row,
            )
        )

    return games


def extract_odds(row: WebElement) -> Odds | None:
    container = None
    for xpath in (
        './/div[contains(@class, "tippabgabe-quoten")]',
        './/td[contains(@class, "quoten")]',
    ):
        try:
            container = row.find_element(By.XPATH, xpath)
            break
        except NoSuchElementException:
            continue

    if container is None:
        return None

    quote_elements = container.find_elements(
        By.XPATH,
        './/*[contains(@class, "quote")][.//span[contains(@class, "quote-label")]]',
    )

    mapping: dict[str, str] = {}
    for element in quote_elements:
        try:
            label = element.find_element(
                By.XPATH, './/span[contains(@class, "quote-label")]'
            ).text.strip()
            value = element.find_element(
                By.XPATH, './/span[contains(@class, "quote-text")]'
            ).text.strip()
        except NoSuchElementException:
            continue
        if label and value:
            mapping[label] = value

    if not {"1", "X", "2"} <= mapping.keys():
        return None

    def to_float(raw: str) -> float:
        return float(raw.replace(",", "."))

    try:
        return Odds(home=to_float(mapping["1"]), draw=to_float(mapping["X"]), away=to_float(mapping["2"]))
    except ValueError:
        return None


def fill_tip(game: OpenGame, home_goals: int, away_goals: int) -> None:
    game.home_input.clear()
    game.home_input.send_keys(str(home_goals))
    game.away_input.clear()
    game.away_input.send_keys(str(away_goals))


def submit_tips(driver) -> None:
    driver.find_element(By.NAME, "submitbutton").click()
    time.sleep(1)

    body_text = driver.find_element(By.TAG_NAME, "body").text.casefold()
    if "nicht alle gesendeten tipps waren korrekt" in body_text:
        raise TippingError("Kicktipp rejected the submitted tips - check the tipping page manually")
