"""Chrome WebDriver setup."""

from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Persisted across runs so the cookie/ad-consent choice (and login session)
# survives between cron invocations instead of showing the consent banner
# on every single run.
PROFILE_DIR = Path(__file__).parent.parent / ".chrome-profile"


def build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--lang=de-DE")
    PROFILE_DIR.mkdir(exist_ok=True)
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)
