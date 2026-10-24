"""SolaX Developer Platform (OpenAPI) client.

Authentication is OAuth2 client credentials — no tokenId anywhere:

1. ``POST /openapi/auth/oauth/token`` with ``client_id`` / ``client_secret`` /
   ``grant_type=client_credentials`` (form-encoded) returns an ``access_token``
   valid for roughly 30 days.
2. Every subsequent call carries ``Authorization: bearer {access_token}``.

A site is modelled as several devices, so one dashboard reading is assembled
from up to three calls:

===============================  ===========  ==================================
endpoint                         deviceType   contributes
===============================  ===========  ==================================
``/v2/device/realtime_data``     1            PV strings, AC output, grid power
``/v2/device/realtime_data``     2            SOC, charge/discharge, battery temp
``/v2/plant/realtime_data``      -            daily/total energy counters
===============================  ===========  ==================================

The token call answers ``code: 0`` and data calls answer ``code: 10000``; both
mean success.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .demo import demo_reading
from .errors import SolaxError
from .models import Reading

log = logging.getLogger(__name__)

TOKEN_PATH = "/openapi/auth/oauth/token"
DEVICE_REALTIME = "/openapi/v2/device/realtime_data"
DEVICE_HISTORY = "/openapi/v2/device/history_data"
DEVICE_LIST = "/openapi/v2/device/page_device_info"
PLANT_REALTIME = "/openapi/v2/plant/realtime_data"
ALARM_LIST = "/openapi/v2/alarm/page_alarm_info"

DEVICE_TYPE_INVERTER = 1
DEVICE_TYPE_BATTERY = 2

#: The token endpoint answers 0, data endpoints answer 10000.
SUCCESS_CODES = {0, 10000}

#: Refresh this long before the token actually expires.
EXPIRY_MARGIN_S = 300

#: Codes meaning "this token is no longer good". The API returns these with
#: HTTP 200, so they have to be caught from the envelope rather than the status.
#: Note that issuing a new token invalidates the previous one, so a token is
#: only ever refreshed reactively — never speculatively.
TOKEN_ERROR_CODES = {10401, 10402}

ERROR_MESSAGES: dict[int, str] = {
    10400: "Bad client credentials — wrong Client ID/Secret, or wrong region "
           "(try another OPENAPI_BASE)",
    10401: "Access token expired or invalid",
    10402: "Access token invalid — it was probably superseded by a newer one",
    10403: "This application is not authorised for the endpoint — check its API "
           "service package on the developer portal",
    10404: "No data found",
    10429: "Rate limit exceeded (hits/min or hits/day of the service package)",
}


@dataclass
class Device:
    """One site: its inverter, its battery if present, and its plant."""

    register_no: str
    inverter_sn: str
    plant_id: str | None = None
    battery_sn: str | None = None
    rated_power: float | None = None
    model: Any = None
    online: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class SolaxClient:
    """Async client for the SolaX Developer Platform."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._token_lock = asyncio.Lock()
        self._devices: dict[str, Device] = {}
        self._discovered = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self.settings.openapi_base.rstrip("/")

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.settings.request_timeout_seconds,
                headers={"User-Agent": "solax-dashboard/1.0", "Accept": "*/*"},
            )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "SolaxClient":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("SOLAX_CLIENT_ID", self.settings.solax_client_id),
                ("SOLAX_CLIENT_SECRET", self.settings.solax_client_secret),
            )
            if not value
        ]
        if missing:
            raise SolaxError("Missing " + " and ".join(missing))

    @property
    def token_valid(self) -> bool:
        return bool(
            self._token and self._expires_at and datetime.now(timezone.utc) < self._expires_at
        )

    async def access_token(self, force: bool = False) -> str:
        """Cached client-credentials token; only one refresh runs at a time."""
        async with self._token_lock:
            if self.token_valid and not force:
                return self._token  # type: ignore[return-value]

            self._require_credentials()
            await self.start()
            assert self._http is not None

            try:
                resp = await self._http.post(
                    TOKEN_PATH,
                    data={
                        "client_id": self.settings.solax_client_id,
                        "client_secret": self.settings.solax_client_secret,
                        "grant_type": "client_credentials",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as exc:
                raise SolaxError(f"Cannot reach {self.base_url}: {exc}") from exc

            result = self._envelope(resp, "requesting an access token").get("result") or {}
            token = result.get("access_token")
            if not token:
                raise SolaxError(f"Token response has no access_token: {str(result)[:200]}")

            expires_in = int(result.get("expires_in") or 3600)
            self._token = token
            self._expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=max(EXPIRY_MARGIN_S + 60, expires_in) - EXPIRY_MARGIN_S
            )
            log.info(
                "Access token acquired (valid %.0f days, scope: %s)",
                expires_in / 86400,
                result.get("scope", "?"),
            )
            return token

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    def _envelope(self, resp: httpx.Response, what: str) -> dict[str, Any]:
        """Validate a response envelope, raising SolaxError on failure."""
        if resp.status_code >= 400:
            raise SolaxError(f"Error {what}: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SolaxError(f"Error {what}: response was not JSON") from exc

        code = payload.get("code")
        if code in SUCCESS_CODES:
            return payload

        detail = ERROR_MESSAGES.get(
            code, payload.get("message") or payload.get("exception") or "unknown error"
        )
        raise SolaxError(f"Error {what}: code {code} — {detail}", code=code)

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        """Authenticated GET, retrying once if the token was rejected.

        Rejection shows up two ways: an HTTP 401/403, or HTTP 200 carrying one
        of TOKEN_ERROR_CODES in the envelope. Both are handled here, and only
        here — refreshing a token anywhere else would invalidate the one the
        poller is using.
        """
        await self.start()
        assert self._http is not None

        for attempt in (1, 2):
            token = await self.access_token(force=attempt == 2)
            try:
                resp = await self._http.get(
                    path, params=params, headers={"Authorization": f"bearer {token}"}
                )
            except httpx.HTTPError as exc:
                raise SolaxError(f"Cannot reach the SolaX API: {exc}") from exc

            if resp.status_code in (401, 403) and attempt == 1:
                self._token = None
                continue

            try:
                return self._envelope(resp, f"calling {path}").get("result")
            except SolaxError as exc:
                if exc.code in TOKEN_ERROR_CODES and attempt == 1:
                    log.info("Access token was rejected; requesting a new one")
                    self._token = None
                    continue
                raise

        raise SolaxError("The SolaX API rejected the access token after a refresh")

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------
    async def discover(self, force: bool = False) -> dict[str, Device]:
        """Map each dongle registration number to its device serials.

        Saves the user from looking anything up: the account is enumerated and
        inverters are paired with their batteries automatically.
        """
        if self._discovered and not force:
            return self._devices

        if self.settings.demo_mode:
            self._devices = {
                "DEMO000001": Device(
                    register_no="DEMO000001",
                    inverter_sn="DEMO0000000001",
                    battery_sn="DEMO0000000002",
                    plant_id="demo-plant",
                    rated_power=12.0,
                    model=103,
                )
            }
            self._discovered = True
            return self._devices

        inverters = await self._list_devices(DEVICE_TYPE_INVERTER)
        batteries = await self._list_devices(DEVICE_TYPE_BATTERY)
        battery_by_register = {b.get("registerNo"): b.get("deviceSn") for b in batteries}

        devices: dict[str, Device] = {}
        for inv in inverters:
            register_no = inv.get("registerNo")
            serial = inv.get("deviceSn")
            if not (register_no and serial):
                continue
            devices[register_no] = Device(
                register_no=register_no,
                inverter_sn=serial,
                plant_id=str(inv["plantId"]) if inv.get("plantId") else None,
                battery_sn=battery_by_register.get(register_no),
                rated_power=inv.get("ratedPower"),
                model=inv.get("deviceModel"),
                online=inv.get("onlineStatus") == 1,
                extra=inv,
            )

        self._devices = devices
        self._discovered = True
        log.info(
            "Discovered %d device(s): %s",
            len(devices),
            ", ".join(
                f"{d.register_no} ({'with battery' if d.battery_sn else 'no battery'})"
                for d in devices.values()
            )
            or "none",
        )
        return devices

    async def _list_devices(self, device_type: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while page <= 20:  # generous bound; a real account never hits it
            result = await self._get(
                DEVICE_LIST, {"deviceType": device_type, "businessType": 1, "pageNo": page}
            )
            if not isinstance(result, dict):
                break
            batch = result.get("records") or []
            records.extend(batch)
            if page >= int(result.get("pages") or 1) or not batch:
                break
            page += 1
        return records

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------
    async def realtime(self, register_no: str) -> Reading:
        """Assemble one reading for a dongle registration number."""
        if self.settings.demo_mode:
            return demo_reading(register_no)

        self._require_credentials()
        devices = await self.discover()
        device = devices.get(register_no)
        if device is None:
            known = ", ".join(devices) or "none"
            raise SolaxError(
                f"Device '{register_no}' is not in this account. Available: {known}"
            )

        inverter = await self._device_realtime(device.inverter_sn, DEVICE_TYPE_INVERTER)
        if inverter is None:
            raise SolaxError(f"No realtime data for inverter {device.inverter_sn}")

        battery = None
        if device.battery_sn:
            try:
                battery = await self._device_realtime(device.battery_sn, DEVICE_TYPE_BATTERY)
            except SolaxError as exc:
                log.warning("Could not read battery %s: %s", device.battery_sn, exc)

        plant = None
        if device.plant_id and self.settings.fetch_plant_totals:
            try:
                result = await self._get(
                    PLANT_REALTIME, {"plantId": device.plant_id, "businessType": 1}
                )
                plant = result if isinstance(result, dict) else None
            except SolaxError as exc:
                log.warning("Could not read plant %s: %s", device.plant_id, exc)

        return Reading.parse(
            inverter,
            battery=battery,
            plant=plant,
            register_no=register_no,
            rated_power=device.rated_power,
            device_model=device.model,
        )

    async def _device_realtime(self, serial: str, device_type: int) -> dict[str, Any] | None:
        result = await self._get(
            DEVICE_REALTIME,
            {"snList": serial, "deviceType": device_type, "businessType": 1},
        )
        if isinstance(result, list):
            return result[0] if result else None
        return result if isinstance(result, dict) else None

    async def realtime_many(self, register_nos: list[str]) -> dict[str, Reading | SolaxError]:
        """Fetch several sites concurrently; failures are returned, not raised."""
        results = await asyncio.gather(
            *(self.realtime(rn) for rn in register_nos), return_exceptions=True
        )
        out: dict[str, Reading | SolaxError] = {}
        for register_no, result in zip(register_nos, results):
            if isinstance(result, Reading):
                out[register_no] = result
            elif isinstance(result, SolaxError):
                out[register_no] = result
            else:
                out[register_no] = SolaxError(str(result))
        return out

    # ------------------------------------------------------------------
    # History + alarms
    # ------------------------------------------------------------------
    async def history(
        self,
        serial: str,
        device_type: int,
        start: datetime,
        end: datetime,
        interval_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        """Raw history rows. The API rejects wide windows, so chunk the range."""
        result = await self._get(
            DEVICE_HISTORY,
            {
                "snList": serial,
                "deviceType": device_type,
                "businessType": 1,
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "timeInterval": interval_minutes,
            },
        )
        return result if isinstance(result, list) else []

    async def alarms(self, register_no: str | None = None, page: int = 1) -> list[dict[str, Any]]:
        """Alarm records raised by SolaX itself, alongside our threshold rules."""
        params: dict[str, Any] = {"pageNo": page, "businessType": 1}
        if register_no:
            device = (await self.discover()).get(register_no)
            if device:
                params["deviceSn"] = device.inverter_sn
        result = await self._get(ALARM_LIST, params)
        if isinstance(result, dict):
            return result.get("records") or []
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    async def diagnose(self) -> dict[str, Any]:
        """Credential / token / device health for the settings page.

        Reuses the cached token rather than forcing a new one: issuing a token
        invalidates the previous one, so a "diagnostic" refresh would break the
        poller mid-flight.
        """
        out: dict[str, Any] = {
            "base_url": self.base_url,
            "has_client_id": bool(self.settings.solax_client_id),
            "has_client_secret": bool(self.settings.solax_client_secret),
            "token_ok": False,
            "token_expires_at": None,
            "devices": [],
            "error": None,
        }
        try:
            await self.access_token()
            out["token_ok"] = True
            out["token_expires_at"] = self._expires_at.isoformat() if self._expires_at else None
            devices = await self.discover()
            out["devices"] = [
                {
                    "register_no": d.register_no,
                    "inverter_sn": d.inverter_sn,
                    "battery_sn": d.battery_sn,
                    "plant_id": d.plant_id,
                    "rated_power": d.rated_power,
                    "online": d.online,
                }
                for d in devices.values()
            ]
        except SolaxError as exc:
            out["error"] = str(exc)
        except Exception as exc:  # never 500 the settings page
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out
