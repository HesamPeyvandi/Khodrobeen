import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from a .env file in the project root (if present) into the
# process environment. Without this, `python main.py` on Windows (and any
# shell where you haven't manually `set`/`export`-ed each variable) will
# never see the values you put in .env, and every setting below silently
# falls back to its default.
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_telegram_ids: list[int] = field(
        default_factory=lambda: [
            int(x) for x in _get_list("ADMIN_TELEGRAM_IDS", []) if x.isdigit()
        ]
    )
    # Optional proxy for reaching api.telegram.org (Telegram is filtered inside
    # Iran). Not needed at all when deployed on a server outside Iran (e.g.
    # Render) - only useful for running the bot locally without a full
    # system-wide VPN. Example: http://127.0.0.1:2080 or socks5://127.0.0.1:1080
    telegram_proxy_url: str = os.getenv("TELEGRAM_PROXY_URL", "")

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    )

    # Web admin panel
    flask_secret_key: str = os.getenv("FLASK_SECRET_KEY", "change-me-in-prod")
    admin_panel_username: str = os.getenv("ADMIN_PANEL_USERNAME", "admin")
    # Store only a password hash. Generate with app.web.auth.hash_password
    admin_panel_password_hash: str = os.getenv("ADMIN_PANEL_PASSWORD_HASH", "")
    web_host: str = os.getenv("WEB_HOST", "0.0.0.0")
    web_port: int = _get_int("WEB_PORT", 8000)

    # Scraper / scheduler behaviour
    poll_interval_seconds: int = _get_int("POLL_INTERVAL_SECONDS", 180)
    request_delay_seconds: float = float(os.getenv("REQUEST_DELAY_SECONDS", "2.5"))
    max_listings_per_scan: int = _get_int("MAX_LISTINGS_PER_SCAN", 30)
    headless_browser: bool = _get_bool("HEADLESS_BROWSER", True)

    # Divar categories included in every scan (fixed by product decision)
    divar_categories: list[str] = field(
        default_factory=lambda: ["car", "pickup"]
    )

    # Free trial length for newly-registered bot users (days). 0 disables trial.
    default_trial_days: int = _get_int("DEFAULT_TRIAL_DAYS", 3)

    # Feature flag: whether a real payment gateway is wired in (see services/payment)
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "manual")


settings = Settings()
