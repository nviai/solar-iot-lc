# Solar Monitor

![Overview with the 3D energy flow](../docs/screenshots/solar-overview-dark.webp)

Monitoring for a SolaX solar installation — **production, consumption, grid
exchange and battery state** — with history, threshold alerts and a small,
stable **JSON API** that other apps (such as [`iot-control`](../iot-control))
read live power from.

FastAPI + Jinja2 + plain HTML/CSS/JS. No CDN, no build step. Charts are
hand-drawn SVG; storage is SQLite.

---

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip

cp .env.example .env      # fill in SOLAX_CLIENT_ID and SOLAX_CLIENT_SECRET

.venv/bin/python scripts/backfill.py --days 7    # optional: load past data
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

Open http://localhost:8000 — interactive API docs at http://localhost:8000/docs.

Both credentials come from an application on https://developer.solaxcloud.com →
**Application → Authorization Key**. Devices are discovered from the account
automatically.

No credentials yet? Set `DEMO_MODE=true` and run `scripts/seed_demo.py --days 14`.

### Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `SOLAX_CLIENT_ID` / `SOLAX_CLIENT_SECRET` | Application credentials (required) |
| `OPENAPI_BASE` | Regional gateway; the wrong region answers `10400` |
| `DEMO_MODE` | Synthetic data, no credentials needed |
| `POLL_INTERVAL_SECONDS` | Initial poll interval (default 60); editable later on `/settings` |
| `ENERGY_PRICE` / `CURRENCY` | Cost estimates on the history page; 0 hides them |
| `ALERT_WEBHOOK_URL` | Push alerts to Slack, Discord or any JSON endpoint |
| `DEVICES` | Restrict polling to specific registration numbers |
| `CALLBACK_SECRET` | Shared secret for inbound data-subscription callbacks |
| `CORS_ORIGINS` | Browser origins allowed to call the API directly |
| `TIMEZONE` | IANA zone quiet hours are read in (default `Asia/Ho_Chi_Minh`) |

Thresholds and the polling schedule are **not** environment variables — they are
edited in the UI and stored in SQLite, so they survive restarts and change
without a redeploy.

---

## Pages

| Page | Contents |
|---|---|
| `/` | Live KPI tiles with sparklines, 3D energy-flow scene, 6h–7d power chart, energy by day, today's counters |
| `/history` | Totals for the range, daily roll-up, battery state-of-charge trace, energy table with cost estimates |
| `/alerts` | Create, edit, enable and delete threshold rules; timeline of what fired |
| `/settings` | SolaX connection health, public API, polling schedule and quiet hours, live device fields, callback address |
| `/docs` | OpenAPI reference for the JSON API |

Light and dark themes follow the system and can be toggled; the layout collapses
to a drawer on phones.

---

## Public API

Everything lives under `/api/v1`. Read endpoints are served from memory or the
local database and **never spend SolaX quota**, however often they are called.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/power` | **Aggregated live power** — the integration endpoint |
| `GET` | `/api/v1/devices` | Latest full reading for every device |
| `GET` | `/api/v1/devices/{register_no}` | Latest full reading for one device |
| `GET` | `/api/v1/devices/{register_no}/history?hours=24` | Stored power samples |
| `GET` | `/api/v1/devices/{register_no}/daily?days=14` | Energy per day (kWh) |
| `GET` | `/api/v1/status` | Poller health, unread alerts |
| `POST` | `/api/v1/refresh` | Poll SolaX now (costs quota) |
| `GET` | `/api/v1/alarms` | Alarms raised by SolaX itself (costs quota) |
| `GET/POST/PATCH/DELETE` | `/api/v1/alerts/rules[/{id}]` | Threshold rules |
| `GET` / `POST` | `/api/v1/alerts/events`, `/api/v1/alerts/events/ack` | Alert timeline |
| `GET` / `PUT` | `/api/v1/schedule` | Polling schedule and quiet hours |

### `GET /api/v1/power`

```json
{
  "measured_at": "2026-10-14T05:12:31.402Z",
  "age_seconds": 18.4,
  "poll_interval_seconds": 60,
  "paused": false,
  "totals": {
    "pv_power": 7220.3, "load_power": 993.6,
    "grid_power": 2802.0, "grid_import": 0.0, "grid_export": 2802.0,
    "battery_power": 3424.7, "battery_charge": 3424.7, "battery_discharge": 0.0,
    "soc": 63.9, "yield_today": 32.8
  },
  "devices": [
    { "register_no": "XSUGLNFHAP", "measured_at": "…", "age_seconds": 18.4,
      "pv_power": 7220.3, "…": "…", "status": "102", "status_label": "Normal", "error": null }
  ]
}
```

- Power in **W**, energy in **kWh**, state of charge in **%**.
- `grid_power` > 0 exports, < 0 imports; `battery_power` > 0 charges.
- `measured_at` is the *oldest* contributing reading, so the totals are only as
  fresh as it says. **Check `age_seconds` before acting on the figures** —
  during quiet hours (`paused: true`) it grows by design.
- `totals` is `null` until the first reading arrives.

The dashboard UI itself has no authentication: keep it on the LAN or put it
behind a reverse proxy that adds some.

---

## How the SolaX API is used

Verified against a live account — see [`docs/API-FINDINGS.md`](docs/API-FINDINGS.md)
for details and the approaches that turned out to be dead ends.

**Authentication** is OAuth2 client credentials:

```
POST {OPENAPI_BASE}/openapi/auth/oauth/token      (form-encoded)
     client_id, client_secret, grant_type=client_credentials
  -> {"code":0,"result":{"access_token":"...","expires_in":2591999}}

Every later request:  Authorization: bearer {access_token}
```

Tokens last about 30 days. Issuing a new token invalidates the previous one, so
the client only refreshes reactively, when a call is rejected.

| Endpoint | Purpose |
|---|---|
| `GET /openapi/v2/device/page_device_info` | Discover devices, map registration numbers to serials |
| `GET /openapi/v2/device/realtime_data` | Telemetry; `deviceType=1` inverter, `=2` battery |
| `GET /openapi/v2/plant/realtime_data` | Site-level energy counters |
| `GET /openapi/v2/device/history_data` | Historical samples for the backfill script |
| `GET /openapi/v2/alarm/page_alarm_info` | Alarms raised by SolaX itself |

Response codes: `0` token issued · `10000` success · `10400` bad credentials or
wrong region · `10403` application not authorised for the endpoint · `10429`
rate limit exceeded.

### Deriving the power flows

Consumption is not reported directly. Sign conventions, verified against real
history:

```
gridPower            > 0 exports to the grid, < 0 imports
chargeDischargePower > 0 charges the battery
acPower1..3          inverter AC output, already net of battery charging

pv_power   = MPPTTotalInputPower          (falls back to the sum of mpptMap)
load_power = ac_power - grid_power        (DC-side balance during an outage)
```

### Rate limits and quiet hours

Each poll costs 2–3 API calls per device (inverter, battery, plant totals). At
60 s with one site that is about 4,300 calls/day. Quiet hours (set on
`/settings`, default 23:00–05:00) either stretch the interval or pause polling
completely; the refresh button always polls immediately.

---

## Callback address

Point the **Callback Address** of your developer-portal application at
`https://<public-host>/callback/solax`. SolaX pushes Data Subscription records
and asynchronous control results there. The endpoint must be reachable from the
internet (use a tunnel such as `ngrok http 8000` in development) and answer 2xx
quickly. Any JSON shape is accepted, logged on `/settings`, and ingested when it
looks like telemetry. Set `CALLBACK_SECRET` to require a shared secret.

---

## Layout

```
app/
  main.py         FastAPI app and lifespan (starts the poller)
  config.py       Settings from .env
  client.py       SolaX Developer Platform client (auth, discovery, telemetry)
  models.py       Reading: normalisation and derived power flows
  telemetry.py    Aggregation into the public power snapshot
  schemas.py      Public API request/response models
  storage.py      SQLite: readings, alert rules, events, callbacks, settings
  alerts.py       Threshold engine (duration, cooldown, resolved events)
  poller.py       Background loop, including quiet hours
  runtime.py      UI-editable settings: declaration, validation, persistence
  demo.py         Synthetic telemetry
  deps.py         Singletons and FastAPI dependencies
  routers/        pages, telemetry, alerts, schedule, callbacks
  templates/      base + overview / history / alerts / settings
  static/
    css/base.css      Design system (shared with iot-control — keep identical)
    css/monitor.css   Charts, energy flow, page specifics
    js/core.js        Helpers (shared with iot-control — keep identical)
    js/fx.js          WebGL aurora, 3D tilt, value animations (shared — keep identical)
    js/charts.js      SVG line/bar charts with crosshair tooltips
    js/flow.js        Energy-flow diagram (CSS 3D scene: roof panels, pylon, battery, house)
scripts/
  backfill.py     Load real history from SolaX
  seed_demo.py    Generate synthetic history
```

## Deployment

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or with Docker from the repository root: `docker compose up -d solar-monitor`.
Behind a reverse proxy, run uvicorn with `--proxy-headers` so the callback and
API URLs shown on `/settings` use the right scheme and host.
