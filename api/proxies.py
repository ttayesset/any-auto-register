from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
from core.config_store import config_store
from core.db import ProxyModel, get_session
from core.proxy_pool import proxy_pool
from core.proxy_utils import normalize_proxy_url

router = APIRouter(prefix="/proxies", tags=["proxies"])


class ProxyCreate(BaseModel):
    url: str
    region: str = ""


class ProxyBulkCreate(BaseModel):
    proxies: list[str]
    region: str = ""


class ExternalProxyFetchRequest(BaseModel):
    api_url: Optional[str] = None
    region: str = ""


def _find_existing_proxy(session: Session, url: str) -> Optional[ProxyModel]:
    raw_url = str(url or "").strip()
    normalized_url = normalize_proxy_url(raw_url)
    for candidate in (normalized_url, raw_url):
        if not candidate:
            continue
        existing = session.exec(select(ProxyModel).where(ProxyModel.url == candidate)).first()
        if existing:
            return existing
    return None


@router.get("")
def list_proxies(session: Session = Depends(get_session)):
    items = session.exec(select(ProxyModel)).all()
    return items


@router.post("")
def add_proxy(body: ProxyCreate, session: Session = Depends(get_session)):
    normalized_url = normalize_proxy_url(body.url)
    if not normalized_url:
        raise HTTPException(400, "代理地址不能为空")
    existing = _find_existing_proxy(session, body.url)
    if existing:
        raise HTTPException(400, "代理已存在")
    p = ProxyModel(url=normalized_url, region=body.region)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.post("/bulk")
def bulk_add_proxies(body: ProxyBulkCreate, session: Session = Depends(get_session)):
    added = 0
    for url in body.proxies:
        normalized_url = normalize_proxy_url(url)
        if not normalized_url:
            continue
        existing = _find_existing_proxy(session, url)
        if not existing:
            session.add(ProxyModel(url=normalized_url, region=body.region))
            added += 1
    session.commit()
    return {"added": added}


@router.post("/check")
def check_proxies(background_tasks: BackgroundTasks):
    background_tasks.add_task(proxy_pool.check_all)
    return {"message": "检测任务已启动"}


def _fetch_external_proxy(api_url: Optional[str] = None, region: str = ""):
    try:
        proxy = proxy_pool.fetch_external_proxy(region=region, api_url=api_url)
    except Exception as exc:
        raise HTTPException(400, f"获取外部代理失败: {exc}") from exc
    if not proxy:
        configured_url = str(api_url or config_store.get("proxy_api_url", "") or "").strip()
        if not configured_url:
            raise HTTPException(400, "请先配置外部代理 API 地址")
        raise HTTPException(400, "外部代理接口未返回可用代理")
    return {"proxy": proxy}


@router.get("/fetch-external")
def fetch_external_proxy_get(api_url: Optional[str] = None, region: str = ""):
    return _fetch_external_proxy(api_url=api_url, region=region)


@router.post("/fetch-external")
def fetch_external_proxy_post(body: ExternalProxyFetchRequest):
    return _fetch_external_proxy(api_url=body.api_url, region=body.region)


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    session.delete(p)
    session.commit()
    return {"ok": True}


@router.patch("/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    p.is_active = not p.is_active
    session.add(p)
    session.commit()
    return {"is_active": p.is_active}
