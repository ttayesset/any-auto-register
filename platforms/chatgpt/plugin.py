"""ChatGPT / Codex CLI 平台插件"""

import random
import string

from core.base_mailbox import BaseMailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.registry import register


@register
class ChatGPTPlatform(BasePlatform):
    name = "chatgpt"
    display_name = "ChatGPT"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def check_valid(self, account: Account) -> bool:
        try:
            from platforms.chatgpt.payment import check_subscription_status

            class _A:
                pass

            a = _A()
            extra = account.extra or {}
            a.access_token = extra.get("access_token") or account.token
            a.cookies = extra.get("cookies", "")
            status = check_subscription_status(a, proxy=self.config.proxy if self.config else None)
            return status not in ("expired", "invalid", "banned", None)
        except Exception:
            return False

    def register(self, email: str = None, password: str = None) -> Account:
        if not password:
            password = "".join(random.choices(string.ascii_letters + string.digits + "!@#$", k=16))

        proxy = self.config.proxy if self.config else None
        browser_mode = (self.config.executor_type if self.config else None) or "protocol"
        log_fn = getattr(self, "_log_fn", print)
        from platforms.chatgpt.register_v2 import RegistrationEngineV2 as RegistrationEngine

        max_retries = 3
        config_extra = dict((self.config.extra or {}) if self.config and getattr(self.config, "extra", None) else {})
        if self.config and getattr(self.config, "extra", None):
            try:
                max_retries = int(config_extra.get("register_max_retries", 3) or 3)
            except Exception:
                max_retries = 3
        rotate_proxy_on_retry = bool(config_extra.get("retry_rotate_proxy"))
        self._last_proxy_used = proxy
        self._last_mailbox = self.mailbox

        def _build_generic_email_service(mailbox_obj, fixed_email: str = None):
            self._last_mailbox = mailbox_obj
            mailbox_obj._log_fn = log_fn

            class GenericEmailService:
                service_type = type("ST", (), {"value": "custom_provider"})()

                def __init__(self):
                    self._acct = None
                    self._email = fixed_email

                def create_email(self, config=None):
                    if self._email and self._acct and fixed_email:
                        return {"email": self._email, "service_id": self._acct.account_id, "token": ""}
                    self._acct = mailbox_obj.get_email()
                    if not self._email:
                        self._email = self._acct.email
                    elif not fixed_email:
                        self._email = self._acct.email
                    return {"email": self._email, "service_id": self._acct.account_id, "token": ""}

                def get_verification_code(
                    self,
                    email=None,
                    email_id=None,
                    timeout=120,
                    pattern=None,
                    otp_sent_at=None,
                    exclude_codes=None,
                ):
                    if not self._acct:
                        raise RuntimeError("邮箱账户尚未创建，无法获取验证码")
                    return mailbox_obj.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )

                def update_status(self, success, error=None):
                    pass

                @property
                def status(self):
                    return None

            return GenericEmailService()

        def _build_tempmail_email_service(proxy_for_attempt: str = None):
            from core.base_mailbox import TempMailLolMailbox

            tmail = TempMailLolMailbox(proxy=proxy_for_attempt)
            self._last_mailbox = tmail
            tmail._log_fn = log_fn

            class TempMailEmailService:
                service_type = type("ST", (), {"value": "tempmail_lol"})()

                def create_email(self, config=None):
                    acct = tmail.get_email()
                    self._acct = acct
                    return {"email": acct.email, "service_id": acct.account_id, "token": acct.account_id}

                def get_verification_code(
                    self,
                    email=None,
                    email_id=None,
                    timeout=120,
                    pattern=None,
                    otp_sent_at=None,
                    exclude_codes=None,
                ):
                    return tmail.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )

                def update_status(self, success, error=None):
                    pass

                @property
                def status(self):
                    return None

            return TempMailEmailService()

        def _resolve_retry_proxy(attempt: int, current_proxy: str = None) -> str:
            if not rotate_proxy_on_retry:
                return current_proxy
            from core.proxy_pool import proxy_pool
            from core.proxy_utils import normalize_proxy_url

            next_proxy = normalize_proxy_url(proxy_pool.get_next())
            if next_proxy:
                log_fn(f"重试前重新获取代理: {next_proxy}")
                self._last_proxy_used = next_proxy
            return next_proxy or current_proxy

        if self.mailbox:
            if rotate_proxy_on_retry:
                from core.base_mailbox import create_mailbox

                provider = config_extra.get("mail_provider", "laoudo")

                def _build_rotating_generic_email_service(proxy_for_attempt: str = None):
                    mailbox_obj = create_mailbox(
                        provider=provider,
                        extra=config_extra,
                        proxy=proxy_for_attempt,
                    )
                    return _build_generic_email_service(mailbox_obj, email)

                email_service = _build_rotating_generic_email_service(proxy)
                email_service_factory = _build_rotating_generic_email_service
            else:
                email_service = _build_generic_email_service(self.mailbox, email)
                email_service_factory = None

            engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                extra_config=config_extra,
                email_service_factory=email_service_factory,
                proxy_resolver=_resolve_retry_proxy if rotate_proxy_on_retry else None,
            )
            engine.email = email
            engine.password = password
        else:
            email_service = _build_tempmail_email_service(proxy)

            engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                extra_config=config_extra,
                email_service_factory=_build_tempmail_email_service if rotate_proxy_on_retry else None,
                proxy_resolver=_resolve_retry_proxy if rotate_proxy_on_retry else None,
            )
            if email:
                engine.email = email
                engine.password = password

        result = engine.run()
        self._last_proxy_used = (
            ((result.metadata or {}).get("proxy_used") if result else "")
            or getattr(engine, "last_proxy_used", None)
            or proxy
        )
        if not result or not result.success:
            raise RuntimeError(result.error_message if result else "注册失败")

        return Account(
            platform="chatgpt",
            email=result.email,
            password=result.password or password,
            user_id=result.account_id,
            token=result.access_token,
            status=AccountStatus.REGISTERED,
            extra={
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "id_token": result.id_token,
                "session_token": result.session_token,
                "workspace_id": result.workspace_id,
                "proxy_used": self._last_proxy_used or "",
            },
        )

    def get_platform_actions(self) -> list:
        return [
            {"id": "refresh_token", "label": "刷新 Token", "params": []},
            {
                "id": "payment_link",
                "label": "生成支付链接",
                "params": [
                    {"key": "country", "label": "地区", "type": "select", "options": ["US", "SG", "TR", "HK", "JP", "GB", "AU", "CA"]},
                    {"key": "plan", "label": "套餐", "type": "select", "options": ["plus", "team"]},
                ],
            },
            {
                "id": "upload_cpa",
                "label": "上传 CPA",
                "params": [
                    {"key": "api_url", "label": "CPA API URL", "type": "text"},
                    {"key": "api_key", "label": "CPA API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_sub2api",
                "label": "上传 Sub2API",
                "params": [
                    {"key": "api_url", "label": "Sub2API API URL", "type": "text"},
                    {"key": "api_key", "label": "Sub2API API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_tm",
                "label": "上传 Team Manager",
                "params": [
                    {"key": "api_url", "label": "TM API URL", "type": "text"},
                    {"key": "api_key", "label": "TM API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_codex_proxy",
                "label": "上传 CodexProxy",
                "params": [
                    {"key": "api_url", "label": "API URL", "type": "text"},
                    {"key": "api_key", "label": "Admin Key", "type": "text"},
                ],
            },
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        proxy = self.config.proxy if self.config else None
        extra = account.extra or {}

        class _A:
            pass

        a = _A()
        a.email = account.email
        a.access_token = extra.get("access_token") or account.token
        a.refresh_token = extra.get("refresh_token", "")
        a.id_token = extra.get("id_token", "")
        a.session_token = extra.get("session_token", "")
        a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
        a.cookies = extra.get("cookies", "")

        if action_id == "refresh_token":
            from platforms.chatgpt.token_refresh import TokenRefreshManager

            manager = TokenRefreshManager(proxy_url=proxy)
            result = manager.refresh_account(a)
            if result.success:
                return {
                    "ok": True,
                    "data": {
                        "access_token": result.access_token,
                        "refresh_token": result.refresh_token,
                    },
                }
            return {"ok": False, "error": result.error_message}

        if action_id == "payment_link":
            from platforms.chatgpt.payment import generate_plus_link, generate_team_link

            plan = params.get("plan", "plus")
            country = params.get("country", "US")
            if plan == "plus":
                url = generate_plus_link(a, proxy=proxy, country=country)
            else:
                url = generate_team_link(
                    a,
                    workspace_name=params.get("workspace_name", "MyTeam"),
                    price_interval=params.get("price_interval", "month"),
                    seat_quantity=int(params.get("seat_quantity", 5) or 5),
                    proxy=proxy,
                    country=country,
                )
            return {"ok": bool(url), "data": {"url": url}}

        if action_id == "upload_cpa":
            from platforms.chatgpt.cpa_upload import generate_token_json, upload_to_cpa

            token_data = generate_token_json(a)
            ok, msg = upload_to_cpa(
                token_data,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_sub2api":
            from platforms.chatgpt.sub2api_upload import upload_to_sub2api

            ok, msg = upload_to_sub2api(
                a,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_tm":
            from platforms.chatgpt.cpa_upload import upload_to_team_manager

            ok, msg = upload_to_team_manager(
                a,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_codex_proxy":
            upload_type = str(
                params.get("upload_type")
                or (self.config.extra or {}).get("codex_proxy_upload_type")
                or "at"
            ).strip().lower()

            if upload_type == "rt":
                from platforms.chatgpt.cpa_upload import upload_to_codex_proxy

                ok, msg = upload_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            else:
                from platforms.chatgpt.cpa_upload import upload_at_to_codex_proxy

                ok, msg = upload_at_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            return {"ok": ok, "data": msg}

        raise NotImplementedError(f"未知操作: {action_id}")
