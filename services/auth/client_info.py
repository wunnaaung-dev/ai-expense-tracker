import ipaddress
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import Request


@dataclass
class ClientInfo:
    user_agent: Optional[str]
    ip: Optional[str]


def client_info(request: Request) -> ClientInfo:
    # Behind Render, uvicorn --proxy-headers puts the real client IP in request.client.
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )


# Order matters: Edge, Opera and Samsung Internet also say "Chrome", and Chrome also says "Safari".
_BROWSERS = [
    ("Edge", re.compile(r"Edg(e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/")),
    ("Safari", re.compile(r"Safari/")),
]

_SYSTEMS = [
    ("iPhone", re.compile(r"iPhone")),
    ("iPad", re.compile(r"iPad")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux")),
]


def describe_device(user_agent: Optional[str]) -> Tuple[str, str]:
    """("Chrome on Windows", "desktop") from a User-Agent; good enough for a device list."""
    ua = user_agent or ""
    browser = next((name for name, pattern in _BROWSERS if pattern.search(ua)), None)
    system = next((name for name, pattern in _SYSTEMS if pattern.search(ua)), None)
    kind = "phone" if system == "iPhone" or (system == "Android" and "Mobile" in ua) else "desktop"
    if browser and system:
        return f"{browser} on {system}", kind
    if browser or system:
        return browser or system, kind
    return "Unknown browser", kind


def mask_ip(ip: Optional[str]) -> Optional[str]:
    """203.81.64.12 → 203.81.x.x; 2001:db8:85a3::1 → 2001:db8:…"""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if address.version == 4:
        first, second, *_ = str(address).split(".")
        return f"{first}.{second}.x.x"
    groups = address.exploded.split(":")
    return f"{groups[0].lstrip('0') or '0'}:{groups[1].lstrip('0') or '0'}:…"
