# IoT Control

![Charging bay](../docs/screenshots/iot-devices-dark.webp)

Drives smart switches over the **local network** — above all the relay feeding
the EV charger — and switches them on surplus solar by day and on the
off-peak tariff by night. Power figures come from the [Solar Monitor](../solar-monitor) public API
(`GET /api/v1/power`); this app never calls SolaX itself, so it spends none of
the SolaX API quota however often it evaluates.

FastAPI + Jinja2 + plain HTML/CSS/JS, SQLite for settings and the activity log.
No CDN, no build step.

```
Solar Monitor  ──GET /api/v1/power──▶  IoT Control  ──Tuya LAN protocol──▶  switches
 (port 8000)        (from memory)        (port 8100)
```

---

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip

cp .env.example .env                  # point SOLAR_API_URL at the monitor
cp devices.example.json devices.json  # describe your switches

.venv/bin/uvicorn app.main:app --port 8100 --reload
```

Open http://localhost:8100. The Solar Monitor must be running and reachable at
`SOLAR_API_URL`.

### Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `SOLAR_API_URL` | Where this app reads `/api/v1/power` (default `http://localhost:8000`) |
| `SOLAR_PUBLIC_URL` | Monitor URL for the browser link; defaults to `SOLAR_API_URL` |
| `SOLAR_POLL_SECONDS` | Seconds between reads of the monitor (default 10) |
| `SOLAR_TIMEOUT_SECONDS` | Per-request timeout (default 5) |
| `DEVICES_FILE` | Device inventory, relative to this folder (default `devices.json`) |
| `DATABASE_PATH` | SQLite file (default `data/iot.db`) |
| `RETENTION_DAYS` | Activity-log retention (default 90) |
| `TIMEZONE` | Starting time zone for the automation windows (default `Asia/Ho_Chi_Minh`) |
| `OFFPEAK_START` / `OFFPEAK_END` | Starting off-peak window (default `20:30` / `04:30`) |

Thresholds, time windows and the time zone are edited per device in the UI and
stored in SQLite, so they survive restarts and change without a redeploy. The
last three variables above are only the values a device starts with until its
settings are first saved.

### Devices (`devices.json`)

```json
{
  "devices": [
    {
      "id": "ev-charger",
      "name": "EV charger",
      "kind": "tuya_switch",
      "description": "Relay feeding the EV charger",
      "tuya": {
        "device_id": "bf0123456789abcdefghij",
        "ip": "192.168.1.50",
        "local_key": "…",
        "version": 3.4,
        "dp": 1
      }
    }
  ]
}
```

- `id` — URL slug (`a-z`, `0-9`, `-`, `_`); automation settings are stored under it.
- `tuya.device_id` / `tuya.local_key` — from `python -m tinytuya wizard` (its `devices.json`).
- `tuya.ip` / `tuya.version` — from the wizard's `snapshot.json` (`ver`, usually 3.3 or 3.4).
  Give the switch a DHCP reservation so the IP never moves.
- `tuya.dp` — DPS index of the relay; 1 is channel 1 on a single-gang switch.
- `"kind": "demo_switch"` (no `tuya` block) is a simulated relay that is always
  online — handy for trying the UI and the schedules without hardware.

The file holds secrets and is git-ignored. A missing or invalid file does not
stop the app: it starts with no devices and the System page says what is wrong.

---

## Automation

Each device has its own controller, evaluated every `tick_seconds` (default 30s).
Two schedules can be armed independently, typically both at once — solar by
day, off-peak by night:

| | Solar mode (day) | Off-peak mode (night) |
|---|---|---|
| Window | 08:30–16:30 | 20:30–04:30 |
| Inside the window | Follows production (hysteresis below) | **On** for the whole window |
| When the window ends | Off, unless "switch off when the window closes" is unticked | **Off**, always |
| Needs solar data | Yes | No |
| Reacts | After `sustain_s` | Immediately |

- **Off-peak wins where the windows overlap.** Outside every armed window the
  device follows whichever window closed most recently: after off-peak it stays
  off, after the solar window it obeys the switch-off option.
- **Time zone per device.** Both windows are read in the device's IANA zone,
  picked in the UI (any zone from the browser's list, or this browser's own).
  Windows may cross midnight.
- **Hysteresis, not a single threshold.** On at ≥ 5 kW, off at ≤ 1 kW by
  default; between the two the relay holds. A load drawing several kW would
  otherwise pull production back under a single threshold the moment it started
  and oscillate.
- **The solar window** (08:30–16:30 by default). Solar mode never switches a
  device on outside it. `00:00–00:00` means always; the off-peak window must
  have different ends.
- **Confirm for (`sustain_s`, default 120s).** A crossed threshold must stay
  crossed this long before the relay moves, which absorbs passing clouds.
- **Master switch.** With automation disarmed the window and thresholds are
  ignored entirely — the relay only moves when you move it.
- **Manual override.** Pressing On or Off stands automation down for 30 minutes
  (configurable; 0 disables the grace period). A button hands control back early.
- **Missing or stale data is a reason to stop** (solar mode). If the Solar Monitor is
  unreachable, has no reading, or its reading is older than `stale_after_s`
  (default 10 min), automation switches the device off rather than keep it
  running on figures it cannot confirm.

Every relay movement is recorded with its cause and the solar figure at the time
(Activity page, `GET /api/v1/events`). The `source` is `manual`, `auto` (solar
mode) or `offpeak`.

![Charger detail in off-peak mode](../docs/screenshots/iot-device-offpeak-dark.webp)

> **One connection at a time.** Tuya switches accept a single local session.
> Two instances of this app against the same device — a dev server alongside the
> container — or the vendor app holding a LAN session will make both report it
> unreachable. Run one.

---

## API

Interactive reference at `/docs`.

| Method & path | Purpose |
|---|---|
| `GET /api/v1/status` | Solar-feed health, device counts, inventory errors |
| `GET /api/v1/solar?refresh=` | Latest power snapshot from the monitor (`refresh=true` fetches now) |
| `GET /api/v1/devices` | Every device with switch state, automation config and current decision (`mode`, `next_change`, `local_time`) |
| `GET /api/v1/devices/{id}` | One device, plus field metadata and recent activity |
| `POST /api/v1/devices/{id}/switch` | `{"on": true}` — switch by hand (arms the override) |
| `POST /api/v1/devices/{id}/refresh` | Re-read the relay and re-run the rules now |
| `POST /api/v1/devices/{id}/override/clear` | Hand control back to automation |
| `PUT /api/v1/devices/{id}/automation` | Partial update of the automation settings (422 on invalid values), e.g. `{"offpeak_enabled": true, "offpeak_start": "22:00", "offpeak_end": "05:00", "timezone": "Asia/Ho_Chi_Minh"}` |
| `GET /api/v1/devices/{id}/events?limit=` | Activity for one device |
| `GET /api/v1/events?limit=&device=` | Activity across all devices |

Unknown device ids answer 404; switching a device whose driver is unavailable
answers 409; a switch that does not respond answers 502.

---

## Docker

```bash
docker build -t iot-control .
docker run -d --name iot-control --network host \
  --env-file .env -e SOLAR_API_URL=http://127.0.0.1:8000 \
  -v "$PWD/devices.json:/app/devices.json:ro" \
  -v iot-data:/app/data \
  iot-control
```

The Tuya LAN protocol needs the container to reach the switches directly, so
host networking (Linux) is the simplest setup. On Docker Desktop (macOS/Windows)
bridge networking with a routable LAN usually works for outbound TCP to the
switch IP; `tinytuya` discovery broadcasts do not, but they are not needed when
the IP is set in `devices.json`.

From the repository root, `docker compose up -d` runs both apps on a bridge
network with `SOLAR_API_URL=http://solar-monitor:8000`. If a switch is
unreachable from there, switch this service to `network_mode: host` and point
`SOLAR_API_URL` at `http://127.0.0.1:8000`.

---

## Layout

```
app/
  main.py         FastAPI app and lifespan (starts the feed and controllers)
  config.py       Settings from .env
  registry.py     devices.json schema and loader
  solar.py        SolarFeed: reads /api/v1/power from the Solar Monitor
  tuya.py         LAN control of one relay (async wrapper over tinytuya)
  demo.py         Simulated relay for "kind": "demo_switch"
  automation.py   DeviceController: solar + off-peak schedules, hysteresis, sustain, override
  runtime.py      UI-editable settings: declaration, validation, persistence
  storage.py      SQLite: settings and the activity log
  deps.py         Singletons and FastAPI dependencies
  routers/        pages, devices API, system API
  templates/      base / devices / device / activity / system; partials/garage.html (3D bay, 24 h dial)
  static/         base.css + core.js + fx.js (shared design system), control.css, page scripts,
                  img/ (Lynk & Co 02 cut-outs, CC BY-SA 4.0 — see the root README)
```
