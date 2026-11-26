"""Client HTTPS cho phiên Admin và credential riêng của companion."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class WorkerError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Worker HTTP {status}. {message}")
        self.status = status


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class WorkerClient:
    def __init__(self, endpoint: str, service_token: str) -> None:
        self.endpoint = endpoint
        self.service_token = service_token
        self.opener = build_opener(NoRedirects())

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
        if not path.startswith("/api/") or "\\" in path:
            raise ValueError("Đường dẫn Worker không hợp lệ.")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data, headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.loads(response.read(65536)) if response.status != 204 else {}
        except HTTPError as exc:
            with exc:
                try:
                    message = json.loads(exc.read(4096)).get("detail", "")
                    if not isinstance(message, str):
                        message = ""
                except (ValueError, AttributeError):
                    message = ""
            raise WorkerError(exc.code, message[:250]) from None
        except (URLError, TimeoutError, OSError):
            raise WorkerError(503, "Không kết nối được Worker.") from None

    def require_admin(self, username: str, password: str) -> None:
        session = self.request("POST", "/api/auth/login", {"username": username, "password": password})
        token = session["token"]
        try:
            who = self.request("GET", "/api/auth/me", token=token)
            if who["role"] != "ADMIN":
                raise WorkerError(403, "Companion cần quyền Admin.")
        finally:
            self.request("POST", "/api/auth/logout", token=token)
        self.service("GET", "/api/local/health")

    def service(self, method: str, path: str, body: dict | None = None) -> dict:
        return self.request(method, path, body, self.service_token)

