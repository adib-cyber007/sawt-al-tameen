"""Small REST client for AssemblyAI Voice Agent resources.

Provisioning and post-call ingestion use only a handful of endpoints. Keeping the HTTP boundary explicit makes
timeouts, error mapping and secret redaction deterministic without coupling the application to an SDK release.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_API_BASE = "https://agents.assemblyai.com"


class AssemblyAIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AssemblyAIClient:
    def __init__(
        self,
        api_key: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        timeout_secs: float = 30,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._timeout_secs = timeout_secs
        self._opener = opener

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        query: dict[str, str | int] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._api_base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, method=method, data=data, headers=headers)
        try:
            with self._opener(request, timeout=self._timeout_secs) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:1000]
            raise AssemblyAIError(
                f"AssemblyAI returned HTTP {exc.code}: {detail}", status=exc.code
            ) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise AssemblyAIError(f"AssemblyAI could not be reached: {reason}") from None
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AssemblyAIError("AssemblyAI returned a non-JSON response") from None
        if not isinstance(payload, dict):
            raise AssemblyAIError("AssemblyAI returned an unexpected JSON response")
        return payload

    def download_json(self, url: str) -> dict[str, Any]:
        """Download one pre-signed JSON artifact. The signature in the URL is the authorization."""
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._opener(request, timeout=self._timeout_secs) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise AssemblyAIError(
                f"AssemblyAI artifact returned HTTP {exc.code}", status=exc.code
            ) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise AssemblyAIError(f"AssemblyAI artifact could not be reached: {reason}") from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AssemblyAIError("AssemblyAI artifact was not valid JSON") from None
        if not isinstance(payload, dict):
            raise AssemblyAIError("AssemblyAI artifact had an unexpected JSON shape")
        return payload
