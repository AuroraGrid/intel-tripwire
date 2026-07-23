from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GroqPublicEvidenceAssistant:
    """Optional free-tier enrichment for public evidence only.

    Groq output is always recorded as an inference. It can never change an
    evidentiary state or produce an authenticity verdict without analyst review.
    """

    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, verifier, opener=None):
        self.verifier = verifier
        self.opener = opener or urllib.request.urlopen
        self.enabled = os.getenv("AURORA_GROQ_ENABLED", "false").lower() in {
            "1", "true", "yes", "on",
        }
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.model = os.getenv("AURORA_GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
        self.daily_limit = max(
            0, min(250, int(os.getenv("AURORA_GROQ_DAILY_LIMIT", "40")))
        )
        self.timeout = max(
            2, min(60, int(os.getenv("AURORA_GROQ_TIMEOUT_SECONDS", "20")))
        )

    def status(self) -> dict[str, Any]:
        return {
            "provider": "GROQ",
            "enabled": self.enabled,
            "configured": bool(self.api_key),
            "model": self.model,
            "daily_limit_per_workspace": self.daily_limit,
            "authority": "INFERENCE_ONLY",
            "classification_allowed": "PUBLIC",
        }

    def _safe_public_url(self, value: Any) -> str:
        url = str(value or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("a public HTTPS image URL is required")
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            raise ValueError("private or local media URLs are not allowed")
        try:
            address = socket.gethostbyname(host)
            parts = [int(item) for item in address.split(".")]
            if (
                parts[0] in {0, 10, 127}
                or (parts[0] == 169 and parts[1] == 254)
                or (parts[0] == 172 and 16 <= parts[1] <= 31)
                or (parts[0] == 192 and parts[1] == 168)
            ):
                raise ValueError("private or local media URLs are not allowed")
        except socket.gaierror:
            # Groq, not AURORA, retrieves the URL. A DNS failure here does not
            # prove the public URL is invalid in Groq's network.
            pass
        return url

    def _parse_content(self, value: Any) -> dict[str, Any]:
        text = str(value or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"raw_output": parsed}
        except json.JSONDecodeError:
            return {"raw_output": text}

    def analyze(self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Groq assistance is disabled")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")
        if self.daily_limit <= 0:
            raise RuntimeError("Groq daily limit is zero")

        asset = self.verifier.asset(actor, asset_id, include_children=False)
        if asset["classification"] != "PUBLIC":
            raise ValueError("only PUBLIC evidence may be sent to Groq")

        image_url = self._safe_public_url(payload.get("image_url") or asset.get("source_url"))
        self.verifier.reserve_ai(actor, "GROQ", self.daily_limit)

        task = str(payload.get("task") or "Analyze observable details in this image.").strip()
        instructions = (
            "You assist an evidence-first OSINT system. Return one JSON object with "
            "keys observation, inference, uncertainty, falsifiers, visible_text, "
            "location_clues, time_clues, manipulation_indicators. Separate directly "
            "visible observations from inferences. Never call media authentic, genuine, "
            "fake, verified, or proven. State uncertainty and possible falsifiers."
        )
        body = {
            "model": self.model,
            "temperature": 0,
            "max_completion_tokens": 1200,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": task},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "AURORA-LIVE/Phase19",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.verifier.record_ai_failure(actor, "GROQ")
            raise RuntimeError("Groq evidence assistance failed") from exc

        choices = result.get("choices") or []
        if not choices:
            self.verifier.record_ai_failure(actor, "GROQ")
            raise RuntimeError("Groq returned no result")
        parsed = self._parse_content((choices[0].get("message") or {}).get("content"))
        observation = str(parsed.get("observation") or "")
        inference = str(parsed.get("inference") or parsed.get("raw_output") or "")
        if not inference:
            inference = "The model returned no explicit inference."
        check = self.verifier.record_check(actor, asset_id, {
            "check_type": str(payload.get("check_type") or "CONTEXT").upper(),
            "result_kind": "INFERENCE",
            "producer": "GROQ",
            "model": self.model,
            "model_version": str(result.get("system_fingerprint") or ""),
            "state": "UNRESOLVED",
            "observation": observation,
            "inference": inference,
            "uncertainty": str(parsed.get("uncertainty") or "Model output requires analyst review"),
            "falsifiers": parsed.get("falsifiers") or [],
            "output": {
                "provider_request_id": (result.get("x_groq") or {}).get("id"),
                "visible_text": parsed.get("visible_text") or [],
                "location_clues": parsed.get("location_clues") or [],
                "time_clues": parsed.get("time_clues") or [],
                "manipulation_indicators": parsed.get("manipulation_indicators") or [],
                "source_url": image_url,
            },
        })
        return {"check": check, "provider": self.status()}
