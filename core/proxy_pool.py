"""代理池 - 从数据库读取代理，支持轮询和按区域选取"""

import threading
from typing import Optional
from urllib.parse import quote

import requests
from sqlmodel import Session, select

from .config_store import config_store
from .db import ProxyModel, engine
from .proxy_utils import build_requests_proxy_config, normalize_proxy_url
from datetime import datetime, timezone


class ProxyPool:
    def __init__(self):
        self._index = 0
        self._lock = threading.Lock()

    def _find_proxy_by_url(self, session: Session, url: str) -> Optional[ProxyModel]:
        raw_url = str(url or "").strip()
        normalized_url = normalize_proxy_url(raw_url)
        for candidate in (normalized_url, raw_url):
            if not candidate:
                continue
            proxy = session.exec(select(ProxyModel).where(ProxyModel.url == candidate)).first()
            if proxy:
                return proxy
        return None

    def _pick_db_proxy(self, region: str = "") -> Optional[str]:
        with Session(engine) as s:
            q = select(ProxyModel).where(ProxyModel.is_active == True)
            if region:
                q = q.where(ProxyModel.region == region)
            proxies = s.exec(q).all()
            if not proxies:
                return None
            proxies.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True,
            )
            with self._lock:
                idx = self._index % len(proxies)
                self._index += 1
            return normalize_proxy_url(proxies[idx].url)

    def _resolve_external_api_url(self, region: str = "", api_url: Optional[str] = None) -> str:
        value = str(api_url or config_store.get("proxy_api_url", "") or "").strip()
        if not value:
            return ""
        if "{region}" in value:
            return value.replace("{region}", quote(region or ""))
        return value

    def _extract_external_proxy(self, response: requests.Response) -> Optional[str]:
        raw_value: Optional[str] = None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, str):
            raw_value = payload
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, str):
                raw_value = first
        elif isinstance(payload, dict):
            for key in ("proxy", "data", "result", "value"):
                current = payload.get(key)
                if isinstance(current, str) and current.strip():
                    raw_value = current
                    break
                if isinstance(current, list) and current and isinstance(current[0], str):
                    raw_value = current[0]
                    break

        if raw_value is None:
            raw_value = response.text

        lines = [line.strip() for line in str(raw_value or "").splitlines() if line.strip()]
        if not lines:
            return None
        return normalize_proxy_url(lines[0])

    def fetch_external_proxy(self, region: str = "", api_url: Optional[str] = None) -> Optional[str]:
        resolved_url = self._resolve_external_api_url(region=region, api_url=api_url)
        if not resolved_url:
            return None

        response = requests.get(resolved_url, timeout=10)
        response.raise_for_status()
        proxy_url = self._extract_external_proxy(response)
        if not proxy_url:
            raise ValueError("外部代理接口未返回可用代理")
        return proxy_url

    def get_next(self, region: str = "") -> Optional[str]:
        """优先从数据库代理池取代理，取不到时回退到外部代理接口。"""
        db_proxy = self._pick_db_proxy(region=region)
        if db_proxy:
            return db_proxy
        return self.fetch_external_proxy(region=region)

    def report_success(self, url: str) -> None:
        normalized_url = normalize_proxy_url(url)
        if not normalized_url:
            return
        with Session(engine) as s:
            p = self._find_proxy_by_url(s, normalized_url)
            if p:
                p.success_count += 1
                p.last_checked = datetime.now(timezone.utc)
                s.add(p)
                s.commit()

    def report_fail(self, url: str) -> None:
        normalized_url = normalize_proxy_url(url)
        if not normalized_url:
            return
        with Session(engine) as s:
            p = self._find_proxy_by_url(s, normalized_url)
            if p:
                p.fail_count += 1
                p.last_checked = datetime.now(timezone.utc)
                # 连续失败超过10次自动禁用
                if p.fail_count > 0 and p.success_count == 0 and p.fail_count >= 5:
                    p.is_active = False
                s.add(p)
                s.commit()

    def check_all(self) -> dict:
        """检测所有代理可用性"""
        with Session(engine) as s:
            proxies = s.exec(select(ProxyModel)).all()
        results = {"ok": 0, "fail": 0}
        for p in proxies:
            try:
                r = requests.get(
                    "https://httpbin.org/ip",
                    proxies=build_requests_proxy_config(p.url),
                    timeout=8,
                )
                if r.status_code == 200:
                    self.report_success(p.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_fail(p.url)
            results["fail"] += 1
        return results


proxy_pool = ProxyPool()
