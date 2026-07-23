from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterator


class AuroraAPIError(RuntimeError):
    def __init__(self, status: int, payload: Any):
        super().__init__(f"AURORA API request failed ({status}): {payload}")
        self.status = status
        self.payload = payload


class AuroraClient:
    """Dependency-free Python client for AURORA API v1 and MCP."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        api_key: str = "",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_key = api_key
        self.timeout = timeout
        if not token and not api_key:
            raise ValueError("token or api_key required")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        if self.api_key:
            request_headers["X-AURORA-API-KEY"] = self.api_key
        else:
            request_headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            raise AuroraAPIError(exc.code, value) from exc

    def pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> Iterator[dict[str, Any]]:
        query = dict(params or {})
        query["limit"] = max(1, min(200, int(limit)))
        cursor = ""
        while True:
            if cursor:
                query["cursor"] = cursor
            separator = "&" if "?" in path else "?"
            response = self._request(
                "GET",
                path + separator + urllib.parse.urlencode(query),
            )
            yield response
            cursor = str(response.get("meta", {}).get("next_cursor") or "")
            if not cursor:
                return

    def detections(self, **filters: Any) -> Iterator[dict[str, Any]]:
        for page in self.pages("/api/v1/detections", params=filters):
            yield from page["data"]

    def routes(self, **filters: Any) -> Iterator[dict[str, Any]]:
        for page in self.pages("/api/v1/routes", params=filters):
            yield from page["data"]

    def forecast_candidates(
        self, **filters: Any
    ) -> Iterator[dict[str, Any]]:
        for page in self.pages(
            "/api/v1/forecast-candidates", params=filters
        ):
            yield from page["data"]

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/api/v1/search?"
            + urllib.parse.urlencode({"query": query, "limit": limit}),
        )
        return response["data"]

    def approve_forecast(
        self, candidate_id: str, rationale: str, idempotency_key: str = ""
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/forecast-candidates/{urllib.parse.quote(candidate_id)}/approve",
            {"rationale": rationale},
            {"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )

    def mcp_call(
        self, tool: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        response = self._request(
            "POST",
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            },
        )
        if "error" in response:
            raise AuroraAPIError(400, response["error"])
        return response["result"]["structuredContent"]
