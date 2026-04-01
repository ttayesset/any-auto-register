"""
注册流程引擎 V2
基于 curl_cffi 的注册状态机，注册成功后直接复用同一会话提取 ChatGPT Session。
"""

import time
import logging
from datetime import datetime
from typing import Optional, Callable, Any

from core.proxy_utils import normalize_proxy_url
from platforms.chatgpt.register import RegistrationResult

from .chatgpt_client import ChatGPTClient
from .oauth_client import OAuthClient
from .oauth import OAuthManager
from .utils import generate_random_name, generate_random_birthday

logger = logging.getLogger(__name__)

class EmailServiceAdapter:
    """\u5c06 V1 \u7684 email_service \u9002\u914d\u6210 V2 \u6240\u9700\u7684\u63a5\u7801\u63a5\u53e3\u3002"""
    def __init__(self, email_service, email, log_fn):
        self.es = email_service
        self.email = email
        self.log_fn = log_fn
        self._used_codes = set()

    def wait_for_verification_code(self, email, timeout=60, otp_sent_at=None, exclude_codes=None):
        msg = f"\u6b63\u5728\u7b49\u5f85\u90ae\u7bb1 {email} \u7684\u9a8c\u8bc1\u7801 ({timeout}s)..."
        self.log_fn(msg)
        code = self.es.get_verification_code(
            timeout=timeout,
            otp_sent_at=otp_sent_at,
            exclude_codes=exclude_codes or self._used_codes,
        )
        if code:
            self._used_codes.add(code)
            self.log_fn(f"\u6210\u529f\u83b7\u53d6\u9a8c\u8bc1\u7801: {code}")
        return code

class RegistrationEngineV2:
    def __init__(
        self,
        email_service,
        proxy_url: Optional[str] = None,
        browser_mode: str = "protocol",
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
        max_retries: int = 3,
        extra_config: Optional[dict] = None,
        email_service_factory: Optional[Callable[[Optional[str]], Any]] = None,
        proxy_resolver: Optional[Callable[[int, Optional[str]], Optional[str]]] = None,
    ):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.browser_mode = browser_mode or "protocol"
        self.callback_logger = callback_logger
        self.task_uuid = task_uuid
        self.max_retries = max(1, int(max_retries or 1))
        self.extra_config = dict(extra_config or {})
        self.email_service_factory = email_service_factory
        self.proxy_resolver = proxy_resolver
        self.last_proxy_used = normalize_proxy_url(proxy_url)
        
        self.email = None
        self.password = None
        self.logs = []
        
    def _log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        self.logs.append(log_message)
        if self.callback_logger:
            self.callback_logger(log_message)
        if level == "error":
            logger.error(log_message)
        else:
            logger.info(log_message)

    def _should_retry(self, message: str) -> bool:
        text = str(message or "").lower()
        retriable_markers = [
            "tls",
            "ssl",
            "curl: (35)",
            "预授权被拦截",
            "authorize",
            "registration_disallowed",
            "http 400",
            "创建账号失败",
            "未获取到 authorization code",
            "consent",
            "workspace",
            "organization",
            "otp",
            "验证码",
            "session",
            "accessToken",
            "next-auth",
            "refresh_token",
            "oauth token",
        ]
        return any(marker.lower() in text for marker in retriable_markers)

    def _fetch_oauth_tokens(
        self,
        *,
        chatgpt_client: ChatGPTClient,
        email: str,
        password: str,
        skymail_adapter: EmailServiceAdapter,
        proxy_url: Optional[str],
        max_attempts: int = 2,
    ) -> tuple[Optional[dict[str, Any]], str]:
        last_error = ""

        for attempt in range(max_attempts):
            if attempt > 0:
                self._log(f"OAuth token exchange 重试 {attempt + 1}/{max_attempts} ...")
                time.sleep(1)

            try:
                oauth_client = OAuthClient(
                    config={},
                    proxy=proxy_url,
                    verbose=False,
                    browser_mode=self.browser_mode,
                )
                oauth_client._log = lambda msg: self._log(f"[OAuth] {msg}")
                oauth_client.session.headers.update(
                    {
                        "Accept-Language": chatgpt_client.accept_language,
                    }
                )

                tokens = oauth_client.login_and_get_tokens(
                    email=email,
                    password=password,
                    device_id=chatgpt_client.device_id,
                    user_agent=chatgpt_client.ua,
                    sec_ch_ua=chatgpt_client.sec_ch_ua,
                    impersonate=chatgpt_client.impersonate,
                    skymail_client=skymail_adapter,
                )
                refresh_token = str((tokens or {}).get("refresh_token") or "").strip()
                if tokens and refresh_token:
                    return tokens, ""

                last_error = "OAuth token exchange 未获取到 refresh_token"
            except Exception as e:
                last_error = f"OAuth token exchange 异常: {e}"

        return None, last_error or "OAuth token exchange 失败"

    def _prepare_attempt_context(self, attempt: int):
        current_proxy = normalize_proxy_url(self.proxy_url)
        if attempt > 0 and callable(self.proxy_resolver):
            try:
                current_proxy = normalize_proxy_url(
                    self.proxy_resolver(attempt, current_proxy)
                ) or current_proxy
            except Exception as exc:
                self._log(f"重试前重新获取代理失败，继续使用当前代理: {exc}")

        self.proxy_url = current_proxy
        self.last_proxy_used = current_proxy

        email_service = self.email_service
        if callable(self.email_service_factory):
            email_service = self.email_service_factory(current_proxy)

        return current_proxy, email_service

    def run(self) -> RegistrationResult:
        result = RegistrationResult(success=False, logs=self.logs)
        try:
            last_error = ""
            for attempt in range(self.max_retries):
                try:
                    if attempt == 0:
                        self._log("=" * 60)
                        self._log("开始注册流程 V2 (Session 复用 + OAuth token exchange)")
                        self._log(f"请求模式: {self.browser_mode}")
                        self._log("=" * 60)
                    else:
                        self._log(f"整流程重试 {attempt + 1}/{self.max_retries} ...")
                        time.sleep(1)

                    current_proxy, email_service = self._prepare_attempt_context(attempt)
                    if current_proxy:
                        self._log(f"当前轮代理: {current_proxy}")

                    # 1. 创建邮箱
                    email_data = email_service.create_email()
                    email_addr = self.email or (email_data.get('email') if email_data else None)
                    if not email_addr:
                        result.error_message = "创建邮箱失败"
                        return result

                    result.email = email_addr

                    pwd = self.password or "AAb1234567890!"
                    result.password = pwd

                    # 随机姓名、生日
                    first_name, last_name = generate_random_name()
                    birthdate = generate_random_birthday()

                    self._log(f"邮箱: {email_addr}, 密码: {pwd}")
                    self._log(f"注册信息: {first_name} {last_name}, 生日: {birthdate}")

                    # 使用包装器为底层客户端提供接码服务
                    skymail_adapter = EmailServiceAdapter(email_service, email_addr, self._log)

                    # 2. 初始化 V2 客户端
                    chatgpt_client = ChatGPTClient(
                        proxy=current_proxy,
                        verbose=False,
                        browser_mode=self.browser_mode,
                    )
                    chatgpt_client._log = self._log

                    self._log("步骤 1/3: 执行注册状态机...")

                    success, msg = chatgpt_client.register_complete_flow(
                        email_addr, pwd, first_name, last_name, birthdate, skymail_adapter
                    )

                    if not success:
                        last_error = f"注册流失败: {msg}"
                        if attempt < self.max_retries - 1 and self._should_retry(msg):
                            self._log(f"注册流失败，准备整流程重试: {msg}")
                            continue
                        result.error_message = last_error
                        return result

                    self._log("步骤 2/3: 复用注册会话，获取 ChatGPT Session / AccessToken...")
                    session_ok, session_result = chatgpt_client.reuse_session_and_get_tokens()
                    if not session_ok:
                        last_error = f"注册成功，但复用会话获取 AccessToken 失败: {session_result}"
                        if attempt < self.max_retries - 1:
                            self._log(f"{last_error}，准备整流程重试")
                            continue
                        result.error_message = last_error
                        return result

                    self._log("步骤 3/3: 执行 OAuth token exchange，获取 Refresh Token...")
                    oauth_tokens, oauth_error = self._fetch_oauth_tokens(
                        chatgpt_client=chatgpt_client,
                        email=email_addr,
                        password=pwd,
                        skymail_adapter=skymail_adapter,
                        proxy_url=current_proxy,
                    )

                    if not oauth_tokens:
                        last_error = oauth_error or "OAuth token exchange 失败"
                        if attempt < self.max_retries - 1 and self._should_retry(last_error):
                            self._log(f"{last_error}，准备整流程重试")
                            continue
                        result.error_message = last_error
                        return result

                    self._log("Token 提取完成！")
                    result.success = True
                    result.access_token = (
                        str(oauth_tokens.get("access_token") or "").strip()
                        or session_result.get("access_token", "")
                    )
                    result.refresh_token = str(oauth_tokens.get("refresh_token") or "").strip()
                    result.id_token = str(oauth_tokens.get("id_token") or "").strip()
                    result.session_token = session_result.get("session_token", "")

                    oauth_account_info = {}
                    if result.id_token:
                        oauth_account_info = OAuthManager(
                            proxy_url=current_proxy
                        ).extract_account_info(result.id_token)

                    result.account_id = (
                        session_result.get("account_id")
                        or oauth_account_info.get("account_id")
                        or session_result.get("user_id")
                        or ("v2_acct_" + chatgpt_client.device_id[:8])
                    )
                    result.workspace_id = session_result.get("workspace_id", "")
                    result.metadata = {
                        "auth_provider": session_result.get("auth_provider", ""),
                        "expires": session_result.get("expires", ""),
                        "user_id": session_result.get("user_id", ""),
                        "user": session_result.get("user") or {},
                        "account": session_result.get("account") or {},
                        "oauth_expires_in": oauth_tokens.get("expires_in", 0),
                        "oauth_token_type": oauth_tokens.get("token_type", ""),
                        "proxy_used": current_proxy or "",
                    }

                    if result.workspace_id:
                        self._log(f"Session Workspace ID: {result.workspace_id}")

                    self._log("=" * 60)
                    self._log("注册流程成功结束!")
                    self._log("=" * 60)
                    return result
                except Exception as attempt_error:
                    last_error = str(attempt_error)
                    if attempt < self.max_retries - 1 and self._should_retry(last_error):
                        self._log(f"本轮出现异常，准备整流程重试: {last_error}")
                        continue
                    raise

            result.error_message = last_error or "注册失败"
            return result
                
        except Exception as e:
            self._log(f"V2 注册全流程执行异常: {e}", "error")
            import traceback
            traceback.print_exc()
            result.error_message = str(e)
            return result
