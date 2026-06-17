from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "data" / "platform.db"
APP_SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
SESSION_COOKIE = "llm_session"
SESSION_SECONDS = 7 * 24 * 60 * 60


PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-3-5-sonnet", "claude-3-haiku"],
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max"],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4", "glm-4-flash"],
    },
}


MODEL_TO_PROVIDER = {
    model: provider_id
    for provider_id, provider in PROVIDERS.items()
    for model in provider["models"]
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            create table if not exists users (
                id text primary key,
                name text not null,
                password_hash text not null default '',
                quota_tokens integer not null default 200000,
                created_at integer not null
            );

            create table if not exists sessions (
                id text primary key,
                user_id text not null,
                expires_at integer not null,
                created_at integer not null
            );

            create table if not exists provider_keys (
                user_id text not null,
                provider_id text not null,
                base_url text not null,
                api_key_cipher text not null,
                enabled integer not null default 1,
                updated_at integer not null,
                primary key (user_id, provider_id)
            );

            create table if not exists prompt_templates (
                id text primary key,
                user_id text not null,
                title text not null,
                content text not null,
                updated_at integer not null
            );

            create table if not exists conversations (
                id text primary key,
                user_id text not null,
                title text not null,
                model text not null,
                provider_id text not null,
                created_at integer not null,
                updated_at integer not null
            );

            create table if not exists messages (
                id text primary key,
                conversation_id text not null,
                role text not null,
                content text not null,
                created_at integer not null
            );

            create table if not exists usage_events (
                id text primary key,
                user_id text not null,
                provider_id text not null,
                model text not null,
                prompt_tokens integer not null,
                completion_tokens integer not null,
                total_tokens integer not null,
                cost_cents real not null,
                created_at integer not null
            );
            """
        )
        ensure_auth_schema(conn)
        demo_user = conn.execute("select * from users where id = ?", ("demo",)).fetchone()
        if not demo_user:
            conn.execute(
                """
                insert into users (id, name, password_hash, quota_tokens, created_at)
                values (?, ?, ?, ?, ?)
                """,
                ("demo", "Demo 用户", hash_password("demo123"), 200000, now()),
            )
        elif not demo_user["password_hash"]:
            conn.execute("update users set password_hash = ? where id = ?", (hash_password("demo123"), "demo"))
        conn.execute("delete from sessions where expires_at <= ?", (now(),))
        if not conn.execute("select 1 from prompt_templates where user_id = ?", ("demo",)).fetchone():
            seed_prompts = [
                (
                    "架构助手",
                    "你是资深架构师。回答时先给结论，再列关键风险与落地步骤。",
                ),
                (
                    "客服助手",
                    "你是耐心的客服助手。语气友好，回答简洁，必要时向用户确认信息。",
                ),
                (
                    "代码审查",
                    "你是严格的代码审查员。优先指出 bug、风险、缺失测试和可维护性问题。",
                ),
            ]
            for title, content in seed_prompts:
                conn.execute(
                    """
                    insert into prompt_templates (id, user_id, title, content, updated_at)
                    values (?, ?, ?, ?, ?)
                    """,
                    (new_id("tpl"), "demo", title, content, now()),
                )


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def ensure_auth_schema(conn: sqlite3.Connection) -> None:
    user_columns = {row["name"] for row in conn.execute("pragma table_info(users)").fetchall()}
    if "password_hash" not in user_columns:
        conn.execute("alter table users add column password_hash text not null default ''")
    conn.execute(
        """
        create table if not exists sessions (
            id text primary key,
            user_id text not null,
            expires_at integer not null,
            created_at integer not null
        )
        """
    )


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 120000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def create_session(user_id: str) -> str:
    session_id = secrets.token_urlsafe(32)
    current = now()
    with connect() as conn:
        conn.execute("delete from sessions where expires_at <= ?", (current,))
        conn.execute(
            "insert into sessions (id, user_id, expires_at, created_at) values (?, ?, ?, ?)",
            (session_id, user_id, current + SESSION_SECONDS, current),
        )
    return session_id


def get_session_user(session_id: str) -> str | None:
    if not session_id:
        return None
    with connect() as conn:
        row = conn.execute("select user_id, expires_at from sessions where id = ?", (session_id,)).fetchone()
        if not row:
            return None
        if int(row["expires_at"]) <= now():
            conn.execute("delete from sessions where id = ?", (session_id,))
            return None
        return str(row["user_id"])


def destroy_session(session_id: str) -> None:
    if session_id:
        with connect() as conn:
            conn.execute("delete from sessions where id = ?", (session_id,))


def login_user(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = str(payload.get("username") or payload.get("user_id") or "").strip()
    password = str(payload.get("password") or "")
    if not user_id or not password:
        raise ValueError("用户名和密码不能为空")
    with connect() as conn:
        user = conn.execute("select * from users where id = ?", (user_id,)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        raise ValueError("用户名或密码错误")
    session_id = create_session(user_id)
    return {
        "ok": True,
        "session_id": session_id,
        "user": {"id": user["id"], "name": user["name"]},
    }


def estimate_tokens(text: str) -> int:
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def encrypt_secret(value: str) -> str:
    key = hashlib.sha256(APP_SECRET.encode("utf-8")).digest()
    data = value.encode("utf-8")
    cipher = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return base64.urlsafe_b64encode(cipher).decode("ascii")


def decrypt_secret(value: str) -> str:
    key = hashlib.sha256(APP_SECRET.encode("utf-8")).digest()
    data = base64.urlsafe_b64decode(value.encode("ascii"))
    plain = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return plain.decode("utf-8")


def mask_key(cipher: str) -> str:
    try:
        key = decrypt_secret(cipher)
    except Exception:
        return "******"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        current = time.time()
        with self._lock:
            hits = [hit for hit in self._hits.get(key, []) if current - hit < self.window_seconds]
            if len(hits) >= self.max_requests:
                self._hits[key] = hits
                return False
            hits.append(current)
            self._hits[key] = hits
            return True


limiter = RateLimiter()


class PlatformHandler(SimpleHTTPRequestHandler):
    server_version = "LLMAggregationPlatform/0.1"

    def translate_path(self, path: str) -> str:
        clean_path = urlparse(path).path
        if clean_path == "/login":
            return str(STATIC_DIR / "login.html")
        if clean_path == "/":
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / clean_path.lstrip("/"))

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-User-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/"):
            self.dispatch_get(route)
            return
        if route == "/login":
            if self.session_user_id():
                self.redirect("/")
                return
            super().do_GET()
            return
        if route in {"/", "/index.html"} and not self.session_user_id():
            self.redirect("/login?next=/")
            return
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/") or route == "/v1/chat/completions":
            self.dispatch_post(route)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/"):
            self.dispatch_delete(route)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def dispatch_get(self, route: str) -> None:
        if route == "/api/session":
            user_id = self.session_user_id()
            if not user_id:
                self.json_response({"authenticated": False}, HTTPStatus.UNAUTHORIZED)
                return
            self.json_response({"authenticated": True, "user": self.user_payload(user_id)})
            return

        user_id = self.require_user_id()
        if not user_id:
            return
        if route == "/api/bootstrap":
            self.json_response(get_bootstrap(user_id))
        elif route == "/api/conversations":
            self.json_response({"items": get_conversations(user_id)})
        elif route.startswith("/api/conversations/"):
            conversation_id = route.rsplit("/", 1)[-1]
            self.json_response(get_conversation(user_id, conversation_id))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def dispatch_post(self, route: str) -> None:
        try:
            payload = self.read_json()
            if route == "/api/login":
                if not limiter.allow(f"login:{self.client_address[0]}"):
                    self.json_response({"error": "登录过快，请稍后再试"}, HTTPStatus.TOO_MANY_REQUESTS)
                    return
                result = login_user(payload)
                self.json_response(
                    {"ok": True, "user": result["user"]},
                    headers={"Set-Cookie": self.session_cookie(result["session_id"])},
                )
                return
            if route == "/api/logout":
                destroy_session(self.cookie_value(SESSION_COOKIE))
                self.json_response({"ok": True}, headers={"Set-Cookie": self.expired_session_cookie()})
                return

            user_id = self.require_user_id(allow_header=route == "/v1/chat/completions")
            if not user_id:
                return
            if not limiter.allow(user_id):
                self.json_response({"error": "请求过快，请稍后再试"}, HTTPStatus.TOO_MANY_REQUESTS)
                return

            if route == "/api/provider-keys":
                self.json_response(save_provider_key(user_id, payload))
            elif route == "/api/prompt-templates":
                self.json_response(save_prompt_template(user_id, payload))
            elif route == "/api/chat":
                self.stream_chat(user_id, payload, openai_compatible=False)
            elif route == "/v1/chat/completions":
                stream = bool(payload.get("stream"))
                if stream:
                    self.stream_chat(user_id, payload, openai_compatible=True)
                else:
                    response = complete_chat(user_id, payload)
                    self.json_response(to_openai_response(response, payload.get("model", "gpt-4o")))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.json_response({"error": f"服务异常：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def dispatch_delete(self, route: str) -> None:
        user_id = self.require_user_id()
        if not user_id:
            return
        if route.startswith("/api/provider-keys/"):
            provider_id = route.rsplit("/", 1)[-1]
            delete_provider_key(user_id, provider_id)
            self.json_response({"ok": True})
        elif route.startswith("/api/prompt-templates/"):
            template_id = route.rsplit("/", 1)[-1]
            delete_prompt_template(user_id, template_id)
            self.json_response({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def cookie_value(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return ""
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        if name not in cookie:
            return ""
        return cookie[name].value

    def session_user_id(self) -> str | None:
        return get_session_user(self.cookie_value(SESSION_COOKIE))

    def require_user_id(self, allow_header: bool = False) -> str | None:
        user_id = self.session_user_id()
        if user_id:
            return user_id
        if allow_header:
            header_user = self.headers.get("X-User-ID", "").strip()
            if header_user:
                return header_user
        self.json_response({"error": "请先登录"}, HTTPStatus.UNAUTHORIZED)
        return None

    def user_payload(self, user_id: str) -> dict[str, str]:
        with connect() as conn:
            user = conn.execute("select id, name from users where id = ?", (user_id,)).fetchone()
        if not user:
            return {"id": user_id, "name": user_id}
        return {"id": user["id"], "name": user["name"]}

    def session_cookie(self, session_id: str) -> str:
        return f"{SESSION_COOKIE}={session_id}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Lax"

    def expired_session_cookie(self) -> str:
        return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 格式错误") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def json_response(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def stream_chat(self, user_id: str, payload: dict[str, Any], openai_compatible: bool) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            response = complete_chat(user_id, payload)
            content = response["content"]
            for chunk in chunk_text(content):
                if openai_compatible:
                    event = {
                        "id": new_id("chatcmpl"),
                        "object": "chat.completion.chunk",
                        "created": now(),
                        "model": response["model"],
                        "choices": [{"delta": {"content": chunk}, "index": 0, "finish_reason": None}],
                    }
                else:
                    event = {"type": "delta", "content": chunk}
                self.write_sse(event)
                time.sleep(0.025)

            if openai_compatible:
                self.write_sse(
                    {
                        "id": new_id("chatcmpl"),
                        "object": "chat.completion.chunk",
                        "created": now(),
                        "model": response["model"],
                        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                    }
                )
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.write_sse({"type": "done", "usage": response["usage"], "conversation_id": response["conversation_id"]})
        except Exception as exc:
            self.write_sse({"type": "error", "message": str(exc)})

    def write_sse(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def get_bootstrap(user_id: str) -> dict[str, Any]:
    ensure_user(user_id)
    with connect() as conn:
        keys = {
            row["provider_id"]: row
            for row in conn.execute("select * from provider_keys where user_id = ?", (user_id,)).fetchall()
        }
        providers = []
        for provider_id, provider in PROVIDERS.items():
            key_row = keys.get(provider_id)
            providers.append(
                {
                    "id": provider_id,
                    "name": provider["name"],
                    "base_url": key_row["base_url"] if key_row else provider["base_url"],
                    "default_base_url": provider["base_url"],
                    "models": provider["models"],
                    "enabled": bool(key_row["enabled"]) if key_row else False,
                    "key_mask": mask_key(key_row["api_key_cipher"]) if key_row else "",
                }
            )
        return {
            "user": get_user_summary(conn, user_id),
            "providers": providers,
            "prompt_templates": get_prompt_templates(user_id),
            "conversations": get_conversations(user_id),
        }


def ensure_user(user_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            insert or ignore into users (id, name, quota_tokens, created_at)
            values (?, ?, ?, ?)
            """,
            (user_id, user_id, 200000, now()),
        )


def get_user_summary(conn: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    user = conn.execute("select * from users where id = ?", (user_id,)).fetchone()
    usage = conn.execute(
        """
        select
            coalesce(sum(total_tokens), 0) as total_tokens,
            coalesce(sum(cost_cents), 0) as cost_cents,
            count(*) as requests
        from usage_events
        where user_id = ?
        """,
        (user_id,),
    ).fetchone()
    provider_usage = [
        dict(row)
        for row in conn.execute(
            """
            select provider_id, coalesce(sum(total_tokens), 0) as total_tokens, count(*) as requests
            from usage_events
            where user_id = ?
            group by provider_id
            order by total_tokens desc
            """,
            (user_id,),
        ).fetchall()
    ]
    used = int(usage["total_tokens"])
    quota = int(user["quota_tokens"])
    return {
        "id": user["id"],
        "name": user["name"],
        "quota_tokens": quota,
        "used_tokens": used,
        "remaining_tokens": max(0, quota - used),
        "cost_cents": round(float(usage["cost_cents"]), 4),
        "requests": int(usage["requests"]),
        "provider_usage": provider_usage,
    }


def get_prompt_templates(user_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                select id, title, content, updated_at
                from prompt_templates
                where user_id = ?
                order by updated_at desc
                """,
                (user_id,),
            ).fetchall()
        ]


def save_prompt_template(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not title or not content:
        raise ValueError("模板名称和内容不能为空")
    template_id = str(payload.get("id") or new_id("tpl"))
    with connect() as conn:
        conn.execute(
            """
            insert into prompt_templates (id, user_id, title, content, updated_at)
            values (?, ?, ?, ?, ?)
            on conflict(id) do update set
                title = excluded.title,
                content = excluded.content,
                updated_at = excluded.updated_at
            """,
            (template_id, user_id, title, content, now()),
        )
    return {"ok": True, "template": {"id": template_id, "title": title, "content": content}}


def delete_prompt_template(user_id: str, template_id: str) -> None:
    with connect() as conn:
        conn.execute("delete from prompt_templates where user_id = ? and id = ?", (user_id, template_id))


def save_provider_key(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(payload.get("provider_id", "")).strip()
    if provider_id not in PROVIDERS:
        raise ValueError("未知模型供应商")
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise ValueError("API Key 不能为空")
    base_url = str(payload.get("base_url") or PROVIDERS[provider_id]["base_url"]).strip().rstrip("/")
    enabled = 1 if payload.get("enabled", True) else 0
    with connect() as conn:
        conn.execute(
            """
            insert into provider_keys (user_id, provider_id, base_url, api_key_cipher, enabled, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(user_id, provider_id) do update set
                base_url = excluded.base_url,
                api_key_cipher = excluded.api_key_cipher,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (user_id, provider_id, base_url, encrypt_secret(api_key), enabled, now()),
        )
    return {"ok": True, "provider_id": provider_id, "key_mask": f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "****"}


def delete_provider_key(user_id: str, provider_id: str) -> None:
    with connect() as conn:
        conn.execute("delete from provider_keys where user_id = ? and provider_id = ?", (user_id, provider_id))


def get_conversations(user_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select c.*, count(m.id) as message_count
            from conversations c
            left join messages m on m.conversation_id = c.id
            where c.user_id = ?
            group by c.id
            order by c.updated_at desc
            limit 20
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation(user_id: str, conversation_id: str) -> dict[str, Any]:
    with connect() as conn:
        conversation = conn.execute(
            "select * from conversations where user_id = ? and id = ?",
            (user_id, conversation_id),
        ).fetchone()
        if not conversation:
            raise ValueError("会话不存在")
        messages = [
            dict(row)
            for row in conn.execute(
                "select role, content, created_at from messages where conversation_id = ? order by created_at asc",
                (conversation_id,),
            ).fetchall()
        ]
        return {"conversation": dict(conversation), "messages": messages}


def complete_chat(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_user(user_id)
    model = str(payload.get("model") or "gpt-4o-mini")
    provider_id = str(payload.get("provider_id") or MODEL_TO_PROVIDER.get(model) or "openai")
    messages = normalize_messages(payload.get("messages", []))
    if not messages:
        raise ValueError("messages 不能为空")

    prompt_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
    with connect() as conn:
        user = conn.execute("select * from users where id = ?", (user_id,)).fetchone()
        used = conn.execute(
            "select coalesce(sum(total_tokens), 0) as total from usage_events where user_id = ?",
            (user_id,),
        ).fetchone()["total"]
        if int(user["quota_tokens"]) - int(used) <= 0:
            raise ValueError("Token 额度已用完")

    provider_order = build_provider_order(user_id, provider_id)
    content = ""
    final_provider = provider_order[0]["provider_id"]
    error_messages = []
    for provider in provider_order:
        final_provider = provider["provider_id"]
        try:
            content = call_provider(provider, model, messages)
            break
        except Exception as exc:
            error_messages.append(f"{provider['provider_id']}: {exc}")
            content = ""

    if not content:
        content = mock_completion(model, messages, error_messages)
        final_provider = "mock"

    completion_tokens = estimate_tokens(content)
    conversation_id = save_chat_history(user_id, payload, final_provider, model, messages, content)
    usage = record_usage(user_id, final_provider, model, prompt_tokens, completion_tokens)
    return {
        "conversation_id": conversation_id,
        "provider_id": final_provider,
        "model": model,
        "content": content,
        "usage": usage,
    }


def normalize_messages(raw_messages: Any) -> list[dict[str, str]]:
    if not isinstance(raw_messages, list):
        raise ValueError("messages 必须是数组")
    messages = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user"))
        content = str(item.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages


def build_provider_order(user_id: str, preferred_provider_id: str) -> list[dict[str, str]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select provider_id, base_url, api_key_cipher
            from provider_keys
            where user_id = ? and enabled = 1
            """,
            (user_id,),
        ).fetchall()
    providers = []
    for row in rows:
        providers.append(
            {
                "provider_id": row["provider_id"],
                "base_url": row["base_url"],
                "api_key": decrypt_secret(row["api_key_cipher"]),
            }
        )
    providers.sort(key=lambda item: 0 if item["provider_id"] == preferred_provider_id else 1)
    if providers:
        return providers
    return [{"provider_id": preferred_provider_id, "base_url": "", "api_key": ""}]


def call_provider(provider: dict[str, str], model: str, messages: list[dict[str, str]]) -> str:
    if not provider.get("api_key") or not provider.get("base_url"):
        raise RuntimeError("未配置 API Key，已切换到本地模拟响应")
    url = f"{provider['base_url'].rstrip('/')}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"供应商返回 {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"供应商连接失败: {exc.reason}") from exc

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("供应商响应格式不兼容") from exc


def mock_completion(model: str, messages: list[dict[str, str]], errors: list[str]) -> str:
    last_user = next((message["content"] for message in reversed(messages) if message["role"] == "user"), "")
    digest = hashlib.sha1(last_user.encode("utf-8")).hexdigest()[:8]
    error_note = ""
    if errors:
        error_note = "\n\n本次未调用真实供应商，原因：" + "；".join(errors[-2:])
    return (
        f"已收到你的请求，我将以 {model} 的聚合平台模拟通道回答。\n\n"
        f"问题摘要：{last_user[:180] or '无用户输入'}\n\n"
        "建议落地路径：\n"
        "1. 先在“模型配置”里录入供应商 API Key，并保留 OpenAI 兼容 Base URL。\n"
        "2. 通过“Prompt 模板”沉淀系统提示词，让不同业务场景复用同一入口。\n"
        "3. 使用“计费看板”观察 token 消耗，再按用户或团队做额度扣减。\n"
        "4. 后续可把当前模拟适配器替换为真实厂商 SDK 或 HTTP 调用。\n\n"
        f"请求指纹：{digest}{error_note}"
    )


def save_chat_history(
    user_id: str,
    payload: dict[str, Any],
    provider_id: str,
    model: str,
    messages: list[dict[str, str]],
    assistant_content: str,
) -> str:
    current = now()
    conversation_id = str(payload.get("conversation_id") or new_id("conv"))
    title = next((message["content"][:40] for message in messages if message["role"] == "user"), "新会话")
    with connect() as conn:
        conn.execute(
            """
            insert into conversations (id, user_id, title, model, provider_id, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                model = excluded.model,
                provider_id = excluded.provider_id,
                updated_at = excluded.updated_at
            """,
            (conversation_id, user_id, title, model, provider_id, current, current),
        )
        for message in messages[-2:]:
            conn.execute(
                "insert into messages (id, conversation_id, role, content, created_at) values (?, ?, ?, ?, ?)",
                (new_id("msg"), conversation_id, message["role"], message["content"], current),
            )
        conn.execute(
            "insert into messages (id, conversation_id, role, content, created_at) values (?, ?, ?, ?, ?)",
            (new_id("msg"), conversation_id, "assistant", assistant_content, current + 1),
        )
    return conversation_id


def record_usage(user_id: str, provider_id: str, model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    total_tokens = prompt_tokens + completion_tokens
    cost_cents = round(total_tokens * 0.00001, 6)
    with connect() as conn:
        conn.execute(
            """
            insert into usage_events
            (id, user_id, provider_id, model, prompt_tokens, completion_tokens, total_tokens, cost_cents, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("usage"), user_id, provider_id, model, prompt_tokens, completion_tokens, total_tokens, cost_cents, now()),
        )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_cents": cost_cents,
    }


def chunk_text(text: str, size: int = 8) -> list[str]:
    chunks = []
    buffer = ""
    for char in text:
        buffer += char
        if len(buffer) >= size or char in "\n。；;,.，":
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def to_openai_response(response: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "id": new_id("chatcmpl"),
        "object": "chat.completion",
        "created": now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response["content"]},
                "finish_reason": "stop",
            }
        ],
        "usage": response["usage"],
    }


def verify_request_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), PlatformHandler)
    print(f"LLM aggregation platform running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")


if __name__ == "__main__":
    main()
