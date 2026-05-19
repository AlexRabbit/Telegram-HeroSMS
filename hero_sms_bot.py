#!/usr/bin/env python3
"""
HeroSMS Telegram Bot — single file.
Receives SMS codes and email verification via hero-sms.com API.

Fresh VPS / new PC:
  python hero_sms_bot.py --install    # writes requirements.txt, installs pip deps
  python hero_sms_bot.py              # run bot (auto-bootstrap once)

Requires: Python 3.10+ (stdlib only; no pip packages needed)
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "hero_data"
FAVS_FILE = DATA_DIR / "favs.json"
STATE_FILE = DATA_DIR / "state.json"
REQ_FILE = SCRIPT_DIR / "requirements.txt"
BOOTSTRAP_MARKER = SCRIPT_DIR / ".hero_bootstrapped"

# Written next to this script on --install (empty = stdlib only, but pip still set up)
REQUIREMENTS_TXT = """# HeroSMS Telegram Bot — auto-generated
# Runtime uses Python stdlib only. Line below is optional extras (leave empty).
"""

FAV_NAME_RE = re.compile(r"^[a-z0-9_]{2,40}$")


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (stdlib only, does not override)."""
    env_path = path or (SCRIPT_DIR / ".env")
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def bootstrap_dependencies(*, force: bool = False) -> None:
    """Prepare a brand-new server: ensure pip exists and install requirements.txt."""
    if not force and BOOTSTRAP_MARKER.exists() and REQ_FILE.exists():
        return
    print("[bootstrap] Preparing Python environment...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REQ_FILE.write_text(REQUIREMENTS_TXT, encoding="utf-8")

    def run(cmd: list[str], required: bool = False) -> bool:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0 and required:
                print(f"[bootstrap] WARN: {' '.join(cmd)}\n{r.stderr or r.stdout}")
            return r.returncode == 0
        except Exception as e:
            if required:
                print(f"[bootstrap] ERROR: {e}")
            return False

    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10+ required. Install: apt install python3 python3-pip")

    run([sys.executable, "-m", "ensurepip", "--upgrade"], required=False)
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"], required=False)

    pkgs = [
        ln.split("#")[0].strip()
        for ln in REQUIREMENTS_TXT.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if pkgs:
        run([sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)], required=False)
    else:
        print("[bootstrap] No pip packages required (stdlib only).")

    BOOTSTRAP_MARKER.write_text(str(time.time()), encoding="utf-8")
    print(f"[bootstrap] Done. Data dir: {DATA_DIR}")


if __name__ == "__main__" and "--install" in sys.argv:
    bootstrap_dependencies(force=True)
    print("Install complete. Copy .env.example to .env, fill secrets, then: python hero_sms_bot.py")
    raise SystemExit(0)

if "--test" not in sys.argv:
    bootstrap_dependencies()

# Load local .env before reading CONFIG (does not override existing env vars)
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — use environment variables or a local .env file (see .env.example)
# ═══════════════════════════════════════════════════════════════════════════════

# Set via environment variables or a local .env file (never commit real values).
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
HEROSMS_API_KEY = os.environ.get("HEROSMS_API_KEY", "")
# Only this Telegram user id can use the bot. Get yours from @userinfobot
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0") or "0")

HEROSMS_BASE = "https://hero-sms.com/stubs/handler_api.php"
HEROSMS_V1_BASE = "https://hero-sms.com/api/v1"
POLL_INTERVAL_SEC = 12
BALANCE_ALERT_LOW = float(os.environ.get("BALANCE_ALERT_LOW", "2.0"))

# Guided UI pagination
SERVICES_COLS = 3
SERVICES_PER_PAGE = 30
COUNTRY_BUY_COLS = 2
COUNTRY_BUY_PER_PAGE = 16
COUNTRY_LIST_COLS = 2
COUNTRY_LIST_PER_PAGE = 24
EMAIL_SITES_COLS = 2
EMAIL_SITES_PER_PAGE = 16
EMAIL_DOMAIN_COLS = 2
EMAIL_DOMAIN_PER_PAGE = 16

POPULAR_EMAIL_SITES = [
    "instagram.com",
    "google.com",
    "facebook.com",
    "twitter.com",
    "discord.com",
    "telegram.org",
    "tiktok.com",
    "snapchat.com",
    "amazon.com",
    "microsoft.com",
    "apple.com",
    "linkedin.com",
    "yahoo.com",
    "gmail.com",
    "outlook.com",
]

# SMS-Activate / HeroSMS country id → ISO 3166-1 alpha-2 (for flag emoji)
COUNTRY_ISO: dict[int, str] = {
    0: "RU", 1: "UA", 2: "KZ", 3: "CN", 4: "PH", 5: "MM", 6: "ID", 7: "MY", 8: "KE", 9: "TZ",
    10: "VN", 11: "KG", 12: "US", 13: "IL", 14: "HK", 15: "PL", 16: "GB", 17: "MG", 18: "CD", 19: "NG",
    20: "MO", 21: "EG", 22: "IN", 23: "IE", 24: "KH", 25: "LA", 26: "HT", 27: "CI", 28: "GM", 29: "RS",
    30: "YE", 31: "ZA", 32: "RO", 33: "CO", 34: "EE", 35: "CA", 36: "MA", 37: "GH", 38: "AR", 39: "UZ",
    40: "CM", 41: "TD", 42: "DE", 43: "LT", 44: "HR", 45: "SE", 46: "IQ", 47: "NL", 48: "LV", 49: "AT",
    50: "BY", 51: "TH", 52: "MX", 53: "SA", 54: "TW", 55: "ES", 56: "IR", 57: "DZ", 58: "SI", 59: "BD",
    60: "SN", 61: "TR", 62: "TR", 63: "PK", 64: "GN", 65: "VE", 66: "ET", 67: "MN", 68: "BR", 69: "AF",
    70: "UG", 71: "AO", 72: "CY", 73: "BR", 74: "MZ", 75: "ZM", 76: "SY", 77: "NA", 78: "NP", 79: "BE",
    80: "BG", 81: "HU", 82: "MD", 83: "IT", 84: "PY", 85: "HN", 86: "TN", 87: "NI", 88: "TL", 89: "BO",
    90: "CR", 91: "GT", 92: "AE", 93: "ZW", 94: "PR", 95: "SD", 96: "TG", 97: "KW", 98: "SV", 99: "LY",
    100: "JM", 101: "TT", 102: "EC", 103: "SZ", 104: "OM", 105: "BA", 106: "DO", 107: "RS", 108: "GE",
    109: "PA", 110: "MR", 111: "MU", 112: "SI", 113: "AM", 114: "FJ", 115: "BW", 116: "GA", 117: "AL",
    118: "LV", 119: "CM", 120: "BH", 121: "BN", 122: "GT", 123: "HN", 124: "IE", 125: "ME", 126: "DZ",
    127: "SK", 128: "SN", 129: "TZ", 130: "ML", 131: "BB", 132: "BI", 133: "BJ", 134: "BN", 135: "BT",
    136: "JO", 137: "CM", 138: "QA", 139: "GW", 140: "GR", 141: "GY", 142: "IS", 143: "CV", 144: "KH",
    145: "LA", 146: "LS", 147: "MW", 148: "NA", 149: "NE", 150: "RW", 151: "CL", 152: "LR", 153: "CG",
    154: "SL", 155: "SO", 156: "SR", 157: "SS", 158: "ST", 159: "SC", 160: "TD", 161: "TO", 162: "TV",
    163: "VU", 164: "WS", 165: "YE", 166: "DJ", 167: "GQ", 168: "ER", 169: "GM", 170: "GN", 171: "GW",
    172: "KI", 173: "KM", 174: "KN", 175: "AU", 176: "NZ", 187: "US",
}

# Default quick-buy favorites (Turkey & Chile Telegram) — edit via /favadd or hero_data/favs.json
DEFAULT_FAVORITES = [
    {"name": "tr_tg", "label": "Turkey · Telegram", "service": "tg", "country": 62},
    {"name": "cl_tg", "label": "Chile · Telegram", "service": "tg", "country": 151},
]

# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hero-sms-bot")

STATUS_LABELS = {
    "1": "SMS sent (ready)",
    "3": "Waiting for email/SMS",
    "4": "Code received",
    "5": "Success",
    "6": "Completed",
    "8": "Cancelled",
    "WAIT": "Waiting for message",
    "SUCCESS": "Message received",
    "CANCEL": "Cancelled",
    "ERROR": "Error",
}

SET_STATUS = {
    "ready": 1,
    "resend": 3,
    "complete": 6,
    "cancel": 8,
}

BUY_ERROR_MESSAGES = {
    "NO_NUMBERS": "No numbers available for this country right now.",
    "NO_BALANCE": "Insufficient balance — top up at hero-sms.com",
    "WRONG_MAX_PRICE": "Price changed — pick another country or raise max price.",
    "BAD_SERVICE": "Unknown service code.",
    "BAD_KEY": "Invalid API key.",
    "CHANNELS_LIMIT": "Too many active activations.",
    "EARLY_CANCEL_DENIED": "Cannot cancel yet — wait a bit and try again.",
}


class HeroSMSBuyError(Exception):
    """Expected purchase failure from HeroSMS API (not a bot crash)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail)


def api_error_code(exc: Exception) -> str:
    text = str(exc).strip()
    for code in BUY_ERROR_MESSAGES:
        if text == code or text.startswith(f"{code}:") or f'"{code}"' in text:
            return code
    if "NO_NUMBERS" in text:
        return "NO_NUMBERS"
    if "NO_BALANCE" in text:
        return "NO_BALANCE"
    return "UNKNOWN"


# ─── Persistent storage (favorites + alert state) ─────────────────────────────


class AppState:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self.data: dict[str, Any] = {"low_balance_alerted": False, "last_balance": None}
        self.load()

    def load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


class FavoritesStore:
    def __init__(self, path: Path = FAVS_FILE) -> None:
        self.path = path
        self.favorites: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.favorites = raw.get("favorites") or []
                return
            except Exception as e:
                log.warning("favs load: %s", e)
        self.favorites = [dict(f) for f in DEFAULT_FAVORITES]
        self.save()

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"favorites": self.favorites}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, name: str) -> dict[str, Any] | None:
        name = name.lower()
        for f in self.favorites:
            if f.get("name") == name:
                return f
        return None

    def add(self, fav: dict[str, Any]) -> None:
        name = fav["name"]
        self.favorites = [f for f in self.favorites if f.get("name") != name]
        self.favorites.append(fav)
        self.favorites.sort(key=lambda x: x.get("label", x.get("name", "")))
        self.save()

    def delete(self, name: str) -> bool:
        name = name.lower()
        before = len(self.favorites)
        self.favorites = [f for f in self.favorites if f.get("name") != name]
        if len(self.favorites) < before:
            self.save()
            return True
        return False


# ─── HeroSMS API ───────────────────────────────────────────────────────────────


class HeroSMS:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.mail_api_enabled = True  # set by mail_probe() on startup

    def v1_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """HeroSMS REST API v1 (emails) — https://hero-sms.com/api/v1"""
        url = HEROSMS_V1_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {
            "User-Agent": "HeroSMS-Telegram-Bot/1.0",
            "Accept": "application/json",
            "Authorization": f"ApiKey {self.api_key}",
        }
        data: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"HTTP {e.code}: {raw[:300]}") from e
            title = err.get("title") or err.get("message") or f"HTTP {e.code}"
            details = err.get("details") or ""
            errors = err.get("errors")
            if isinstance(errors, dict):
                detail_parts = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in errors.items()]
                details = (details + " " + "; ".join(detail_parts)).strip()
            raise RuntimeError(f"{title}. {details}".strip(". ")) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e}") from e
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()

    def mail_probe(self) -> bool:
        """Check whether email REST API is available for this API key."""
        try:
            self.v1_request("GET", "/emails", params={"page": 1, "size": 1})
            self.mail_api_enabled = True
            log.info("HeroSMS email API (v1) OK")
            return True
        except Exception as e:
            self.mail_api_enabled = False
            log.warning("HeroSMS email API unavailable: %s", e)
            return False

    def call(self, action: str, **params: Any) -> Any:
        q = {"api_key": self.api_key, "action": action, **{k: v for k, v in params.items() if v is not None}}
        url = f"{HEROSMS_BASE}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers={"User-Agent": "HeroSMS-Telegram-Bot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {raw[:300]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e}") from e

        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()

    def get_balance(self) -> float:
        r = self.call("getBalance")
        if isinstance(r, str) and r.startswith("ACCESS_BALANCE:"):
            return float(r.split(":", 1)[1])
        raise RuntimeError(f"Unexpected balance: {r}")

    def get_number(self, service: str, country: int, **kw: Any) -> tuple[int, str]:
        r = self.call("getNumber", service=service, country=country, **kw)
        if isinstance(r, str) and r.startswith("ACCESS_NUMBER:"):
            parts = r.split(":")
            return int(parts[1]), parts[2]
        raise RuntimeError(str(r))

    def get_number_v2(self, service: str, country: int, **kw: Any) -> dict:
        r = self.call("getNumberV2", service=service, country=country, **kw)
        if isinstance(r, dict):
            return r
        raise RuntimeError(str(r))

    def set_status(self, activation_id: int, status: int) -> str:
        r = self.call("setStatus", id=activation_id, status=status)
        return str(r)

    def get_status(self, activation_id: int) -> str:
        return str(self.call("getStatus", id=activation_id))

    def get_status_v2(self, activation_id: int) -> Any:
        return self.call("getStatusV2", id=activation_id)

    def get_active(self, start: int = 0, limit: int = 100) -> list[dict]:
        r = self.call("getActiveActivations", start=start, limit=limit)
        if isinstance(r, str):
            if r in ("NO_ACTIVATIONS", "BAD_ACTION"):
                return []
            return []
        if isinstance(r, dict):
            if r.get("status") == "success":
                data = r.get("data")
                if isinstance(data, list):
                    return data
                acts = r.get("activeActivations")
                if isinstance(acts, list):
                    return acts
                if isinstance(acts, dict):
                    inner = acts.get("rows")
                    if inner is None:
                        inner = acts.get("row")
                    if isinstance(inner, list):
                        return inner
                return []
            if "activeActivations" in r:
                acts = r["activeActivations"]
                if isinstance(acts, list):
                    return acts
            rows = r.get("rows")
            if rows is None:
                rows = r.get("row")
            if isinstance(rows, list):
                return rows
        if isinstance(r, list):
            return r
        return []

    def get_all_sms(self, activation_id: int, page: int = 1, size: int = 10) -> Any:
        return self.call("getAllSms", id=activation_id, page=page, size=size)

    def finish(self, activation_id: int) -> None:
        self.call("finishActivation", id=activation_id)

    def cancel_activation(self, activation_id: int) -> None:
        self.call("cancelActivation", id=activation_id)

    def get_countries(self) -> list[dict]:
        r = self.call("getCountries")
        if isinstance(r, list):
            return r
        if isinstance(r, dict):
            return list(r.values()) if all(str(k).isdigit() for k in r) else []
        return []

    def get_services(self, country: int | None = None, lang: str = "en") -> list[dict]:
        kw: dict[str, Any] = {"lang": lang}
        if country is not None:
            kw["country"] = country
        r = self.call("getServicesList", **kw)
        if isinstance(r, dict) and r.get("status") == "success":
            return r.get("services") or []
        if isinstance(r, dict) and "services" in r:
            return r["services"]
        return []

    def get_prices(self, service: str, country: int) -> Any:
        return self.call("getPrices", service=service, country=country)

    def get_operators(self, country: int | None = None) -> Any:
        kw: dict[str, Any] = {}
        if country is not None:
            kw["country"] = country
        return self.call("getOperators", **kw)

    def get_top_countries(self, service: str, free_price: bool = False) -> Any:
        return self.call(
            "getTopCountriesByService",
            service=service,
            freePrice="true" if free_price else "false",
        )

    def get_list_of_top_countries_by_service(self, service: str, **extra: Any) -> list[dict[str, Any]]:
        """SMS-Activate style: [{country, share, rate}, ...] — not always enabled on HeroSMS."""
        raw = self.call("getListOfTopCountriesByService", service=service, **extra)
        return _parse_country_success_list(raw)

    def get_country_success_statistics(self, service: str) -> list[dict[str, Any]]:
        """
        Try several action names / params for website-style success stats (12h & 24h).
        Returns normalized rows: country, rate, share, success_12h, success_24h (when present).
        """
        attempts: list[tuple[str, dict[str, Any]]] = [
            ("getListOfTopCountriesByService", {"service": service}),
            ("getListOfTopCountriesByService", {"service": service, "period": 12}),
            ("getListOfTopCountriesByService", {"service": service, "hours": 12}),
            ("getCountrySuccessStatistics", {"service": service}),
            ("getServiceCountryStatistics", {"service": service}),
            ("getTopCountriesSuccessStatistics", {"service": service}),
            ("getSuccessfulCountries", {"service": service}),
            ("getTopCountriesByService", {"service": service}),
            ("getTopCountriesByServiceRank", {"service": service}),
        ]
        fallback: list[dict[str, Any]] = []
        for action, params in attempts:
            try:
                raw = self.call(action, **params)
            except RuntimeError:
                continue
            rows = _parse_country_success_list(raw)
            if not rows:
                continue
            if _rows_have_success_windows(rows):
                return rows
            if action in (
                "getListOfTopCountriesByService",
                "getTopCountriesByService",
                "getTopCountriesByServiceRank",
            ):
                fallback = rows
        return fallback

    def get_top_countries_for_service(self, service: str) -> list[dict[str, Any]]:
        """Countries with stock for a service, sorted by price (cheapest first)."""
        raw = self.get_top_countries(service)
        items: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            for val in raw.values():
                if isinstance(val, dict) and "country" in val:
                    items.append(val)
        elif isinstance(raw, list):
            for block in raw:
                if isinstance(block, dict):
                    for val in block.values():
                        if isinstance(val, list):
                            items.extend(v for v in val if isinstance(v, dict))
                        elif isinstance(val, dict) and "country" in val:
                            items.append(val)
        out: list[dict[str, Any]] = []
        for it in items:
            try:
                cnt = int(it.get("count") or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt <= 0:
                continue
            try:
                price = float(it.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            out.append(
                {
                    "country": int(it["country"]),
                    "price": price,
                    "count": cnt,
                    "retail_price": it.get("retail_price"),
                }
            )
        out.sort(key=lambda x: (x["price"], -x["count"]))
        return out

    def get_history(self, start: int, end: int, offset: int = 0, size: int = 20) -> Any:
        return self.call("getHistory", start=start, end=end, offset=offset, size=size)

    def get_numbers_status(self, country: int = 0, operator: str | None = None) -> Any:
        kw: dict[str, Any] = {"country": country}
        if operator:
            kw["operator"] = operator
        return self.call("getNumbersStatus", **kw)

    # Email API — HeroSMS REST v1 (https://hero-sms.com/api — tag: emails)
    def _mail_empty(self) -> dict:
        return {"list": [], "count": 0, "pages": 0}

    @staticmethod
    def _v1_data(raw: Any) -> Any:
        if isinstance(raw, dict) and "data" in raw:
            return raw["data"]
        return raw

    def mail_domains(self, site: str) -> dict:
        if not self.mail_api_enabled:
            raise RuntimeError("Email API is not available. Check your HeroSMS API key.")
        raw = self.v1_request("GET", "/emails/domains", params={"site": site})
        items = self._v1_data(raw)
        if not isinstance(items, list):
            raise RuntimeError(str(raw))
        domains: list[dict[str, Any]] = []
        for d in items:
            if not isinstance(d, dict):
                continue
            cnt = int(d.get("count") or 0)
            if cnt <= 0:
                continue
            domains.append(
                {
                    "name": d.get("name"),
                    "cost": d.get("cost"),
                    "count": cnt,
                }
            )
        domains.sort(key=lambda x: float(x.get("cost") or 0))
        return {"domains": domains}

    def mail_buy(self, site: str, mail_domain: str, *, mail_type: int | None = None) -> dict:
        """Buy email. mail_type is ignored (legacy); v1 uses site + domain only."""
        if not self.mail_api_enabled:
            raise RuntimeError("Email API is not available. Check your HeroSMS API key.")
        raw = self.v1_request(
            "POST",
            "/emails",
            json_body={"site": site, "domain": mail_domain},
        )
        data = self._v1_data(raw)
        if not isinstance(data, dict):
            raise RuntimeError(str(raw))
        return data

    def mail_history(self, page: int = 1, per_page: int = 20) -> dict:
        if not self.mail_api_enabled:
            return self._mail_empty()
        size = max(1, min(int(per_page), 25))
        raw = self.v1_request(
            "GET",
            "/emails",
            params={"page": page, "size": size},
        )
        items = self._v1_data(raw)
        lst = items if isinstance(items, list) else []
        meta = raw.get("meta") if isinstance(raw, dict) else {}
        total = int((meta or {}).get("total") or len(lst))
        pages = int((meta or {}).get("pages") or 0)
        return {"list": lst, "count": total, "pages": pages}

    def mail_check(self, mail_id: int) -> dict:
        if not self.mail_api_enabled:
            raise RuntimeError("Email API is not available. Check your HeroSMS API key.")
        raw = self.v1_request("GET", f"/emails/{mail_id}")
        data = self._v1_data(raw)
        if not isinstance(data, dict):
            raise RuntimeError(str(raw))
        val = data.get("value") or data.get("message")
        if val is not None:
            data = {**data, "value": val}
        return data

    def mail_cancel(self, mail_id: int) -> bool:
        if not self.mail_api_enabled:
            raise RuntimeError("Email API is not available. Check your HeroSMS API key.")
        self.v1_request("DELETE", f"/emails/{mail_id}")
        return True

    def mail_reorder(self, mail_id: int) -> dict:
        if not self.mail_api_enabled:
            raise RuntimeError("Email API is not available. Check your HeroSMS API key.")
        raw = self.v1_request("POST", f"/emails/{mail_id}/reorder")
        data = self._v1_data(raw)
        if isinstance(data, dict):
            return data
        raise RuntimeError(str(raw))

    def mail_get_active(self) -> list[dict]:
        """Emails still waiting for a verification message (status WAIT)."""
        if not self.mail_api_enabled:
            return []
        data = self.mail_history(per_page=25)
        done = {"SUCCESS", "CANCEL", "CANCELLED", "5", "6", "8"}
        out: list[dict] = []
        for m in data.get("list") or []:
            if not isinstance(m, dict):
                continue
            st = str(m.get("status", "")).upper()
            if st == "WAIT":
                out.append(m)
                continue
            if st in done or m.get("value"):
                continue
            # Unknown in-progress status without message yet
            out.append(m)
        return out


# ─── Telegram (stdlib) ─────────────────────────────────────────────────────────


class Telegram:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, data: dict | None = None) -> dict:
        body = urllib.parse.urlencode({k: v for k, v in (data or {}).items() if v is not None}).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                out = json.loads(e.read().decode())
                desc = out.get("description", str(e))
            except Exception:
                desc = str(e)
            raise RuntimeError(desc) from e
        if not out.get("ok"):
            raise RuntimeError(out.get("description", "Telegram API error"))
        return out

    def send(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        disable_preview: bool = True,
    ) -> int | None:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        r = self._post("sendMessage", data)
        return r.get("result", {}).get("message_id")

    def edit(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            self._post("editMessageText", data)
        except RuntimeError as e:
            if "message is not modified" not in str(e).lower():
                raise

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> bool:
        """Answer inline button press. Returns False if query expired (harmless)."""
        try:
            self._post(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert},
            )
            return True
        except RuntimeError as e:
            msg = str(e).lower()
            if "query is too old" in msg or "query id is invalid" in msg or "response timeout expired" in msg:
                log.debug("Stale callback ignored: %s", e)
                return False
            raise

    def get_me(self) -> dict:
        return self._post("getMe")["result"]

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict]:
        url = (
            f"{self.base}/getUpdates?timeout={timeout}"
            f"&offset={offset}&allowed_updates={urllib.parse.quote(json.dumps(['message', 'callback_query']))}"
        )
        with urllib.request.urlopen(url, timeout=timeout + 10) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            raise RuntimeError(data.get("description"))
        return data.get("result", [])


# ─── Formatting ────────────────────────────────────────────────────────────────


def esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def fmt_money(v: Any) -> str:
    try:
        return f"${float(v):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return esc(v)


def mono_block(text: str, max_len: int = 500) -> str:
    """Monospace block — tap to select/copy in Telegram."""
    return f"<pre>{esc(str(text)[:max_len])}</pre>"


def mono_inline(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def code_display(code: str, title: str = "Code") -> str:
    """Large monospace OTP + hint to use Copy button below."""
    c = str(code).strip()
    return f"📋 <b>{esc(title)}</b> (mono, tap to copy):\n{mono_block(c, 64)}"


def extract_sms_code(a: dict) -> str:
    code = a.get("smsCode")
    if isinstance(code, list):
        return str(code[0]) if code else ""
    return str(code or "").strip()


def copy_code_keyboard(code: str, extra: list[list[dict]] | None = None) -> dict:
    rows: list[list[dict]] = [[{"text": "📋 Copy code", "copy_text": {"text": str(code)[:256]}}]]
    if extra:
        rows.extend(extra)
    return {"inline_keyboard": rows}


def parse_status_text(raw: str) -> str:
    if raw.startswith("STATUS_OK:"):
        return f"✅ {code_display(raw.split(':', 1)[1], 'SMS code')}"
    if raw.startswith("STATUS_WAIT_RETRY:"):
        return f"🔁 {code_display(raw.split(':', 1)[1], 'Retry code')}"
    labels = {
        "STATUS_WAIT_CODE": "⏳ Waiting for SMS…",
        "STATUS_WAIT_RESEND": "⏳ Waiting for resend…",
        "STATUS_CANCEL": "❌ Cancelled",
    }
    return labels.get(raw, mono_inline(raw))


def activation_card(a: dict, *, show_code_block: bool = True) -> str:
    sid = a.get("activationId") or a.get("id")
    code = extract_sms_code(a)
    st = str(a.get("activationStatus") or a.get("status", ""))
    st_label = STATUS_LABELS.get(st, st)
    lines = [
        f"📱 <b>SMS #{esc(sid)}</b>",
        f"Service: {mono_inline(a.get('serviceCode', '?'))}",
        f"Number: {mono_inline(a.get('phoneNumber', a.get('phone', '?')))}",
        f"Country: {esc(a.get('countryName', a.get('countryCode', '?')))}",
        f"Cost: {fmt_money(a.get('activationCost', a.get('cost', 0)))}",
        f"Status: {esc(st_label)}",
    ]
    if code and show_code_block:
        lines.append(code_display(code, "SMS code"))
    txt = a.get("smsText")
    if txt:
        lines.append(f"💬 {mono_block(str(txt), 200)}")
    return "\n".join(lines)


def mail_card(m: dict) -> str:
    mid = m.get("id")
    st = str(m.get("status", ""))
    st_label = STATUS_LABELS.get(st, st)
    lines = [
        f"📧 <b>Email #{esc(mid)}</b>",
        f"Site: {mono_inline(m.get('site'))}",
        f"Address: {mono_inline(m.get('email'))}",
        f"Cost: {fmt_money(m.get('cost', 0))}",
        f"Status: {esc(st_label)}",
        f"Date: {esc(m.get('date', ''))}",
    ]
    val = m.get("value") or m.get("full_message")
    if val:
        lines.append(f"📩 {mono_block(str(val), 1500)}")
    return "\n".join(lines)


def fav_line(f: dict) -> str:
    mp = f.get("max_price")
    extra = f" · max {fmt_money(mp)}" if mp is not None else ""
    return (
        f"⭐ <b>{esc(f.get('label', f.get('name')))}</b> "
        f"<code>{esc(f.get('name'))}</code>\n"
        f"   {mono_inline(f.get('service'))} · country {mono_inline(f.get('country'))}{extra}"
    )


def active_emails_keyboard(emails: list[dict]) -> dict:
    """Inline actions for waiting email orders."""
    rows: list[list[dict]] = []
    for m in emails[:10]:
        mid = m.get("id")
        if mid is None:
            continue
        rows.append(
            [
                {"text": f"📩 #{mid}", "callback_data": f"em:mail:{mid}"},
                {"text": f"❌ Cancel", "callback_data": f"em:cancel:{mid}"},
            ]
        )
    rows.append(
        [
            {"text": "🔄 Refresh", "callback_data": "m:activeem"},
            {"text": "📧 Buy email", "callback_data": "m:buyem"},
        ]
    )
    rows.append([{"text": "🏠 Menu", "callback_data": "m:menu"}])
    return {"inline_keyboard": rows}


def favs_keyboard(favorites: list[dict]) -> dict:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for f in favorites:
        name = f.get("name", "")[:32]
        label = (f.get("label") or name)[:28]
        row.append({"text": f"🛒 {label}", "callback_data": f"fav:buy:{name}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🏠 Menu", "callback_data": "m:menu"}])
    return {"inline_keyboard": rows}


def main_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📱 Active SMS", "callback_data": "m:active"},
                {"text": "📬 Active email", "callback_data": "m:activeem"},
            ],
            [
                {"text": "⭐ Favorites", "callback_data": "m:favs"},
                {"text": "🛒 Buy SMS", "callback_data": "m:buysms"},
            ],
            [
                {"text": "📧 Buy email", "callback_data": "m:buyem"},
                {"text": "💰 Balance", "callback_data": "m:bal"},
            ],
            [{"text": "📖 Help", "callback_data": "m:help"}],
        ]
    }


def flag_emoji(iso2: str) -> str:
    iso2 = (iso2 or "").upper()
    if len(iso2) != 2 or not iso2.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2)


def country_flag(country_id: int) -> str:
    return flag_emoji(COUNTRY_ISO.get(int(country_id), ""))


def btn_label(text: str, max_len: int = 60) -> str:
    t = text.strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def paginate(items: list, page: int, per_page: int) -> tuple[list, int, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start : start + per_page], page, total_pages


def pager_buttons(prefix: str, page: int, total_pages: int) -> list[dict]:
    row: list[dict] = []
    if page > 0:
        row.append({"text": "◀ Prev", "callback_data": f"{prefix}:{page - 1}"})
    row.append({"text": f"· {page + 1}/{total_pages} ·", "callback_data": "m:noop"})
    if page < total_pages - 1:
        row.append({"text": "Next ▶", "callback_data": f"{prefix}:{page + 1}"})
    return row


def _parse_country_success_list(raw: Any) -> list[dict[str, Any]]:
    """Normalize assorted API shapes into country stat dicts."""
    items: list[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        if raw.get("status") == "success" and isinstance(raw.get("response"), list):
            items = raw["response"]
        elif raw.get("status") == "success" and isinstance(raw.get("data"), list):
            items = raw["data"]
        else:
            for val in raw.values():
                if isinstance(val, dict) and "country" in val:
                    items.append(val)
                elif isinstance(val, list):
                    items.extend(val)
    rows: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            cid = int(it.get("country", it.get("countryId", it.get("id", -1))))
        except (TypeError, ValueError):
            continue
        row: dict[str, Any] = {"country": cid}
        for key in (
            "rate",
            "share",
            "count",
            "price",
            "retail_price",
            "success_12h",
            "success_24h",
            "success12",
            "success24",
            "count12",
            "count24",
            "successful12",
            "successful24",
            "delivered12",
            "delivered24",
        ):
            if key in it and it[key] is not None:
                row[key] = it[key]
        # Alternate nested windows
        for window in ("12", "24", 12, 24):
            block = it.get(str(window)) or it.get(f"h{window}")
            if isinstance(block, dict):
                for sk in ("success", "count", "successful", "delivered"):
                    if sk in block:
                        row[f"success_{window}"] = block[sk]
        if "rate" not in row and "success_rate" in it:
            row["rate"] = it["success_rate"]
        if "share" not in row and "share_percent" in it:
            row["share"] = it["share_percent"]
        rows.append(row)
    return rows


def _rows_have_success_windows(rows: list[dict[str, Any]]) -> bool:
    for r in rows:
        s12 = _row_success_count(r, 12)
        s24 = _row_success_count(r, 24)
        if s12 is not None and s24 is not None:
            return True
    return False


def _row_success_count(row: dict[str, Any], hours: int) -> int | None:
    for key in (f"success_{hours}", f"success{hours}", f"count{hours}", f"successful{hours}"):
        if key in row:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                pass
    return None


def format_top_success_report(service: str, rows: list[dict[str, Any]], bot: "Bot") -> str:
    """Build /top message with 50+ / 500+ / 1000+ sections when 12h&24h data exists."""
    svc_name = bot.service_label(service)
    lines = [
        f"📊 <b>Success stats — {esc(svc_name)}</b> ({mono_inline(service)})",
        "<i>Countries ranked by successful code deliveries (platform data).</i>",
        "",
    ]

    if _rows_have_success_windows(rows):
        thresholds = [
            (50, "🥇 <b>Top tier</b> — 50+ successful codes in <b>12h</b> and <b>24h</b>", 10),
            (500, "🔥 <b>High volume</b> — 500+ in <b>12h</b> and <b>24h</b>", None),
            (1000, "💎 <b>Elite volume</b> — 1000+ in <b>12h</b> and <b>24h</b>", None),
        ]
        for min_ok, title, limit in thresholds:
            bucket = []
            for r in rows:
                s12 = _row_success_count(r, 12) or 0
                s24 = _row_success_count(r, 24) or 0
                if s12 >= min_ok and s24 >= min_ok:
                    bucket.append((r, s12, s24))
            bucket.sort(key=lambda x: (-x[2], -x[1], -float(x[0].get("rate") or 0)))
            if limit:
                bucket = bucket[:limit]
            lines.append(title)
            if not bucket:
                lines.append("<i>None right now.</i>")
            else:
                for r, s12, s24 in bucket:
                    cid = int(r["country"])
                    flag = country_flag(cid)
                    name = bot.country_name(cid)
                    rate = r.get("rate")
                    extra = f" · {rate}% OK" if rate is not None else ""
                    lines.append(
                        f"{flag} <b>{esc(name)}</b> {mono_inline(cid)} — "
                        f"12h: <b>{s12}</b> · 24h: <b>{s24}</b>{extra}"
                    )
            lines.append("")
        return "\n".join(lines).strip()

    # Legacy SMS-Activate: rate + share only (top 10)
    rated = [r for r in rows if r.get("rate") is not None]
    rated.sort(key=lambda x: -float(x.get("rate") or 0))
    lines.append("🥇 <b>Top 10 by success rate</b> <i>(share = % of platform purchases)</i>")
    if not rated:
        lines.append("<i>No rate data returned.</i>")
    else:
        for r in rated[:10]:
            cid = int(r["country"])
            flag = country_flag(cid)
            name = bot.country_name(cid)
            lines.append(
                f"{flag} <b>{esc(name)}</b> — "
                f"rate <b>{esc(r.get('rate'))}%</b> · share <b>{esc(r.get('share'))}%</b>"
            )
    # Fallback: stock / price ranking (NOT the same as website success-rate widget)
    by_stock = []
    for r in rows:
        try:
            cnt = int(r.get("count") or 0)
        except (TypeError, ValueError):
            cnt = 0
        if cnt > 0:
            by_stock.append((r, cnt))
    by_stock.sort(key=lambda x: -x[1])
    lines.append("")
    lines.append("📦 <b>Available numbers now</b> <i>(stock — not success-rate stats)</i>")
    if not by_stock:
        lines.append("<i>No stock data.</i>")
    else:
        for r, cnt in by_stock[:15]:
            cid = int(r["country"])
            flag = country_flag(cid)
            name = bot.country_name(cid)
            price = r.get("price")
            pline = f" · {fmt_money(price)}" if price is not None else ""
            lines.append(f"{flag} <b>{esc(name)}</b> — <b>{cnt}</b> nums{pline}")
    lines.append(
        "\n⚠️ <i>12h / 24h <b>success counts</b> (50+ / 500+ / 1000+) are on the HeroSMS website "
        "but not in the public API yet. Tried <code>getListOfTopCountriesByService</code>. "
        "Ask HeroSMS support to expose success statistics for bots.</i>"
    )
    return "\n".join(lines)


def grid_keyboard(
    items: list[tuple[str, str]],
    *,
    cols: int,
    page: int,
    per_page: int,
    pager_prefix: str,
    footer: list[list[dict]] | None = None,
) -> dict:
    """items: [(button_text, callback_data), ...]"""
    chunk, page, total_pages = paginate(items, page, per_page)
    rows: list[list[dict]] = []
    row: list[dict] = []
    for label, cb in chunk:
        row.append({"text": label, "callback_data": cb})
        if len(row) >= cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if total_pages > 1:
        rows.append(pager_buttons(pager_prefix, page, total_pages))
    if footer:
        rows.extend(footer)
    else:
        rows.append([{"text": "🏠 Menu", "callback_data": "m:menu"}])
    return {"inline_keyboard": rows}


# Help: (command, emoji, title, description) grouped by category below
HELP_CATEGORIES: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    (
        "🏠",
        "Getting started",
        [
            ("/start", "👋", "Start", "Welcome screen + main menu buttons"),
            ("/menu", "🏠", "Menu", "Open the button menu anytime"),
            ("/help", "📖", "Help", "This guide"),
            ("/balance", "💰", "Balance", "Money left on hero-sms.com"),
            ("/alertbalance", "🔔", "Low balance alert", "Warn when below $X: /alertbalance 2"),
            ("/notify", "🔕", "Auto-alerts", "/notify on or /notify off"),
            ("/poll", "🔄", "Check now", "Force-check for new SMS/email codes"),
        ],
    ),
    (
        "🛒",
        "Buy SMS (phone numbers)",
        [
            ("/buy", "🛒", "Quick buy", "/buy tg 62 — service code + country id"),
            ("/buyv2", "🛒", "Quick buy (debug)", "Same as /buy, full API reply"),
            ("/countries", "🗺", "Country list", "All country IDs with flags (pages)"),
            ("/services", "📋", "Service codes", "App codes: tg, wa, ig…"),
            ("/prices", "💵", "Price check", "/prices tg 62"),
            ("/numbers", "📊", "Stock", "How many numbers available: /numbers 62"),
            ("/operators", "📶", "Operators", "Networks for a country: /operators 62"),
            ("/top", "📈", "Best countries", "Ranking for a service: /top tg"),
        ],
    ),
    (
        "📱",
        "SMS orders (after you buy)",
        [
            ("/active", "📱", "Active SMS", "Numbers still waiting for a code"),
            ("/status", "🔍", "Status", "Did the code arrive? /status ID"),
            ("/sms", "💬", "All texts", "Every SMS for an order: /sms ID"),
            ("/ready", "📨", "Ready", "Tell API you are waiting: /ready ID"),
            ("/resend", "🔁", "Resend", "Ask to send code again: /resend ID"),
            ("/complete", "✅", "Complete", "Done after you got the code: /complete ID"),
            ("/cancel", "❌", "Cancel", "Refund if no code yet: /cancel ID"),
            ("/finish", "🏁", "Finish", "Close on HeroSMS: /finish ID"),
            ("/history", "🕐", "History", "Last SMS orders (24h)"),
        ],
    ),
    (
        "📧",
        "Email verification",
        [
            ("/email", "📧", "Buy email", "/email site.com gmail.com"),
            ("/domains", "🌐", "List domains", "Prices for a site: /domains instagram.com"),
            ("/activeemail", "📬", "Active email", "Inboxes still waiting for a message"),
            ("/emails", "📬", "All emails", "Full email order history"),
            ("/mail", "📩", "Read inbox", "OTP / message: /mail ID"),
            ("/mailcancel", "❌", "Cancel email", "/mailcancel ID"),
            ("/mailreorder", "🔁", "Reorder", "Buy same again: /mailreorder ID"),
        ],
    ),
    (
        "⭐",
        "Favorites (one-tap rebuy)",
        [
            ("/favs", "⭐", "List", "Saved combos with buy buttons"),
            ("/favbuy", "⚡", "Buy", "/favbuy tr_tg"),
            ("/favadd", "➕", "Add", "/favadd name tg 62"),
            ("/favlabel", "✏️", "Rename", "/favlabel tr_tg Turkey TG"),
            ("/favdel", "🗑", "Delete", "/favdel tr_tg"),
        ],
    ),
]

HELP_DIVIDER = "──────────────────"


def help_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🛒 Buy SMS", "callback_data": "m:buysms"},
                {"text": "⭐ Favorites", "callback_data": "m:favs"},
            ],
            [
                {"text": "📱 Active SMS", "callback_data": "m:active"},
                {"text": "📬 Active email", "callback_data": "m:activeem"},
            ],
            [
                {"text": "💰 Balance", "callback_data": "m:bal"},
                {"text": "🏠 Main menu", "callback_data": "m:menu"},
            ],
        ]
    }


def build_help_text() -> str:
    """Single-page help grouped by category (fits Telegram 4096 limit)."""
    lines = [
        "📖 <b>HeroSMS Bot — Help</b>",
        "",
        HELP_DIVIDER,
        "🚀 <b>Quick start</b>",
        HELP_DIVIDER,
        "1️⃣ Tap <b>Buy SMS</b> or ⭐ <b>Favorites</b>",
        "2️⃣ Paste the number on the app/site",
        "3️⃣ Code arrives here — tap <b>Copy</b>",
        "",
        "🎛 <b>Menu:</b> Active SMS · Active email · Favorites · Buy · Balance · Help",
        "",
        HELP_DIVIDER,
        "📋 <b>Commands by category</b>",
        HELP_DIVIDER,
    ]
    for cat_emoji, cat_name, entries in HELP_CATEGORIES:
        lines.append(f"\n{cat_emoji} <b>{esc(cat_name)}</b>")
        for cmd, emoji, title, desc in entries:
            lines.append(f"{emoji} {mono_inline(cmd)} · <b>{esc(title)}</b> — {esc(desc)}")
    lines.extend(
        [
            "",
            HELP_DIVIDER,
            "🏷 <b>Service codes</b>",
            HELP_DIVIDER,
            "<code>tg</code> Telegram · <code>wa</code> WhatsApp · <code>ig</code> Instagram",
            "<code>go</code> Google · <code>fb</code> Facebook · <code>ds</code> Discord",
            "",
            HELP_DIVIDER,
            "⭐ <b>Default favorites</b>",
            HELP_DIVIDER,
            "<code>tr_tg</code> 🇹🇷 Turkey TG · <code>cl_tg</code> 🇨🇱 Chile TG",
            "",
            "❓ Stuck? /menu — use the buttons.",
        ]
    )
    return "\n".join(lines)


# ─── Bot logic ─────────────────────────────────────────────────────────────────


class Bot:
    def __init__(self) -> None:
        if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.startswith("PASTE_"):
            raise SystemExit(
                "Set TELEGRAM_TOKEN (env var). Example: export TELEGRAM_TOKEN='123:ABC...'"
            )
        if not HEROSMS_API_KEY or HEROSMS_API_KEY.startswith("PASTE_"):
            raise SystemExit("Set HEROSMS_API_KEY (env var) from hero-sms.com")
        if not ALLOWED_USER_ID:
            raise SystemExit("Set ALLOWED_USER_ID (env var) — your numeric id from @userinfobot")

        self.tg = Telegram(TELEGRAM_TOKEN)
        self.api = HeroSMS(HEROSMS_API_KEY)
        self.favs = FavoritesStore()
        self.state = AppState()
        self.allowed = ALLOWED_USER_ID
        self.offset = 0
        self.notify = True
        self.balance_alert_low = BALANCE_ALERT_LOW
        self._seen_sms: dict[str, str] = {}
        self._seen_mail: dict[str, str] = {}
        self._lock = threading.Lock()
        self._poll_stop = threading.Event()
        self._session: dict[int, dict[str, Any]] = {}
        self._services_cache: list[dict[str, Any]] | None = None
        self._countries_by_id: dict[int, dict[str, Any]] = {}

    def session(self, chat_id: int) -> dict[str, Any]:
        if chat_id not in self._session:
            self._session[chat_id] = {}
        return self._session[chat_id]

    def allowed_user(self, user_id: int | None) -> bool:
        return user_id == self.allowed

    def deny(self, chat_id: int) -> None:
        self.tg.send(
            chat_id,
            "⛔ This bot is private. Your user id is not authorized.\n"
            f"Your id: <code>{chat_id}</code>",
        )

    def ui_send(
        self,
        chat_id: int,
        text: str,
        markup: dict | None = None,
        *,
        message_id: int | None = None,
    ) -> int | None:
        if message_id:
            try:
                self.tg.edit(chat_id, message_id, text, markup)
                return message_id
            except Exception:
                pass
        return self.tg.send(chat_id, text, reply_markup=markup)

    def show_menu(self, chat_id: int, *, message_id: int | None = None) -> None:
        self.ui_send(
            chat_id,
            "🏠 <b>Main menu</b>\n\n"
            "📱 Active SMS · 📬 Active email · ⭐ Favorites · 🛒 Buy · 📧 Email\n"
            "💰 Balance · 📖 Help\n\n"
            "<i>Tip: 🛒 Buy SMS walks you through step by step.</i>",
            main_keyboard(),
            message_id=message_id,
        )

    def load_countries_map(self) -> dict[int, dict[str, Any]]:
        if not self._countries_by_id:
            for c in self.api.get_countries():
                try:
                    cid = int(c.get("id"))
                except (TypeError, ValueError):
                    continue
                self._countries_by_id[cid] = c
        return self._countries_by_id

    def country_name(self, country_id: int) -> str:
        c = self.load_countries_map().get(int(country_id), {})
        return str(c.get("eng") or c.get("rus") or f"Country {country_id}")

    def load_services(self) -> list[dict[str, Any]]:
        if self._services_cache is None:
            svcs = self.api.get_services()
            self._services_cache = sorted(svcs, key=lambda s: (s.get("name") or s.get("code", "")).lower())
        return self._services_cache

    def service_label(self, code: str) -> str:
        for s in self.load_services():
            if s.get("code") == code:
                return str(s.get("name") or code)
        return code

    def notify_balance_after_code(self, chat_id: int) -> str:
        try:
            bal = self.api.get_balance()
            line = f"\n\n💰 Balance: <b>{fmt_money(bal)}</b>"
            if bal < self.balance_alert_low:
                line += f"\n⚠️ Low balance (under {fmt_money(self.balance_alert_low)})"
            return line
        except Exception as e:
            log.warning("balance after code: %s", e)
            return ""

    # ── Guided: Buy SMS ─────────────────────────────────────────────────────

    def flow_buy_sms_services(self, chat_id: int, page: int = 0, *, message_id: int | None = None) -> None:
        svcs = self.load_services()
        if not svcs:
            self.ui_send(chat_id, "❌ Could not load services.", main_keyboard(), message_id=message_id)
            return
        self.session(chat_id)["flow"] = "buy_sms"
        items: list[tuple[str, str]] = []
        for s in svcs:
            code = str(s.get("code", "")).strip()
            if not code or len(code) > 32:
                continue
            name = str(s.get("name") or code)
            items.append((btn_label(f"{code} · {name}", 58), f"sms:s:{code}"))
        markup = grid_keyboard(
            items,
            cols=SERVICES_COLS,
            page=page,
            per_page=SERVICES_PER_PAGE,
            pager_prefix="sms:p",
            footer=[[{"text": "🏠 Menu", "callback_data": "m:menu"}]],
        )
        chunk, pg, total = paginate(items, page, SERVICES_PER_PAGE)
        self.ui_send(
            chat_id,
            f"🛒 <b>Step 1 of 3 — Pick an app</b>\n\n"
            f"📲 Which service needs a phone number?\n"
            f"📄 Page <b>{pg + 1}/{total}</b> · showing {len(chunk)} of {len(items)}",
            markup,
            message_id=message_id,
        )

    def flow_buy_sms_countries(
        self, chat_id: int, service: str, page: int = 0, *, message_id: int | None = None
    ) -> None:
        s = self.session(chat_id)
        s["buy_service"] = service
        try:
            offers = self.api.get_top_countries_for_service(service)
        except Exception as e:
            self.ui_send(chat_id, f"❌ {esc(e)}", main_keyboard(), message_id=message_id)
            return
        if not offers:
            self.ui_send(
                chat_id,
                f"😔 No numbers in stock for <b>{esc(service)}</b> right now.\nTry another service.",
                main_keyboard(),
                message_id=message_id,
            )
            return
        s["buy_offers"] = offers
        s["buy_country_page"] = page
        items: list[tuple[str, str]] = []
        for o in offers:
            cid = int(o["country"])
            flag = country_flag(cid)
            name = self.country_name(cid)
            price = fmt_money(o["price"])
            label = btn_label(f"{flag} {price} · {name}", 60)
            items.append((label, f"sms:y:{cid}"))
        markup = grid_keyboard(
            items,
            cols=COUNTRY_BUY_COLS,
            page=page,
            per_page=COUNTRY_BUY_PER_PAGE,
            pager_prefix="sms:c",
            footer=[
                [{"text": "◀ Change service", "callback_data": "m:buysms"}],
                [{"text": "🏠 Menu", "callback_data": "m:menu"}],
            ],
        )
        chunk, pg, total = paginate(items, page, COUNTRY_BUY_PER_PAGE)
        svc_name = self.service_label(service)
        self.ui_send(
            chat_id,
            f"🌍 <b>Step 2 of 3 — Pick a country</b>\n\n"
            f"📲 Service: <b>{esc(svc_name)}</b> ({mono_inline(service)})\n"
            f"💵 Cheapest first · only countries with numbers in stock\n"
            f"📄 Page <b>{pg + 1}/{total}</b> · {len(offers)} countries available",
            markup,
            message_id=message_id,
        )

    def flow_buy_sms_confirm(
        self, chat_id: int, country_id: int, *, message_id: int | None = None
    ) -> None:
        s = self.session(chat_id)
        service = s.get("buy_service")
        if not service:
            self.flow_buy_sms_services(chat_id, message_id=message_id)
            return
        offer = next((o for o in s.get("buy_offers", []) if int(o["country"]) == int(country_id)), None)
        if not offer:
            self.flow_buy_sms_countries(chat_id, service, message_id=message_id)
            return
        s["buy_country"] = int(country_id)
        s["buy_price"] = float(offer["price"])
        flag = country_flag(country_id)
        name = self.country_name(country_id)
        svc_name = self.service_label(service)
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm purchase", "callback_data": "sms:ok"},
                    {"text": "❌ Cancel", "callback_data": f"sms:c:0"},
                ],
                [{"text": "🏠 Menu", "callback_data": "m:menu"}],
            ]
        }
        self.ui_send(
            chat_id,
            f"🧾 <b>Step 3 of 3 — Confirm purchase</b>\n\n"
            f"📲 App: <b>{esc(svc_name)}</b> ({mono_inline(service)})\n"
            f"🌍 Country: {flag} <b>{esc(name)}</b>\n"
            f"💵 Price: <b>{fmt_money(offer['price'])}</b>\n"
            f"📦 In stock: <b>{offer['count']}</b> numbers\n\n"
            "✅ Tap <b>Confirm purchase</b> when ready.",
            markup,
            message_id=message_id,
        )

    def flow_buy_sms_execute(self, chat_id: int, *, message_id: int | None = None) -> None:
        s = self.session(chat_id)
        service = s.get("buy_service")
        country_id = s.get("buy_country")
        price = s.get("buy_price")
        if not service or country_id is None:
            self.flow_buy_sms_services(chat_id, message_id=message_id)
            return
        self.ui_send(chat_id, "⏳ Purchasing number…", message_id=message_id)
        try:
            self.perform_buy(
                chat_id,
                str(service),
                int(country_id),
                max_price=price,
                label=f"{self.service_label(str(service))} · {self.country_name(int(country_id))}",
                skip_ordering_msg=True,
            )
        except HeroSMSBuyError as e:
            self.handle_buy_failure(chat_id, e, message_id=message_id)

    def handle_buy_failure(
        self, chat_id: int, err: HeroSMSBuyError, *, message_id: int | None = None
    ) -> None:
        """Show a clear error and resume guided flow where the user left off."""
        msg = BUY_ERROR_MESSAGES.get(err.code, err.detail)
        s = self.session(chat_id)

        if s.get("flow") == "buy_sms" and s.get("buy_service"):
            service = str(s["buy_service"])
            page = int(s.get("buy_country_page", 0))
            country_id = s.get("buy_country")
            cname = self.country_name(int(country_id)) if country_id is not None else "?"
            self.ui_send(
                chat_id,
                f"😔 <b>{esc(msg)}</b>\n\n"
                f"Tried: {country_flag(int(country_id)) if country_id is not None else '🌍'} "
                f"<b>{esc(cname)}</b>\n"
                "Choose another country:",
                message_id=message_id,
            )
            self.flow_buy_sms_countries(chat_id, service, page=page)
            return

        self.tg.send(chat_id, f"❌ <b>{esc(msg)}</b>", reply_markup=main_keyboard())

    # ── Guided: Countries list ────────────────────────────────────────────────

    def flow_countries_list(self, chat_id: int, page: int = 0, *, message_id: int | None = None) -> None:
        cmap = self.load_countries_map()
        countries = sorted(cmap.values(), key=lambda c: (c.get("eng") or "").lower())
        items: list[tuple[str, str]] = []
        for c in countries:
            if not c.get("visible", 1):
                continue
            cid = int(c["id"])
            flag = country_flag(cid)
            name = c.get("eng") or c.get("rus") or "?"
            items.append((btn_label(f"{flag} {cid} · {name}", 60), f"cn:i:{cid}"))
        markup = grid_keyboard(
            items,
            cols=COUNTRY_LIST_COLS,
            page=page,
            per_page=COUNTRY_LIST_PER_PAGE,
            pager_prefix="cn:p",
            footer=[[{"text": "🏠 Menu", "callback_data": "m:menu"}]],
        )
        chunk, pg, total = paginate(items, page, COUNTRY_LIST_PER_PAGE)
        self.ui_send(
            chat_id,
            f"🗺 <b>All countries</b> ({len(items)} total)\n\n"
            f"Use the id with manual buy: <code>/buy tg COUNTRY_ID</code>\n"
            f"Page <b>{pg + 1}/{total}</b> · showing {len(chunk)}",
            markup,
            message_id=message_id,
        )

    # ── Guided: Buy email ─────────────────────────────────────────────────────

    def flow_buy_email_sites(self, chat_id: int, page: int = 0, *, message_id: int | None = None) -> None:
        if not self.api.mail_api_enabled:
            self.ui_send(
                chat_id,
                "📭 <b>Email API unavailable</b>\n"
                "Could not reach <code>hero-sms.com/api/v1/emails</code> with your API key.\n"
                "SMS buying still works via <b>Buy SMS</b>.",
                main_keyboard(),
                message_id=message_id,
            )
            return
        self.session(chat_id)["flow"] = "buy_email"
        sites = POPULAR_EMAIL_SITES
        self.session(chat_id)["em_sites"] = sites
        items = [(btn_label(site, 58), f"em:i:{i}") for i, site in enumerate(sites)]
        markup = grid_keyboard(
            items,
            cols=EMAIL_SITES_COLS,
            page=page,
            per_page=EMAIL_SITES_PER_PAGE,
            pager_prefix="em:sp",
            footer=[[{"text": "🏠 Menu", "callback_data": "m:menu"}]],
        )
        self.ui_send(
            chat_id,
            "📧 <b>Step 1/3 — Choose website</b>\n\n"
            "Pick the site you need email verification for.\n"
            "Or type: <code>/domains yoursite.com</code>",
            markup,
            message_id=message_id,
        )

    def flow_buy_email_domains(
        self, chat_id: int, site: str, page: int = 0, *, message_id: int | None = None
    ) -> None:
        s = self.session(chat_id)
        s["email_site"] = site
        try:
            data = self.api.mail_domains(site)
        except Exception as e:
            self.ui_send(chat_id, f"❌ {esc(e)}", main_keyboard(), message_id=message_id)
            return
        domains: list[dict[str, Any]] = list(data.get("domains") or [])
        if not domains:
            self.ui_send(chat_id, f"No domains for <b>{esc(site)}</b>.", main_keyboard(), message_id=message_id)
            return
        domains.sort(key=lambda d: float(d.get("cost") or 0))
        s["email_domains"] = domains
        items: list[tuple[str, str]] = []
        for i, d in enumerate(domains):
            price = fmt_money(d.get("cost", 0))
            stock = d.get("count", "?")
            label = btn_label(f"💵 {price} · {d['name']} ({stock})", 60)
            items.append((label, f"em:d:{i}"))
        markup = grid_keyboard(
            items,
            cols=EMAIL_DOMAIN_COLS,
            page=page,
            per_page=EMAIL_DOMAIN_PER_PAGE,
            pager_prefix="em:dp",
            footer=[
                [{"text": "◀ Change site", "callback_data": "m:buyem"}],
                [{"text": "🏠 Menu", "callback_data": "m:menu"}],
            ],
        )
        self.ui_send(
            chat_id,
            f"📬 <b>Step 2/3 — Choose domain</b>\n\n"
            f"Site: <b>{esc(site)}</b>\n"
            f"Sorted by price · only domains with stock.",
            markup,
            message_id=message_id,
        )

    def flow_buy_email_confirm(
        self, chat_id: int, domain_idx: int, *, message_id: int | None = None
    ) -> None:
        s = self.session(chat_id)
        site = s.get("email_site")
        domains = s.get("email_domains") or []
        if site is None or domain_idx >= len(domains):
            self.flow_buy_email_sites(chat_id, message_id=message_id)
            return
        d = domains[domain_idx]
        s["email_pick"] = d
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm purchase", "callback_data": "em:ok"},
                    {"text": "❌ Cancel", "callback_data": "m:buyem"},
                ],
                [{"text": "🏠 Menu", "callback_data": "m:menu"}],
            ]
        }
        self.ui_send(
            chat_id,
            f"🧾 <b>Step 3/3 — Confirm email</b>\n\n"
            f"Site: <b>{esc(site)}</b>\n"
            f"Domain: <b>{esc(d['name'])}</b>\n"
            f"Price: <b>{fmt_money(d.get('cost', 0))}</b>\n\n"
            "Tap confirm to buy.",
            markup,
            message_id=message_id,
        )

    def flow_buy_email_execute(self, chat_id: int) -> None:
        s = self.session(chat_id)
        site = s.get("email_site")
        d = s.get("email_pick")
        if not site or not d:
            self.flow_buy_email_sites(chat_id)
            return
        self.tg.send(chat_id, f"⏳ Buying email for <code>{esc(site)}</code>…")
        r = self.api.mail_buy(site, str(d["name"]))
        mid, email = r.get("id"), r.get("email")
        kb = {
            "inline_keyboard": [
                [
                    {"text": "📬 Active email", "callback_data": "m:activeem"},
                    {"text": f"📩 Check #{mid}", "callback_data": f"em:mail:{mid}"},
                ],
                [
                    {"text": f"❌ Cancel #{mid}", "callback_data": f"em:cancel:{mid}"},
                    {"text": "🏠 Menu", "callback_data": "m:menu"},
                ],
            ]
        }
        self.tg.send(
            chat_id,
            f"✅ <b>Email purchased</b>\n\n"
            f"ID: {mono_inline(mid)}\n"
            f"Email:\n{mono_block(str(email), 80)}\n\n"
            f"<i>Waiting for message — you'll get an alert when it arrives.</i>",
            reply_markup=kb,
        )
        with self._lock:
            self._seen_mail[str(mid)] = ""

    def handle_command(self, chat_id: int, text: str, message_id: int) -> None:
        parts = text.strip().split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        try:
            if cmd in ("/start", "/menu"):
                self.cmd_start(chat_id)
            elif cmd == "/countries":
                self.flow_countries_list(chat_id)
            elif cmd == "/help":
                self.cmd_help(chat_id)
            elif cmd == "/balance":
                self.cmd_balance(chat_id)
            elif cmd == "/buy":
                self.cmd_buy(chat_id, args, v2=False)
            elif cmd == "/buyv2":
                self.cmd_buy(chat_id, args, v2=True)
            elif cmd == "/active":
                self.cmd_active(chat_id)
            elif cmd in ("/activeemail", "/activeem"):
                self.cmd_active_emails(chat_id)
            elif cmd == "/status":
                self.cmd_status(chat_id, args)
            elif cmd in ("/ready", "/complete", "/cancel", "/resend"):
                self.cmd_set_status(chat_id, args, cmd.lstrip("/"))
            elif cmd == "/finish":
                self.cmd_finish(chat_id, args)
            elif cmd == "/sms":
                self.cmd_sms(chat_id, args)
            elif cmd == "/prices":
                self.cmd_prices(chat_id, args)
            elif cmd == "/services":
                self.cmd_services(chat_id, args)
            elif cmd == "/operators":
                self.cmd_operators(chat_id, args)
            elif cmd == "/top":
                self.cmd_top(chat_id, args)
            elif cmd == "/history":
                self.cmd_history(chat_id)
            elif cmd == "/numbers":
                self.cmd_numbers(chat_id, args)
            elif cmd == "/domains":
                self.cmd_domains(chat_id, args)
            elif cmd in ("/email", "/mailbuy"):
                self.cmd_email_buy(chat_id, args)
            elif cmd == "/emails":
                self.cmd_emails(chat_id)
            elif cmd == "/mail":
                self.cmd_mail_check(chat_id, args)
            elif cmd == "/mailcancel":
                self.cmd_mail_cancel(chat_id, args)
            elif cmd == "/mailreorder":
                self.cmd_mail_reorder(chat_id, args)
            elif cmd == "/notify":
                self.cmd_notify(chat_id, args)
            elif cmd == "/poll":
                self.poll_once(chat_id, notify=True)
            elif cmd in ("/favs", "/favorites"):
                self.cmd_favs(chat_id)
            elif cmd == "/favadd":
                self.cmd_favadd(chat_id, args)
            elif cmd == "/favlabel":
                self.cmd_favlabel(chat_id, args)
            elif cmd == "/favdel":
                self.cmd_favdel(chat_id, args)
            elif cmd in ("/favbuy", "/fav"):
                self.cmd_favbuy(chat_id, args)
            elif cmd == "/alertbalance":
                self.cmd_alertbalance(chat_id, args)
            else:
                self.tg.send(
                    chat_id,
                    "❓ <b>Unknown command</b>\n\n"
                    "Tap 📖 <b>Help</b> or send /help for the full list.\n"
                    "/menu — open buttons",
                    reply_markup=help_keyboard(),
                )
        except HeroSMSBuyError as e:
            self.handle_buy_failure(chat_id, e)
        except Exception as e:
            log.exception("command %s", cmd)
            self.tg.send(chat_id, f"❌ <b>Error</b>\n<pre>{esc(str(e))}</pre>")

    def cmd_start(self, chat_id: int) -> None:
        bal = "…"
        try:
            bal = fmt_money(self.api.get_balance())
        except Exception:
            pass
        text = (
            "✨ <b>Welcome to HeroSMS Bot</b>\n\n"
            f"💰 Balance: <b>{bal}</b>\n\n"
            "🛒 <b>Buy SMS</b> · 📧 <b>Buy email</b> — guided buttons\n"
            "📱 <b>Active SMS</b> · 📬 <b>Active email</b> — waiting orders + cancel\n"
            "📨 Codes arrive with 📋 <b>Copy</b> button\n\n"
            "👇 Pick an action, or /help for every command"
        )
        self.tg.send(chat_id, text, reply_markup=main_keyboard())

    def cmd_help(self, chat_id: int) -> None:
        """Send help as one message (categories, no multi-page split)."""
        text = build_help_text()
        if len(text) > 4096:
            log.warning("Help text too long (%s chars), truncating", len(text))
            text = text[:4080] + "\n…"
        self.tg.send(chat_id, text, reply_markup=help_keyboard())

    def cmd_balance(self, chat_id: int) -> None:
        b = self.api.get_balance()
        warn = ""
        if b < self.balance_alert_low:
            warn = f"\n\n⚠️ Below alert threshold ({fmt_money(self.balance_alert_low)})"
        self.tg.send(
            chat_id,
            f"💰 <b>Your balance</b>\n\n"
            f"💵 Available: <b>{fmt_money(b)}</b>{warn}\n\n"
            f"🔗 Top up at hero-sms.com if needed.",
        )

    def perform_buy(
        self,
        chat_id: int,
        service: str,
        country: int,
        *,
        max_price: str | float | None = None,
        label: str | None = None,
        v2: bool = False,
        skip_ordering_msg: bool = False,
    ) -> None:
        kw: dict[str, Any] = {}
        if max_price is not None:
            kw["maxPrice"] = max_price
        title = f" ({esc(label)})" if label else ""
        if not skip_ordering_msg:
            self.tg.send(chat_id, f"⏳ Ordering {mono_inline(service)} / country {country}{title}…")
        try:
            if v2:
                data = self.api.get_number_v2(service, country, **kw)
            else:
                aid, phone = self.api.get_number(service, country, **kw)
        except RuntimeError as e:
            raise HeroSMSBuyError(api_error_code(e), str(e)) from e
        if v2:
            self.tg.send(
                chat_id,
                f"✅ <b>Number ordered (v2)</b>\n{mono_block(json.dumps(data, indent=2), 3500)}",
            )
            return
        self.api.set_status(aid, SET_STATUS["ready"])
        text = (
            f"✅ <b>Number ready</b>{title}\n\n"
            f"ID: {mono_inline(aid)}\n"
            f"Phone:\n{mono_block(phone, 32)}\n"
            f"Service: {mono_inline(service)}\n\n"
            "Use this number on the site. I'll notify you when the SMS arrives.\n"
            f"/status {aid} · /cancel {aid}"
        )
        self.tg.send(
            chat_id,
            text,
            reply_markup={"inline_keyboard": [[{"text": "🏠 Menu", "callback_data": "m:menu"}]]},
        )
        with self._lock:
            self._seen_sms[str(aid)] = ""

    def cmd_buy(self, chat_id: int, args: list[str], v2: bool) -> None:
        if len(args) < 2:
            self.tg.send(
                chat_id,
                "Usage: <code>/buy service country [maxPrice]</code>\n"
                "Example: <code>/buy tg 6</code> or <code>/favbuy tr_tg</code>",
            )
            return
        service, country = args[0], int(args[1])
        mp = args[2] if len(args) >= 3 else None
        self.perform_buy(chat_id, service, country, max_price=mp, v2=v2)

    def cmd_favs(self, chat_id: int) -> None:
        if not self.favs.favorites:
            self.tg.send(
                chat_id,
                "No favorites yet.\n"
                "<code>/favadd tr_tg tg 62</code>\n"
                "<code>/favlabel tr_tg Turkey Telegram</code>",
            )
            return
        lines = [
            f"⭐ <b>Your favorites</b> ({len(self.favs.favorites)})",
            "",
            "<i>One tap to buy the same country + app again:</i>",
            "",
        ]
        lines.extend(fav_line(f) for f in self.favs.favorites)
        lines.append("\n🛒 Tap a button below · or <code>/favbuy name</code>")
        self.tg.send(chat_id, "\n".join(lines), reply_markup=favs_keyboard(self.favs.favorites))

    def cmd_favadd(self, chat_id: int, args: list[str]) -> None:
        if len(args) < 3:
            self.tg.send(
                chat_id,
                "Usage: <code>/favadd name service country [maxprice]</code>\n"
                "Example: <code>/favadd tr_tg tg 62 1.5</code>\n"
                "Then: <code>/favlabel tr_tg Turkey Telegram</code>",
            )
            return
        name = args[0].lower()
        if not FAV_NAME_RE.match(name):
            self.tg.send(chat_id, "Name must be 2-40 chars: lowercase letters, digits, underscore.")
            return
        service = args[1].lower()
        country = int(args[2])
        max_price = None
        if len(args) >= 4:
            try:
                max_price = float(args[3])
            except ValueError:
                self.tg.send(chat_id, "maxprice must be a number (e.g. 1.5)")
                return
        fav = {
            "name": name,
            "label": f"{service.upper()} · country {country}",
            "service": service,
            "country": country,
        }
        if max_price is not None:
            fav["max_price"] = max_price
        self.favs.add(fav)
        self.tg.send(chat_id, f"✅ Saved favorite <code>{esc(name)}</code>\n{fav_line(fav)}", reply_markup=favs_keyboard(self.favs.favorites))

    def cmd_favlabel(self, chat_id: int, args: list[str]) -> None:
        if len(args) < 2:
            self.tg.send(chat_id, "Usage: <code>/favlabel name Your display label</code>")
            return
        name = args[0].lower()
        fav = self.favs.get(name)
        if not fav:
            self.tg.send(chat_id, f"Favorite <code>{esc(name)}</code> not found.")
            return
        fav["label"] = " ".join(args[1:])[:80]
        self.favs.add(fav)
        self.tg.send(chat_id, f"✅ Label updated.\n{fav_line(fav)}", reply_markup=favs_keyboard(self.favs.favorites))

    def cmd_favdel(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/favdel name</code>")
            return
        if self.favs.delete(args[0].lower()):
            self.tg.send(chat_id, f"🗑 Removed <code>{esc(args[0].lower())}</code>", reply_markup=favs_keyboard(self.favs.favorites))
        else:
            self.tg.send(chat_id, "Favorite not found.")

    def cmd_favbuy(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.cmd_favs(chat_id)
            return
        fav = self.favs.get(args[0].lower())
        if not fav:
            self.tg.send(chat_id, f"Unknown favorite <code>{esc(args[0])}</code>. See /favs")
            return
        self.session(chat_id).update(
            {
                "flow": "buy_sms",
                "buy_service": fav["service"],
                "buy_country": int(fav["country"]),
                "buy_country_page": 0,
            }
        )
        try:
            self.perform_buy(
                chat_id,
                fav["service"],
                int(fav["country"]),
                max_price=fav.get("max_price"),
                label=fav.get("label"),
            )
        except HeroSMSBuyError as e:
            self.handle_buy_failure(chat_id, e)

    def cmd_alertbalance(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(
                chat_id,
                f"Low-balance alert when below <b>{fmt_money(self.balance_alert_low)}</b>\n"
                "Set: <code>/alertbalance 5</code>",
            )
            return
        try:
            self.balance_alert_low = float(args[0])
            self.tg.send(chat_id, f"✅ Alert threshold: <b>{fmt_money(self.balance_alert_low)}</b>")
        except ValueError:
            self.tg.send(chat_id, "Usage: <code>/alertbalance 2.5</code>")

    def cmd_active(self, chat_id: int) -> None:
        items = self.api.get_active()
        if not items:
            self.tg.send(
                chat_id,
                "📭 <b>No active SMS</b>\n\n"
                "You have no numbers waiting for a code right now.\n"
                "🛒 Tap <b>Buy SMS</b> to get one.",
                reply_markup=main_keyboard(),
            )
            return
        chunks = [activation_card(a) for a in items[:15]]
        self.tg.send(
            chat_id,
            f"📱 <b>Active SMS</b> ({len(items)})\n\n"
            f"<i>Numbers waiting for verification codes:</i>\n\n"
            + "\n\n".join(chunks)
            + "\n\n<i>Cancel:</i> <code>/cancel ID</code>",
            reply_markup=main_keyboard(),
        )

    def cmd_active_emails(self, chat_id: int) -> None:
        if not self.api.mail_api_enabled:
            self.tg.send(
                chat_id,
                "📭 <b>Email API unavailable</b>\n"
                "Could not reach HeroSMS email API.",
                reply_markup=main_keyboard(),
            )
            return
        items = self.api.mail_get_active()
        if not items:
            self.tg.send(
                chat_id,
                "📭 <b>No active email</b>\n\n"
                "No inboxes waiting for a message right now.\n"
                "📧 Tap <b>Buy email</b> to get one.",
                reply_markup=main_keyboard(),
            )
            return
        chunks = [mail_card(m) for m in items[:10]]
        self.tg.send(
            chat_id,
            f"📬 <b>Active email</b> ({len(items)})\n\n"
            f"<i>Waiting for verification mail — tap buttons below:</i>\n\n"
            + "\n\n".join(chunks)
            + "\n\n<i>Or:</i> <code>/mail ID</code> · <code>/mailcancel ID</code>",
            reply_markup=active_emails_keyboard(items),
        )

    def cmd_status(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/status activation_id</code>")
            return
        aid = int(args[0])
        raw = self.api.get_status(aid)
        try:
            v2 = self.api.get_status_v2(aid)
            extra = f"\n\n<b>Details</b>\n<pre>{esc(json.dumps(v2, indent=2)[:2500])}</pre>"
        except Exception:
            extra = ""
        self.tg.send(chat_id, f"📱 Activation <code>{aid}</code>\n{fmt_status(raw)}{extra}")

    def cmd_set_status(self, chat_id: int, args: list[str], action: str) -> None:
        if not args:
            self.tg.send(chat_id, f"Usage: <code>/{action} activation_id</code>")
            return
        aid = int(args[0])
        r = self.api.set_status(aid, SET_STATUS[action])
        self.tg.send(chat_id, f"✅ <code>/{action}</code> → <code>{esc(r)}</code>")

    def cmd_finish(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/finish activation_id</code>")
            return
        self.api.finish(int(args[0]))
        self.tg.send(chat_id, "✅ Activation finished.")

    def cmd_sms(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/sms activation_id</code>")
            return
        data = self.api.get_all_sms(int(args[0]))
        self.tg.send(chat_id, f"📨 SMS list\n<pre>{esc(json.dumps(data, indent=2)[:3800])}</pre>")

    def cmd_prices(self, chat_id: int, args: list[str]) -> None:
        if len(args) < 2:
            self.tg.send(chat_id, "Usage: <code>/prices service country</code>")
            return
        data = self.api.get_prices(args[0], int(args[1]))
        self.tg.send(chat_id, f"💵 Prices\n<pre>{esc(json.dumps(data, indent=2)[:3800])}</pre>")

    def cmd_countries(self, chat_id: int) -> None:
        self.flow_countries_list(chat_id)

    def cmd_services(self, chat_id: int, args: list[str]) -> None:
        country = int(args[0]) if args else None
        svcs = self.api.get_services(country)
        lines = [f"<code>{esc(s.get('code')):>6}</code> {esc(s.get('name'))}" for s in svcs[:60]]
        self.tg.send(
            chat_id,
            f"<b>Services</b>{f' (country {country})' if country else ''}\n" + "\n".join(lines),
        )

    def cmd_operators(self, chat_id: int, args: list[str]) -> None:
        country = int(args[0]) if args else None
        data = self.api.get_operators(country)
        self.tg.send(chat_id, f"<b>Operators</b>\n<pre>{esc(json.dumps(data, indent=2)[:3800])}</pre>")

    def cmd_top(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(
                chat_id,
                "📊 <b>Success statistics</b>\n\n"
                "Usage: <code>/top service</code>\n"
                "Example: <code>/top tg</code> — Telegram\n\n"
                "Shows countries with the most successful code deliveries "
                "(50+ / 500+ / 1000+ in 12h &amp; 24h) when HeroSMS API provides them.",
                reply_markup=main_keyboard(),
            )
            return
        service = args[0].strip().lower()
        self.tg.send(chat_id, f"⏳ Loading success stats for <code>{esc(service)}</code>…")
        try:
            rows = self.api.get_country_success_statistics(service)
            if not rows:
                rows = self.api.get_list_of_top_countries_by_service(service)
        except RuntimeError as e:
            if "BAD_ACTION" in str(e) or "Method Not Found" in str(e):
                self.tg.send(
                    chat_id,
                    f"📊 <b>Stats unavailable via API</b>\n\n"
                    f"HeroSMS does not expose the website success-ranking endpoint on "
                    f"<code>handler_api.php</code> yet.\n\n"
                    f"Tried: <code>getListOfTopCountriesByService</code> and related actions.\n\n"
                    f"<b>Workaround:</b> check rankings on hero-sms.com when buying "
                    f"<code>{esc(service)}</code>, or ask support to enable the statistics API.\n\n"
                    f"<b>Still works:</b> /favs · Buy SMS menu · <code>/prices {esc(service)} COUNTRY_ID</code>",
                    reply_markup=main_keyboard(),
                )
                return
            raise
        if not rows:
            self.tg.send(
                chat_id,
                f"No statistics returned for <code>{esc(service)}</code>.\n"
                "The service code may be wrong — try /services.",
                reply_markup=main_keyboard(),
            )
            return
        report = format_top_success_report(service, rows, self)
        kb = {
            "inline_keyboard": [
                [{"text": f"🛒 Buy {service}", "callback_data": f"sms:s:{service}"}],
                [{"text": "🏠 Menu", "callback_data": "m:menu"}],
            ]
        }
        self.tg.send(chat_id, report, reply_markup=kb)

    def cmd_history(self, chat_id: int) -> None:
        now = int(time.time())
        data = self.api.get_history(now - 86400, now, size=15)
        if isinstance(data, list) and data:
            chunks = []
            for h in data[:10]:
                chunks.append(
                    f"#{esc(h.get('id'))} <code>{esc(h.get('phone'))}</code> "
                    f"{esc(h.get('sms', ''))[:80]} · {fmt_money(h.get('cost', 0))}"
                )
            self.tg.send(chat_id, "<b>History (24h)</b>\n" + "\n".join(chunks))
        else:
            self.tg.send(chat_id, f"<b>History</b>\n<pre>{esc(json.dumps(data, indent=2)[:3800])}</pre>")

    def cmd_numbers(self, chat_id: int, args: list[str]) -> None:
        country = int(args[0]) if args else 0
        data = self.api.get_numbers_status(country)
        if isinstance(data, dict):
            top = sorted(data.items(), key=lambda x: -int(x[1]) if str(x[1]).isdigit() else 0)[:25]
            lines = [f"<code>{esc(k)}</code>: {esc(v)}" for k, v in top]
            self.tg.send(chat_id, f"<b>Stock (country {country})</b>\n" + "\n".join(lines))
        else:
            self.tg.send(chat_id, f"<pre>{esc(json.dumps(data, indent=2)[:3800])}</pre>")

    def cmd_domains(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/domains site.com</code>")
            return
        site = args[0]
        data = self.api.mail_domains(site)
        domains = data.get("domains") or []
        lines = [f"<b>Domains for {esc(site)}</b> (cheapest first)"]
        for d in domains[:20]:
            lines.append(
                f"• <code>{esc(d.get('name'))}</code> — {fmt_money(d.get('cost'))} "
                f"({esc(d.get('count', '?'))} in stock)"
            )
        lines.append(f"\nBuy: <code>/email {esc(site)} gmail.com</code>")
        self.tg.send(chat_id, "\n".join(lines))

    def cmd_email_buy(self, chat_id: int, args: list[str]) -> None:
        if len(args) < 2:
            self.tg.send(
                chat_id,
                "Usage: <code>/email site.com domain</code>\n"
                "Example: <code>/email instagram.com gmail.com</code>\n"
                "Tip: <code>/domains instagram.com</code> lists prices first.",
            )
            return
        site = args[0]
        domain = args[2] if len(args) >= 3 else args[1]
        self.tg.send(chat_id, f"⏳ Buying email for <code>{esc(site)}</code>…")
        r = self.api.mail_buy(site, domain)
        mid, email = r.get("id"), r.get("email")
        kb = {
            "inline_keyboard": [
                [
                    {"text": "📬 Active email", "callback_data": "m:activeem"},
                    {"text": f"📩 Check #{mid}", "callback_data": f"em:mail:{mid}"},
                ],
                [
                    {"text": f"❌ Cancel #{mid}", "callback_data": f"em:cancel:{mid}"},
                    {"text": "🏠 Menu", "callback_data": "m:menu"},
                ],
            ]
        }
        self.tg.send(
            chat_id,
            f"✅ <b>Email purchased</b>\n\n"
            f"ID: <code>{esc(mid)}</code>\n"
            f"Email: <code>{esc(email)}</code>\n\n"
            f"<i>Waiting for message — alert when it arrives.</i>",
            reply_markup=kb,
        )
        with self._lock:
            self._seen_mail[str(mid)] = ""

    def cmd_emails(self, chat_id: int) -> None:
        if not self.api.mail_api_enabled:
            self.tg.send(
                chat_id,
                "📭 <b>Email API unavailable</b> on your HeroSMS account.\n"
                "SMS numbers still work — use /favs or /buy.",
            )
            return
        data = self.api.mail_history()
        lst = data.get("list") or []
        if not lst:
            self.tg.send(chat_id, "📭 No email orders.")
            return
        chunks = [mail_card(m) for m in lst[:12]]
        self.tg.send(chat_id, f"<b>Email orders ({data.get('count', len(lst))})</b>\n\n" + "\n\n".join(chunks))

    def cmd_mail_check(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/mail id</code>")
            return
        mid = int(args[0])
        r = self.api.mail_check(mid)
        val = r.get("value")
        if val:
            otp = re.search(r"\b(\d{4,8})\b", str(val))
            kb = copy_code_keyboard(otp.group(1)) if otp else None
            body = f"📧 <b>Mail #{mid}</b>\n{mono_block(val, 3500)}"
            if otp:
                body = f"📧 <b>Mail #{mid}</b>\n{code_display(otp.group(1), 'OTP')}\n\n{mono_block(val, 2000)}"
            self.tg.send(chat_id, body, reply_markup=kb)
        else:
            st = esc(STATUS_LABELS.get(str(r.get("status", "")), r.get("status", "waiting")))
            self.tg.send(chat_id, f"📭 No message yet for #{mid}.\nStatus: <b>{st}</b>")

    def cmd_mail_cancel(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/mailcancel id</code>\nOr use 📬 <b>Active email</b> → ❌ Cancel")
            return
        self.cancel_email(chat_id, int(args[0]))

    def cancel_email(self, chat_id: int, mail_id: int) -> None:
        try:
            self.api.mail_cancel(mail_id)
            self.tg.send(
                chat_id,
                f"✅ <b>Email cancelled</b>\n\n"
                f"Order {mono_inline(mail_id)} refunded per HeroSMS rules.",
                reply_markup=main_keyboard(),
            )
        except Exception as e:
            self.tg.send(chat_id, f"❌ <b>Cancel failed</b>\n\n{esc(e)}", reply_markup=main_keyboard())

    def cmd_mail_reorder(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.tg.send(chat_id, "Usage: <code>/mailreorder id</code>")
            return
        r = self.api.mail_reorder(int(args[0]))
        self.tg.send(
            chat_id,
            f"✅ Reordered\nID: <code>{esc(r.get('id'))}</code>\nEmail: <code>{esc(r.get('email'))}</code>",
        )

    def cmd_notify(self, chat_id: int, args: list[str]) -> None:
        if args and args[0].lower() in ("off", "0", "false"):
            self.notify = False
            self.tg.send(chat_id, "🔕 Auto-notifications OFF")
        else:
            self.notify = True
            self.tg.send(chat_id, "🔔 Auto-notifications ON")

    def handle_callback(self, cq: dict) -> None:
        user_id = cq.get("from", {}).get("id")
        chat_id = cq["message"]["chat"]["id"]
        msg_id = cq["message"]["message_id"]
        if not self.allowed_user(user_id):
            self.tg.answer_callback(cq["id"], "Not authorized", alert=True)
            return
        data = cq.get("data", "")
        self.tg.answer_callback(cq["id"])

        if data == "m:noop":
            return

        # Main menu
        if data == "m:menu":
            self.show_menu(chat_id, message_id=msg_id)
        elif data == "m:bal":
            self.cmd_balance(chat_id)
        elif data == "m:active":
            self.cmd_active(chat_id)
        elif data == "m:activeem":
            self.cmd_active_emails(chat_id)
        elif data == "m:favs":
            self.cmd_favs(chat_id)
        elif data == "m:help":
            self.cmd_help(chat_id)
        elif data == "m:tophelp":
            self.tg.send(
                chat_id,
                "📈 <b>Success rankings</b>\n\n"
                "Shows which countries get codes most often.\n\n"
                "Example:\n"
                "<code>/top tg</code> — Telegram\n"
                "<code>/top wa</code> — WhatsApp\n\n"
                "<i>Uses HeroSMS stats when the API provides them.</i>",
                reply_markup=help_keyboard(),
            )
        elif data == "m:buysms":
            self.flow_buy_sms_services(chat_id, message_id=msg_id)
        elif data == "m:buyem":
            self.flow_buy_email_sites(chat_id, message_id=msg_id)

        # Buy SMS wizard
        elif data.startswith("sms:p:"):
            self.flow_buy_sms_services(chat_id, int(data.split(":")[2]), message_id=msg_id)
        elif data.startswith("sms:s:"):
            service = data.split(":", 2)[2]
            self.flow_buy_sms_countries(chat_id, service, message_id=msg_id)
        elif data.startswith("sms:c:"):
            svc = self.session(chat_id).get("buy_service", "tg")
            self.flow_buy_sms_countries(chat_id, str(svc), int(data.split(":")[2]), message_id=msg_id)
        elif data.startswith("sms:y:"):
            self.flow_buy_sms_confirm(chat_id, int(data.split(":")[2]), message_id=msg_id)
        elif data == "sms:ok":
            self.flow_buy_sms_execute(chat_id, message_id=msg_id)

        # Countries browser
        elif data.startswith("cn:p:"):
            self.flow_countries_list(chat_id, int(data.split(":")[2]), message_id=msg_id)
        elif data.startswith("cn:i:"):
            cid = int(data.split(":")[2])
            flag = country_flag(cid)
            self.tg.send(
                chat_id,
                f"{flag} <b>{esc(self.country_name(cid))}</b>\n"
                f"Country id: {mono_inline(cid)}\n\n"
                f"Manual buy: <code>/buy tg {cid}</code>",
            )

        # Buy email wizard
        elif data.startswith("em:sp:"):
            self.flow_buy_email_sites(chat_id, int(data.split(":")[2]), message_id=msg_id)
        elif data.startswith("em:i:"):
            idx = int(data.split(":")[2])
            sites = self.session(chat_id).get("em_sites") or POPULAR_EMAIL_SITES
            if 0 <= idx < len(sites):
                self.flow_buy_email_domains(chat_id, sites[idx], message_id=msg_id)
        elif data.startswith("em:dp:"):
            site = self.session(chat_id).get("email_site", "")
            self.flow_buy_email_domains(chat_id, str(site), int(data.split(":")[2]), message_id=msg_id)
        elif data.startswith("em:d:"):
            self.flow_buy_email_confirm(chat_id, int(data.split(":")[2]), message_id=msg_id)
        elif data == "em:ok":
            self.flow_buy_email_execute(chat_id)
        elif data.startswith("em:cancel:"):
            self.cancel_email(chat_id, int(data.split(":")[2]))
        elif data.startswith("em:mail:"):
            self.cmd_mail_check(chat_id, [data.split(":")[2]])

        # Legacy / extras
        elif data.startswith("act:complete:"):
            aid = int(data.split(":", 2)[2])
            self.api.set_status(aid, SET_STATUS["complete"])
            self.tg.send(chat_id, f"✅ Completed {mono_inline(aid)}")
        elif data.startswith("fav:buy:"):
            name = data.split(":", 2)[2]
            fav = self.favs.get(name)
            if not fav:
                self.tg.send(chat_id, f"Favorite <code>{esc(name)}</code> not found.")
                return
            self.session(chat_id).update(
                {
                    "flow": "buy_sms",
                    "buy_service": fav["service"],
                    "buy_country": int(fav["country"]),
                    "buy_country_page": 0,
                }
            )
            try:
                self.perform_buy(
                    chat_id,
                    fav["service"],
                    int(fav["country"]),
                    max_price=fav.get("max_price"),
                    label=fav.get("label"),
                )
            except HeroSMSBuyError as e:
                self.handle_buy_failure(chat_id, e)

    def poll_once(self, chat_id: int | None = None, notify: bool = False) -> None:
        target = chat_id or self.allowed
        do_notify = notify or self.notify

        # SMS activations
        try:
            for a in self.api.get_active():
                aid = str(a.get("activationId", ""))
                code = extract_sms_code(a)
                text_sig = f"{code}|{a.get('smsText', '')}"
                with self._lock:
                    prev = self._seen_sms.get(aid)
                    if prev is None:
                        self._seen_sms[aid] = text_sig
                        continue
                    if prev == text_sig:
                        continue
                    self._seen_sms[aid] = text_sig
                if code and do_notify:
                    bal_line = self.notify_balance_after_code(target)
                    msg = (
                        f"🔔 <b>New SMS code</b>\n\n{activation_card(a)}{bal_line}\n\n"
                        f"/complete {aid} · /sms {aid}"
                    )
                    kb = copy_code_keyboard(
                        code,
                        [
                            [{"text": f"✅ Complete {aid}", "callback_data": f"act:complete:{aid}"}],
                            [{"text": "🏠 Menu", "callback_data": "m:menu"}],
                        ],
                    )
                    self.tg.send(target, msg, reply_markup=kb)
        except Exception as e:
            log.warning("poll sms: %s", e)

        # Emails
        try:
            data = self.api.mail_history(per_page=25)
            for m in data.get("list") or []:
                mid = str(m.get("id", ""))
                val = str(m.get("value") or m.get("full_message") or "")
                st = str(m.get("status", ""))
                sig = f"{st}|{val}"
                with self._lock:
                    prev = self._seen_mail.get(mid)
                    if prev is None:
                        self._seen_mail[mid] = sig
                        continue
                    if prev == sig:
                        continue
                    self._seen_mail[mid] = sig
                if val and do_notify:
                    otp = re.search(r"\b(\d{4,8})\b", val)
                    kb = copy_code_keyboard(otp.group(1)) if otp else None
                    self.tg.send(
                        target,
                        f"🔔 <b>New email</b>\n\n{mail_card(m)}\n\n/mail {mid}",
                        reply_markup=kb,
                    )
                elif st in ("5", "SUCCESS") and do_notify and not val:
                    self.tg.send(target, f"✅ Email #{mid} marked success.\n/mail {mid}")
        except Exception as e:
            log.warning("poll mail: %s", e)

    def poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            self.poll_once()
            self._poll_stop.wait(POLL_INTERVAL_SEC)

    def run(self) -> None:
        # Verify API key
        try:
            bal = self.api.get_balance()
            log.info("HeroSMS balance: %s", bal)
        except Exception as e:
            log.error("HeroSMS API check failed: %s", e)
            raise SystemExit(f"Cannot connect to HeroSMS API: {e}") from e

        self.api.mail_probe()

        t = threading.Thread(target=self.poll_loop, daemon=True, name="poller")
        t.start()
        log.info("Bot running for user id %s — Ctrl+C to stop", self.allowed)

        self.tg.send(
            self.allowed,
            "🟢 <b>Bot is online</b>\n\n"
            "🛒 <b>Buy SMS</b> — step-by-step buttons\n"
            "📨 Auto-alerts when codes arrive\n"
            "📖 /help — commands by category\n\n"
            f"⏱ Checking every {POLL_INTERVAL_SEC}s for new messages.",
            reply_markup=main_keyboard(),
        )

        # Drop queued updates from before this run (old menu buttons crash the bot)
        try:
            pending = self.tg.get_updates(self.offset, timeout=1)
            if pending:
                self.offset = max(u["update_id"] for u in pending) + 1
                log.info("Cleared %d stale update(s) from queue", len(pending))
        except Exception as e:
            log.warning("startup drain: %s", e)

        while True:
            try:
                updates = self.tg.get_updates(self.offset, timeout=25)
            except Exception as e:
                log.warning("getUpdates: %s", e)
                time.sleep(3)
                continue
            for u in updates:
                self.offset = max(self.offset, u["update_id"] + 1)
                try:
                    if "callback_query" in u:
                        cq = u["callback_query"]
                        if self.allowed_user(cq.get("from", {}).get("id")):
                            self.handle_callback(cq)
                        continue
                    msg = u.get("message") or {}
                    uid = (msg.get("from") or {}).get("id")
                    chat_id = msg.get("chat", {}).get("id")
                    text = (msg.get("text") or "").strip()
                    if not chat_id or not text:
                        continue
                    if not self.allowed_user(uid):
                        self.deny(chat_id)
                        continue
                    if text.startswith("/"):
                        self.handle_command(chat_id, text, msg.get("message_id", 0))
                except HeroSMSBuyError as e:
                    self.handle_buy_failure(chat_id, e)
                except Exception as e:
                    log.exception("handler error")
                    try:
                        self.tg.send(
                            chat_id,
                            f"⚠️ <b>Unexpected error</b>\n<pre>{esc(str(e))[:500]}</pre>",
                            reply_markup=main_keyboard(),
                        )
                    except Exception:
                        pass


def fmt_status(raw: str) -> str:
    return parse_status_text(raw)


# ─── Tests (run: python hero_sms_bot.py --test) ───────────────────────────────


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.skipped: list[str] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        log.info("PASS  %s", name)

    def fail(self, name: str, err: str) -> None:
        self.failed.append(f"{name}: {err}")
        log.error("FAIL  %s — %s", name, err)

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append(f"{name}: {reason}")
        log.warning("SKIP  %s — %s", name, reason)

    def summary(self) -> int:
        print("\n" + "=" * 50)
        print(f"PASSED:  {len(self.passed)}")
        print(f"FAILED:  {len(self.failed)}")
        print(f"SKIPPED: {len(self.skipped)}")
        if self.failed:
            print("\nFailures:")
            for f in self.failed:
                print(f"  - {f}")
        if self.skipped:
            print("\nSkipped:")
            for s in self.skipped:
                print(f"  - {s}".encode("ascii", errors="replace").decode())
        print("=" * 50)
        return 1 if self.failed else 0


def run_tests() -> int:
    t = TestResult()

    # Unit: auth
    try:
        b = Bot.__new__(Bot)
        b.allowed = ALLOWED_USER_ID
        assert b.allowed_user(ALLOWED_USER_ID) is True
        assert b.allowed_user(999999999) is False
        assert b.allowed_user(None) is False
        t.ok("auth_whitelist")
    except Exception as e:
        t.fail("auth_whitelist", str(e))

    # Unit: parsers
    try:
        assert "12345" in parse_status_text("STATUS_OK:12345")
        assert esc("<>&") == "&lt;&gt;&amp;"
        assert "<pre>" in mono_block("999")
        card = activation_card(
            {"activationId": "1", "serviceCode": "tg", "phoneNumber": "+7900", "smsCode": "999"}
        )
        assert "tg" in card and "999" in card
        t.ok("formatting_helpers")
    except Exception as e:
        t.fail("formatting_helpers", str(e))

    # Unit: favorites store
    try:
        test_path = DATA_DIR / "_test_favs.json"
        store = FavoritesStore(test_path)
        store.add({"name": "x1", "label": "Test", "service": "tg", "country": 62})
        assert store.get("x1") is not None
        store.delete("x1")
        test_path.unlink(missing_ok=True)
        t.ok("favorites_store")
    except Exception as e:
        t.fail("favorites_store", str(e))

    # Config
    if ALLOWED_USER_ID > 0:
        t.ok("config_user_id")
    else:
        t.skip("config_user_id", "set ALLOWED_USER_ID env var")

    if TELEGRAM_TOKEN and not TELEGRAM_TOKEN.startswith("PASTE_"):
        t.ok("config_bot_token_set")
    else:
        t.skip("config_bot_token_set", "set TELEGRAM_TOKEN env var")

    has_hero_key = bool(HEROSMS_API_KEY) and not HEROSMS_API_KEY.startswith("PASTE_")

    # Live: Telegram getMe
    try:
        tg = Telegram(TELEGRAM_TOKEN)
        me = tg.get_me()
        assert me.get("is_bot") is True
        username = me.get("username", "?")
        t.ok(f"telegram_getMe (@{username})")
    except Exception as e:
        t.fail("telegram_getMe", str(e))
        me = None
        tg = None

    # Live: send test DM to allowed user
    if tg and me:
        try:
            mid = tg.send(
                ALLOWED_USER_ID,
                "🧪 <b>HeroSMS bot test</b>\n\n"
                f"Bot: @{esc(me.get('username', '?'))}\n"
                f"Your id: <code>{ALLOWED_USER_ID}</code>\n"
                "If you see this, Telegram delivery works.\n\n"
                "Reply /start to open the menu.",
                reply_markup=main_keyboard(),
            )
            assert mid is not None
            t.ok("telegram_send_test_message")
        except Exception as e:
            err = str(e).lower()
            if "chat not found" in err or "bot was blocked" in err or "user is deactivated" in err:
                t.skip(
                    "telegram_send_test_message",
                    "Open your bot in Telegram, press Start, re-run: python hero_sms_bot.py --test",
                )
            else:
                t.fail("telegram_send_test_message", str(e))

    # Live: HeroSMS API
    if has_hero_key:
        try:
            api = HeroSMS(HEROSMS_API_KEY)
            bal = api.get_balance()
            assert isinstance(bal, float)
            t.ok(f"herosms_getBalance ({fmt_money(bal)})")
        except Exception as e:
            t.fail("herosms_getBalance", str(e))

        try:
            countries = api.get_countries()
            assert len(countries) > 10
            t.ok(f"herosms_getCountries ({len(countries)} countries)")
        except Exception as e:
            t.fail("herosms_getCountries", str(e))

        try:
            active = api.get_active()
            assert isinstance(active, list)
            t.ok(f"herosms_getActiveActivations ({len(active)} active)")
        except Exception as e:
            t.fail("herosms_getActiveActivations", str(e))

        try:
            api.mail_probe()
            hist = api.mail_history(per_page=5)
            assert isinstance(hist, dict)
            if api.mail_api_enabled:
                t.ok(f"herosms_v1_emails ({hist.get('count', 0)} orders)")
                dom = api.mail_domains("instagram.com")
                assert isinstance(dom.get("domains"), list)
                t.ok(f"herosms_v1_emails_domains ({len(dom['domains'])} domains)")
            else:
                t.skip("herosms_v1_emails", "email API auth failed (SMS still works)")
        except Exception as e:
            t.fail("herosms_v1_emails", str(e))
    else:
        t.skip("herosms_getBalance", "set HEROSMS_API_KEY to test HeroSMS API")
        t.skip("herosms_getCountries", "set HEROSMS_API_KEY")
        t.skip("herosms_getActiveActivations", "set HEROSMS_API_KEY")
        t.skip("herosms_v1_emails", "set HEROSMS_API_KEY")

    return t.summary()


def main() -> None:
    Bot().run()


if __name__ == "__main__":
    if "--test" in sys.argv:
        raise SystemExit(run_tests())
    main()
