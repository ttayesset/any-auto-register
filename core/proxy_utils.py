from __future__ import annotations

from typing import Optional
from urllib.parse import unquote, urlsplit, urlunsplit


def normalize_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    """补齐代理协议并将 socks5:// 规范化为 socks5h://。"""
    if proxy_url is None:
        return None

    value = str(proxy_url).strip()
    if not value:
        return None

    if "://" not in value:
        value = f"http://{value}"

    parts = urlsplit(value)
    if (parts.scheme or "").lower() == "socks5":
        parts = parts._replace(scheme="socks5h")
        return urlunsplit(parts)
    return value


def build_requests_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    value = normalize_proxy_url(proxy_url)
    if not value:
        return None
    return {"http": value, "https": value}


def build_playwright_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    value = normalize_proxy_url(proxy_url)
    if not value:
        return None

    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname or parts.port is None:
        return {"server": value}

    config = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        config["username"] = unquote(parts.username)
    if parts.password:
        config["password"] = unquote(parts.password)
    return config
