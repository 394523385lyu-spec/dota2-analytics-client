from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class HttpError(RuntimeError):
    pass


def _curl_json(
    url: str,
    *,
    method: str,
    headers: Dict[str, str],
    body: Optional[Dict[str, Any]],
    timeout: int,
    retries: int,
) -> Any:
    command = [
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--compressed",
        "--connect-timeout",
        "8",
        "--max-time",
        str(max(timeout, 30)),
        "--retry",
        str(max(5, retries)),
        "--retry-delay",
        "2",
        "--retry-max-time",
        "120",
        "--retry-all-errors",
        "--request",
        method,
    ]
    for key, value in headers.items():
        command.extend(("--header", f"{key}: {value}"))
    if body is not None:
        command.extend(
            (
                "--data-binary",
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            )
        )
    command.append(url)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HttpError(f"系统网络请求失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知网络错误").strip()
        raise HttpError(f"OpenDota 请求失败：{detail[:500]}")
    try:
        return json.loads(result.stdout) if result.stdout else {}
    except json.JSONDecodeError as exc:
        raise HttpError("OpenDota 返回了无法识别的数据。") from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 45,
    retries: int = 3,
) -> Any:
    payload = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Dota2Analytics/1.1"
        ),
        **(headers or {}),
    }
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    # macOS 系统 curl 在国内网络与 Cloudflare/OpenDota 的连接上更稳定，
    # 也直接使用系统钥匙串中的根证书。优先使用它，urllib 作为备用。
    try:
        return _curl_json(
            url,
            method=method,
            headers=request_headers,
            body=body,
            timeout=timeout,
            retries=retries,
        )
    except HttpError as curl_error:
        last_error: Optional[Exception] = curl_error

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=payload, headers=request_headers, method=method
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code < 500 and exc.code != 429:
                raise HttpError(f"HTTP {exc.code}: {detail[:500]}") from exc
            last_error = HttpError(f"HTTP {exc.code}: {detail[:500]}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (2**attempt))
    raise HttpError(f"网络请求失败：{last_error}")


def with_query(url: str, params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return f"{url}?{urllib.parse.urlencode(clean)}"
