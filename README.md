# Khodrobeen 🚗

**Khodrobeen** (خودروبین — "car-seer") is an automated deal-finder for used
cars on [Divar](https://divar.ir), Iran's largest classifieds marketplace.
It continuously monitors new car listings in cities you choose, gets each
one appraised by [Hamrah Mechanic](https://www.hamrah-mechanic.com)'s price
estimation tool, and pushes a Telegram notification the moment it finds a
listing priced below its estimated market value.

> Persian documentation: [README.fa.md](README.fa.md)

---

## How it works

```
   APScheduler (scan cycle, every N minutes)
        │
        ▼
   Divar scraper (Playwright) ──► Hamrah Mechanic price estimator (Playwright)
        │                                        │
        └───────────────► Deal checker ◄─────────┘
                        (compare & persist)
                                │
                                ▼
                    SQLite / SQLAlchemy
                                │
                 ┌──────────────┴──────────────┐
                 ▼                              ▼
         Flask admin panel              Telegram bot (aiogram)
        (stats, user management)     (city picker, deal alerts)
```

Everything runs as a single process (`main.py`): the admin panel runs on a
background thread, while the Telegram bot and scheduler share the main
asyncio event loop. This lets the whole stack fit on one small/free web
service instance.

## Features

- 🏙 **Per-user city selection** via inline Telegram keyboards — no need to
  edit config files to change what's being watched
- 🔍 **Automated Divar scraping** for new car/pickup listings in every
  watched city, with a global de-duplication table so nothing is processed
  twice
- 💰 **Automatic price appraisal** against Hamrah Mechanic's estimation
  tool, including a proper mapping from Divar's ten standardized
  body-condition categories to Hamrah Mechanic's per-part checkboxes
- 📲 **Instant Telegram alerts** whenever a listing's asking price is below
  its estimated value, with the listing link, price, and estimate attached
- 🖥 **Right-to-left Persian admin panel** (Flask) for viewing stats and
  managing subscribers
- 💳 **Extensible subscription system** — manual activation today, wired
  through a `PaymentProvider` interface so a real gateway (e.g. Zarinpal)
  can be added later without touching the bot, the panel, or the database
  models
- 🌐 **Optional proxy support** for the Telegram connection, since
  `api.telegram.org` is filtered inside Iran but a server deployed outside
  Iran (e.g. Render) needs none of this

## Tech stack

| Layer | Technology |
|---|---|
| Scraping / browser automation | [Playwright](https://playwright.dev) (Chromium, headless) |
| Telegram bot | [aiogram](https://docs.aiogram.dev) 3.x |
| Admin panel | [Flask](https://flask.palletsprojects.com) + Jinja2 |
| Database | SQLite via [SQLAlchemy](https://www.sqlalchemy.org) 2.x (swap `DATABASE_URL` for Postgres at scale) |
| Scheduling | [APScheduler](https://apscheduler.readthedocs.io) |
| Production server | [Waitress](https://docs.pylonsproject.org/projects/waitress/) |

## Project structure

```
app/
  config.py              Environment-driven settings (.env)
  db/                     SQLAlchemy models & session
  services/
    divar_client.py        Divar scraper (Playwright)
    price_estimator.py     Hamrah Mechanic form automation (Playwright)
    deal_checker.py         Price comparison logic
    notifier.py              Telegram message formatting/sending
    subscription.py          User & subscription business logic
    payment/                 Pluggable payment-provider interface
  bot/                    Telegram bot (aiogram) — handlers & keyboards
  web/                    Admin panel (Flask) — routes & templates
  scheduler/              Periodic scan job (APScheduler)
  constants/cities.py     Divar city slug ↔ Persian name mapping
main.py                  Entry point — runs everything together
```

## Getting started

### Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (for admin access) — get it from
  [@userinfobot](https://t.me/userinfobot)

### Local setup

```bash
git clone https://github.com/<your-username>/khodrobeen.git
cd khodrobeen

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env with your values — see Configuration below
```

Generate the admin panel's password hash:

```bash
python -c "from app.web.auth import hash_password; print(hash_password('your-password'))"
```
Paste the output into `ADMIN_PANEL_PASSWORD_HASH` in `.env`.

Run it:

```bash
python main.py
```

- Telegram bot: send `/start` to your bot
- Admin panel: `http://localhost:8000`
- Health check (for uptime monitoring): `http://localhost:8000/health`

### Configuration

All settings live in `.env` (see `.env.example` for the full list with
comments). The most important ones:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot's token from BotFather |
| `ADMIN_TELEGRAM_IDS` | Comma-separated Telegram user IDs with admin access |
| `DATABASE_URL` | SQLAlchemy connection string (defaults to local SQLite) |
| `ADMIN_PANEL_PASSWORD_HASH` | Hash generated as shown above |
| `POLL_INTERVAL_SECONDS` | How often to scan Divar (default: 180) |
| `TELEGRAM_PROXY_URL` | Optional — only needed for local dev inside Iran, where Telegram is filtered |
| `PAYMENT_PROVIDER` | `manual` by default; see [Adding a real payment gateway](#adding-a-real-payment-gateway) |

## Deploying to Render (free tier)

A ready-to-use `render.yaml` blueprint is included.

1. Push this repo to GitHub.
2. In Render, create a new **Blueprint** from the repo.
3. Fill in the environment variables marked `sync: false` in the Render UI
   (bot token, admin IDs, password hash).
4. Once live, add an [UptimeRobot](https://uptimerobot.com) HTTP monitor on
   `https://<your-app>.onrender.com/health` (5-minute interval) to keep the
   free instance awake.

### ⚠️ Free tier has no persistent disk

Render's free plan doesn't persist disk storage across deploys/restarts, so
a local SQLite file will be wiped on every redeploy. For anything beyond a
demo, point `DATABASE_URL` at a free external Postgres instance (e.g.
[Neon](https://neon.tech) or [Supabase](https://supabase.com)) — no code
changes needed, just install `psycopg2-binary` and update the URL.

### ⚠️ Headless browser memory usage

Playwright's Chromium needs more RAM than a typical small web service.
Render's free 512MB tier may be slow or unstable under load. If you hit
memory/timeout errors, increase `POLL_INTERVAL_SECONDS` and lower
`MAX_LISTINGS_PER_SCAN`, or run the scraper/scheduler on a small VPS
instead (the `app/services` and `app/scheduler` packages have no Flask
dependency, so they're easy to split out).

## Selector maintenance

Divar's `robots.txt` disallows automated crawlers, and both Divar's and
Hamrah Mechanic's frontend markup change over time. Every CSS/DOM selector
used for scraping lives in one `SELECTORS` dictionary at the top of each
file, so updates never require touching the surrounding logic:

- `app/services/divar_client.py`
- `app/services/price_estimator.py` — also see `DIVAR_BODY_STATUS_MAP`,
  which maps Divar's ten standardized body-condition categories (e.g.
  "رنگ‌شدگی، در ۲ ناحیه") to Hamrah Mechanic's per-part checkboxes

Because Hamrah Mechanic is a Next.js app using CSS Modules, most class
names look like `detailRow_car-detail__car-name__fhOg7` — the `__fhOg7`
suffix is a build hash that changes on every deploy, while the prefix
before it stays stable. Selectors use `[class*="..."]` on that stable
prefix instead of matching the full class, so they survive future
rebuilds. If scraping starts failing, open the page in Chrome DevTools,
inspect the element in question, and update the relevant entry.

Please also keep `REQUEST_DELAY_SECONDS` reasonable — this project is
meant for personal, low-volume monitoring, not high-throughput crawling.

## Admin commands (Telegram)

```
/admin_users                            List all users
/admin_activate <telegram_id> <days>    Activate/extend a subscription
/admin_disable <telegram_id>            Disable a user
/admin_enable <telegram_id>             Re-enable a user
```

The same actions are also available from the web admin panel.

## Adding a real payment gateway

The subscription system is built around a `PaymentProvider` interface so a
real gateway can be dropped in without touching the bot, the web panel, or
the database models:

1. Create a new class in `app/services/payment/` that implements
   `PaymentProvider` (see `base.py`) — e.g. `ZarinpalPaymentProvider`.
2. Register it in `get_payment_provider()` in
   `app/services/payment/__init__.py`.
3. Set `PAYMENT_PROVIDER=zarinpal` in `.env`.

Everything downstream goes through `activate_subscription()` in
`app/services/subscription.py`, so that's the only choke point that needs
to stay correct.

## Known limitations

- Divar has no public API for reading listings; this project scrapes
  public pages, which is against Divar's `robots.txt`. It's built for
  personal use at a low request rate, not commercial-scale crawling.
- Brand/model splitting from Divar's combined text field
  (`deal_checker.py::_split_brand_model`) is best-effort and may need
  refinement for less common car names.
- Mapping Divar's body-condition category to *which* specific panels were
  painted/replaced on Hamrah Mechanic's form is necessarily approximate,
  since Divar only reports how extensive the damage is, not which panels.
- SQLite is fine for a small deployment; migrate to Postgres for anything
  larger (just change `DATABASE_URL`).

## License

No license has been chosen yet — all rights reserved by default until one
is added.
