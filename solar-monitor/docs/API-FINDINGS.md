# SolaX API — findings from real requests

What was verified with real requests against a real account (2026-08-13),
because the public PDF documentation is out of date and describes a different
system.

## Conclusion: there are TWO separate API systems

| | Developer Platform (**use this one**) | tokenId API (legacy) |
|---|---|---|
| Host | `openapi-eu.solaxcloud.com`, `openapi.solaxcloud.com` | `www.solaxcloud.com`, `api-*.solaxcloud.com` |
| Authentication | `Client ID` + `Client Secret` → `access_token` | `tokenId` header |
| Header | `Authorization: bearer {access_token}` | `tokenId: {token}` |
| Where to get it | developer.solaxcloud.com → Application | solaxcloud.com → Service → API |
| Success code | `0` (token), `10000` (data) | `success: true` |
| Endpoints | 123 | 1 |

The two systems **do not share credentials**: a tokenId cannot call the OpenAPI
and vice versa. This app only uses the Developer Platform; all tokenId code has
been removed.

## 1. Authentication flow

```bash
curl -X POST 'https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'client_id=<CLIENT_ID>' \
  --data-urlencode 'client_secret=<CLIENT_SECRET>' \
  --data-urlencode 'grant_type=client_credentials'
```

```json
{"code":0,"result":{"access_token":"...","token_type":"Bearer",
 "expires_in":2591999,"scope":"API_Telemetry_V2 API_Info_V2 ...","auth_station":"all"}}
```

`expires_in` = **30 days**, so a token only needs fetching once a month. The
client caches it in memory and renews it 5 minutes before it expires.

Every request after that sends `Authorization: bearer {access_token}` — with
`bearer` in lower case, exactly as documented.

## 2. The full spec is available

`GET /openapi/v3/api-docs` (with an access_token) returns **the whole OpenAPI
3.0.3 spec, 123 endpoints** — more accurate than any PDF. Use it when adding
features:

```bash
curl -s https://openapi-eu.solaxcloud.com/openapi/v3/api-docs \
  -H "Authorization: bearer $TOKEN" | python3 -m json.tool
```

Endpoints the app uses:

| Endpoint | Used for |
|---|---|
| `GET /openapi/v2/device/page_device_info` | mapping Registration No. → inverter/battery serial |
| `GET /openapi/v2/device/realtime_data` | `deviceType=1` inverter, `=2` battery |
| `GET /openapi/v2/plant/realtime_data` | plant-level energy counters |
| `GET /openapi/v2/device/history_data` | loading history (backfill) |
| `GET /openapi/v2/alarm/page_alarm_info` | alarms raised by SolaX itself |
| `POST /openapi/v2/device/subs/http/create` | creating a Data Subscription → Callback Address |

There is also remote control (TOU, work mode, export/import control, VPP,
heat pump, EV charger) — see the spec.

## 3. One system, several devices

`deviceType` is an important parameter that the PDF documentation omits:

- `1` = inverter → PV, AC, `gridPower`, temperature
- `2` = battery → `batterySOC`, `chargeDischargePower`, SOH, cycle count

Both share one `registerNo` (the number printed on the dongle). The app calls
both and merges them, so the user declares nothing — only the Client ID/Secret.

## 4. Sign conventions (verified on 20 hours of real history)

- `gridPower` **> 0 exporting to the grid**, **< 0 importing from the grid**
  (at 21:00 with no PV: `gridPower = -151`, and `todayImportEnergy` keeps rising)
- `chargeDischargePower` **> 0 charging**
- `acPower1..3` is the inverter's AC output, **after battery charging is taken out**

The load calculation checked against real figures:
`PV 11610 = 4201+3935+3474` · `AC 7535 + charge 3995 ≈ PV` ·
`load = 7535 − (−88) = 7623 W`

## 5. Field names (entirely different from the legacy API)

| OpenAPI | Legacy API | Meaning |
|---|---|---|
| `MPPTTotalInputPower` / `mpptMap.MPPTnPower` | `powerdcN` | PV power |
| `acPower1..3` | `acpower` | AC output |
| `gridPower` | `feedinpower` | grid power |
| `chargeDischargePower` | `batPower` | battery power |
| `batterySOC` | `soc` | battery state of charge |
| `dailyYield` / `totalYield` | `yieldtoday` / `yieldtotal` | yield |
| `todayImportEnergy` / `totalImportEnergy` | `consumeenergy` | imported from the grid |
| `todayExportEnergy` / `totalExportEnergy` | `feedinenergy` | exported to the grid |
| `inverterTemperature` | `temperature` | temperature |
| `deviceStatus` | `inverterStatus` | **same code table** (102 = Normal) |

Only the **device status** table is shared. `deviceModel` uses a **different**
code table from the legacy `inverterType`, so the app shows "Model {n}" rather
than guess a wrong name.

## 6. Error codes

| Code | Meaning |
|---|---|
| `0` | token issued |
| `10000` | API call succeeded |
| `10400` | Bad client credentials — wrong Client ID/Secret, or wrong region |
| `10403` | not authorised — check the API Service Package |
| `10429` | hits/min or hits/day quota exceeded |

## 7. What was tried and did NOT work

Recorded so nobody has to dig it up again:

- **`api-*.solaxcloud.com` + Client ID/Secret** → `103 token invalid`. That
  gateway only reads the `tokenId` header and does not accept OAuth2. Using the
  client_id, the client_secret and `base64(id:secret)` as the tokenId were all
  rejected.
- **70 OAuth2 combinations on `api-*` and `solaxopenmanager`** (6 token paths ×
  2 auth styles × 5 hosts) → none issued a token. The Developer Platform token
  endpoint lives on the `openapi-*` host family, **not** `api-*` — which is
  where the initial probing went wrong.
- **`global.solaxcloud.com`** → HTTP 200 with an empty body; it serves no API
  even though it appears in the PDF paths.
- **`openapi-cn.solaxcloud.com`** → `10400 Bad client credentials` with an EU
  account. The region must match.
- **`history_data` with a 20-hour window** → returns 0 records. Split the range;
  the backfill script uses 6-hour chunks.

## 8. The old PDF documentation

Still correct for the **device status code table** (100–133). The endpoints,
hosts and field names are out of date — use the `/openapi/v3/api-docs` spec
instead.
