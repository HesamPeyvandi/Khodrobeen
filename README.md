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
  managing subscribers, with all timestamps shown in Iran local time
  (UTC+3:30) regardless of what timezone the server itself runs in
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
test_divar.py            Quick smoke test: does Divar scraping work at all?
local_test_watch.py      Watch the full scrape → estimate pipeline in a
                          visible (non-headless) browser, one listing at a
                          time, without touching the database or Telegram
debug_divar_specs.py     Dumps candidate spec-row elements from a Divar
                          listing page, for fixing divar_client.py's
                          selectors when they stop matching
debug_hamrah_picker.py   Dumps every result item Hamrah Mechanic's car
                          picker shows for a query, with visibility/enabled
                          state, for fixing price_estimator.py's selectors
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

### ⚠️ Free tier has no persistent disk — set up free Postgres (Neon)

Render's free plan doesn't persist disk storage across deploys/restarts, so
a local SQLite file gets wiped every time you redeploy — including all
registered bot users and their city selections. `psycopg2-binary` is
already in `requirements.txt`, so switching to Postgres is just a
`DATABASE_URL` change:

1. Go to [neon.tech](https://neon.tech) and sign up for a free account.
2. Create a new project (any name/region is fine — pick a region close to
   your Render service if given a choice).
3. On the project dashboard, find the **connection string** — it looks
   like `postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`.
   Copy it.
4. In Render, open your service → **Environment** → edit `DATABASE_URL` →
   paste the Neon connection string → save. Render will redeploy
   automatically.
5. That's it — no code or schema changes needed. `init_db()` creates all
   tables automatically on first startup against the new database.

Neon's free tier auto-suspends the database after a period of inactivity
and resumes it on the next connection (this project's DB engine already
sets `pool_pre_ping=True` so it reconnects transparently instead of
erroring on a stale connection) — the only visible effect is the first
query after a period of silence being a bit slower while it wakes up.

### ⚠️ Headless browser memory usage

Playwright's Chromium needs more RAM than a typical small web service.
Render's free 512MB tier may be slow or unstable under load. If you hit
memory/timeout errors, increase `POLL_INTERVAL_SECONDS` and lower
`MAX_LISTINGS_PER_SCAN`, or run the scraper/scheduler on a small VPS
instead (the `app/services` and `app/scheduler` packages have no Flask
dependency, so they're easy to split out).

## How Hamrah Mechanic price estimation actually works

Hamrah Mechanic doesn't publish a documented public API, but its site is
Next.js, and this was confirmed by inspecting the real Network tab: once a
brand/model/year/trim is selected and "محاسبه قیمت" is clicked, the page
does a client-side route change to
`/carprice/{brand}/{model}/{year}/{typeId}/`, which Next.js resolves by
fetching
`/_next/data/{buildId}/carprice/{brand}/{model}/{year}/{typeId}.json?kilometer=...&clr=...&bodycondition=...&replacedparts=...`
and getting back structured JSON directly — no DOM scraping needed for the
result at all.

So `price_estimator.py` still uses Playwright to open the car picker and
select brand/model/year/trim (there's no separate confirmed search API for
that part) and to click submit (which is what triggers the route change),
but instead of also filling in the mileage/body-status/color UI and
scraping a result element back out of the DOM — both of which were the
source of nearly every bug this project hit early on (stuck modals,
disabled tabs, click timing, result-wait timing) — it reads the resolved
`{brand}/{model}/{year}/{typeId}` straight from the post-click URL, builds
the JSON data-URL itself with its own precise query parameters (computed
directly from `CarSpec`, no DOM interaction needed for mileage/color/body
status at all), fetches it with `page.request.get()` (same browser
session/cookies), and parses the JSON response directly. Far fewer moving
parts, and none of the fragile ones.

Two things worth knowing if this ever needs revisiting:
- The Next.js `buildId` embedded in the data-URL changes on every Hamrah
  Mechanic deploy — it's read fresh off the live page each time
  (`window.__NEXT_DATA__.buildId`) rather than hardcoded, so this doesn't
  need maintenance when their site updates.
- The real body-part identifiers (confirmed from a live API response's
  `bodyParts` field) are `Hood`, `Trunk`, `DoorFrontLeft`, `DoorBackLeft`,
  `DoorFrontRight`, `DoorBackRight`, `FenderFrontLeft`, `FenderBackLeft`,
  `FenderFrontRight`, `FenderBackRight`, `Roof` — notably no bumpers, which
  an earlier DOM-clicking version of this code had incorrectly guessed
  existed. See `DIVAR_BODY_STATUS_MAP` in `price_estimator.py` for how
  Divar's ten body-condition categories map to these.

## Troubleshooting: listing detail pages keep timing out

If `list_new_listings` finds listings fine but `get_listing_detail` keeps
logging `TimeoutError: Page.goto: Timeout ... exceeded` (navigation itself
timing out), this usually means Divar's network path is bad or actively
throttled from wherever you're hosting - see the network-path explanation
below. But if the error is `Locator.inner_text: Timeout ... exceeded`
instead (navigation succeeds, but reading a specific element hangs), that's
a different problem: a selector is matching something that never becomes
visible/stable. That was in fact the root cause of most real-world
failures seen while building this project - a guessed price selector
(`:text('تومان')`) was matching an element that hung for the full default
30s timeout on nearly every listing. The fix was to stop querying the DOM
separately for price and instead read it from the same reliable label/value
row-scan used for every other spec field (see `SPEC_LABELS["price"]` in
`divar_client.py`) - both `list_new_listings` and the label/value rows
already have short, guarded timeouts (2-3s) with try/except around each
item, so one bad row never blocks the whole page for 30s.

### The car picker modal used to never close

The brand/model/year/trim picker on Hamrah Mechanic opens inside a modal.
For a while, nothing explicitly closed it after picking a trim — it stayed
open on top of the page, blocking the submit click (this was the actual
cause behind most "not clickable" failures back when the DOM-based
mileage/body-status/color form filling was still in use — see the section
above on why that's gone now). `price_estimator.py`'s
`_close_car_picker_modal()` closes it (confirm button → Escape →
click-outside, polling a few times since closing can be animated) right
after trim selection, and `_dismiss_overlays()` clears common cookie/promo
popups right after page load too.

Two more related fixes:
- The year/trim tabs are checked for `aria-disabled="true"` before
  clicking — some cars genuinely have no trim data in Hamrah Mechanic, and
  clicking a disabled tab was both a wasted click *and* left the modal in a
  state where it couldn't close normally.
- If the submit button still fails specifically with "subtree intercepts
  pointer events" (a leftover modal confirmed to be the cause, not some
  other kind of failure), it's force-clicked through the stale overlay as
  a last resort — safe here specifically because the target element is
  confirmed to be the correct button, just visually blocked.

If the page never navigates to a resolved `/carprice/{brand}/{model}/
{year}/{typeId}/` URL after clicking submit, or the JSON API call itself
fails, the error message includes the page's current URL (or the API
response) plus a text excerpt — searched for common Persian
validation-error keywords first, rather than just grabbing whatever's at
the top of the page, which tends to be generic header/nav text. The
dashboard's failures table truncates long error messages for display —
hover a cell (or open the listing directly) to read the full text.

If `list_new_listings` finds listings fine but `get_listing_detail` keeps
logging `TimeoutError: Page.goto: Timeout ... exceeded`, this usually means
Divar's network path is bad or actively throttled from wherever you're
hosting. Iranian sites are often behind a CDN/anti-bot layer (Arvan Cloud is
common) that slow-walks or drops traffic that looks automated - and "loading
dozens of detail pages back-to-back from one non-Iranian IP" looks exactly
like a bot. Three settings in `.env` address this, roughly in order of
how likely they are to actually fix it:

1. `SCRAPER_PROXY_URL` - route the scraper's own browser traffic through a
   proxy with a good path into Iran. This fixes the underlying network
   problem rather than just waiting longer for a degraded connection, so
   try this first if (2) and (3) don't help.
2. `PAGE_TIMEOUT_MS` - raise this (e.g. to `90000`) if pages are loading,
   just slowly.
3. `PAGE_GOTO_RETRIES` - each page load is already retried this many times
   with backoff before giving up; raising it trades speed for resilience.

If the error is instead `page loaded but no content extracted (possible
rate limit)` - navigation succeeded but nothing rendered - the page can
still be an empty shell for a while after it technically finishes loading
under network stress, same underlying pattern as the Hamrah Mechanic wait
above. `DIVAR_DETAIL_RENDER_WAIT_MS` (default 8000) controls how long
`get_listing_detail` waits for real content (title or spec rows) before
giving up; the error message includes the page's title and a body-text
excerpt, which is usually enough to tell a genuine rate-limit/challenge
page apart from a normal slow load.

If scans are consistently slower than `POLL_INTERVAL_SECONDS`, you'll see
`apscheduler` log warnings like `maximum number of running instances
reached` - that's the built-in overlap guard working as intended, not a
bug, but it does mean scans are falling behind. Fixing the timeout issue
above should resolve it; as a stopgap you can also raise
`POLL_INTERVAL_SECONDS` and lower `MAX_LISTINGS_PER_SCAN`.

## Brand/model mapping table

Free-text matching against Hamrah Mechanic's search box is unreliable
since Divar and Hamrah Mechanic often name the same car differently.
`app/services/car_mapping.py` wraps a manually-reviewed mapping table
(`app/data/car_brand_mapping.json`, ~933 entries) that gives the *exact*
Hamrah Mechanic brand/model name for a given Divar listing, used instead
of guessing whenever a match exists:

- Confidently-matched rows (`status: "تطبیق یافت شد"`, 432 entries) are
  tried first.
- If nothing confident matches, lower-confidence "needs review" rows
  (`status: "نیاز به بررسی"`, 333 entries) are used as a fallback — still
  far more informed than free-text guessing.
- If neither has an entry, `deal_checker.py` falls back to the old
  heuristic (`_split_brand_model` + `DOMESTIC_MODEL_TO_BRAND`).

To extend coverage, edit the mapping and re-export to
`app/data/car_brand_mapping.json` (same shape as the existing entries).
`explore_hamrah_catalog.py` is a small interactive script for exploring
Hamrah Mechanic's brand → model drill-down navigation in a visible browser
if you need to verify new entries by hand.

## Debugging the scrapers locally

Both Divar and Hamrah Mechanic are React/Next.js apps whose markup changes
over time, so scraping breaks in ways that are much easier to diagnose by
*watching* a real browser than by reading server logs. Three scripts help
with that (run them from the project root, with your venv active):

- **`python local_test_watch.py [city_slug] [category_slug]`** — runs the
  real pipeline (grab a fresh listing → parse its specs → drive Hamrah
  Mechanic's form → print the estimate) in a visible browser window,
  without touching the database or sending anything to Telegram. This is
  the first thing to run after any selector change.
- **`python debug_divar_specs.py <listing_url>`** — opens a Divar listing
  and dumps every candidate spec-row element's class and text, to help
  find the right selector when `divar_client.py`'s extraction stops
  working.
- **`python debug_hamrah_picker.py "<query>"`** — opens Hamrah Mechanic's
  car picker, types your query, and dumps every result item's text, class,
  visibility, and enabled state — useful when a brand/model search isn't
  matching the way `price_estimator.py` expects.
- **`python explore_hamrah_catalog.py`** — walks Hamrah Mechanic's
  brand → model drill-down navigation for the first few brands in a
  visible browser, printing what each step reveals. Useful when extending
  `app/data/car_brand_mapping.json` with new entries.

All three force `HEADLESS_BROWSER=false` regardless of your `.env`, so you
can watch exactly what the page is doing.

## Tracking scrape/estimate failures in the admin panel

Every listing the scheduler attempts gets a row in the database — including
ones that failed — so nothing gets silently retried forever. The dashboard
shows:

- A **success rate** stat for the last 24 hours (successful estimates ÷
  attempted listings, excluding intentionally-skipped `اوراقی` listings
  from the denominator).
- A **failures table** listing which listings got stuck, at which stage
  (`دریافت اطلاعات دیوار` = Divar scraping, `تخمین قیمت` = Hamrah Mechanic
  estimation), with the error message and a link to the listing.

This is backed by two new columns on `SeenListing` (`status`,
`error_message`) — see `app/db/models.py::ListingStatus`. Since this
project doesn't use a migration tool (Alembic etc.), adding these columns
to an *already-populated* database won't happen automatically via
`init_db()`, which only creates missing tables, not missing columns on
existing ones. If you're upgrading an existing deployment and see a
database error mentioning `status` or `error_message`, drop the
`seen_listings` table (or the whole database, if using a fresh Neon
project anyway) and let it recreate on next startup.

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
