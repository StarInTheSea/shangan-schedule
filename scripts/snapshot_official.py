#!/usr/bin/env python3
"""Rate-limited, non-overwriting snapshots for explicitly approved official URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from source_registry import allowed_hosts, host_is_allowed


USER_AGENT = "shangan-schedule/0.1 (official-notice research; rate-limited)"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,95}$")
BLOCKED_URL_MARKERS = re.compile(
    r"(?:^|[/_.?=&-])(?:login|signin|captcha|verify|verification|auth)(?:$|[/_.?=&-])",
    re.IGNORECASE,
)
BLOCKED_HTML_MARKERS = (
    re.compile(r"<input\b[^>]*\btype\s*=\s*['\"]?password\b", re.IGNORECASE),
    re.compile(r"\b(?:id|class)\s*=\s*['\"][^'\"]*(?:captcha|verify-code|verification)[^'\"]*['\"]", re.IGNORECASE),
    re.compile(r"<title[^>]*>[^<]*(?:登录|验证码|安全验证|访问受限|access denied|login)[^<]*</title>", re.IGNORECASE),
)


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


class WhitelistRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect before any request reaches a nonofficial host."""

    def __init__(self, whitelist: set[str]) -> None:
        super().__init__()
        self.whitelist = whitelist

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved_url = urljoin(req.full_url, newurl)
        parsed = urlparse(resolved_url)
        if parsed.scheme not in ("https", "http") or not host_is_allowed(
            parsed.hostname or "", self.whitelist
        ):
            raise ValueError(
                f"重定向目标不在官方白名单: {parsed.hostname or resolved_url}"
            )
        return super().redirect_request(
            req, fp, code, msg, headers, resolved_url
        )


def open_allowlisted(request: Request, whitelist: set[str], timeout: float):
    opener = build_opener(WhitelistRedirectHandler(whitelist))
    return opener.open(request, timeout=timeout)


def robots_allows(url: str, timeout: float, whitelist: set[str]) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = robotparser.RobotFileParser()
    parser.set_url(robots_url)
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with open_allowlisted(request, whitelist, timeout) as response:
            parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
    except HTTPError as error:
        if error.code in (401, 403):
            return False
        if error.code == 404:
            return True
        raise
    return parser.can_fetch(USER_AGENT, url)


def extension_for(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return {
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "application/json": ".json",
    }.get(normalized, ".bin")


def detect_access_block(body: bytes, content_type: str, final_url: str) -> str | None:
    """Return a reason when a successful response is actually an access gate."""

    if BLOCKED_URL_MARKERS.search(final_url):
        return "最终网址疑似登录或验证入口"
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in {"text/html", "application/xhtml+xml", "text/plain"}:
        return None
    text = body.decode("utf-8", errors="replace")
    if any(pattern.search(text) for pattern in BLOCKED_HTML_MARKERS):
        return "响应内容疑似登录、验证码或访问限制页面"
    return None


def snapshot(
    item: dict[str, Any],
    output: Path,
    whitelist: set[str],
    timeout: float,
    max_bytes: int,
) -> dict[str, Any]:
    item_id = str(item.get("id", ""))
    url = str(item.get("url", ""))
    if not SAFE_ID.fullmatch(item_id):
        raise ValueError(f"非法请求 ID: {item_id}")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError(f"非法网址: {url}")
    if not host_is_allowed(parsed.hostname, whitelist):
        raise ValueError(f"来源域名不在官方白名单: {parsed.hostname}")
    if not robots_allows(url, timeout, whitelist):
        raise PermissionError(f"robots.txt 不允许抓取: {url}")

    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,text/plain,application/json;q=0.9,*/*;q=0.1"})
    with open_allowlisted(request, whitelist, timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"响应超过大小限制: {content_length} bytes")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"响应超过大小限制: {max_bytes} bytes")
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        final_url = response.geturl()
        status = getattr(response, "status", 200)

    final_host = urlparse(final_url).hostname or ""
    if not host_is_allowed(final_host, whitelist):
        raise ValueError(f"重定向目标不在官方白名单: {final_host}")
    access_block = detect_access_block(body, content_type, final_url)
    if access_block:
        raise PermissionError(f"{access_block}: {final_url}")

    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    digest = hashlib.sha256(body).hexdigest()
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    item_directory = output / item_id
    item_directory.mkdir(parents=True, exist_ok=True)
    basename = f"{stamp}-{digest[:12]}"
    content_path = item_directory / f"{basename}{extension_for(content_type)}"
    metadata_path = item_directory / f"{basename}.metadata.json"
    if content_path.exists() or metadata_path.exists():
        raise FileExistsError(f"快照已存在，不覆盖: {basename}")
    content_path.write_bytes(body)
    metadata = {
        "schema_version": 1,
        "id": item_id,
        "requested_url": url,
        "final_url": final_url,
        "retrieved_at": retrieved_at,
        "http_status": status,
        "content_type": content_type,
        "bytes": len(body),
        "sha256": digest,
        "content_path": str(content_path),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="限速保存官方公告原始快照")
    parser.add_argument("--manifest", required=True, help="包含 requests 数组的 JSON")
    parser.add_argument("--sources", required=True, help="官方来源白名单 JSON")
    parser.add_argument("--output", required=True, help="非覆盖式快照目录")
    parser.add_argument("--delay", type=float, default=2.0, help="请求间隔秒数，最小 1 秒")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数")
    parser.add_argument("--max-bytes", type=int, default=15_000_000, help="单个响应大小上限")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.delay < 1:
        print("ERROR 请求间隔不能小于 1 秒", file=sys.stderr)
        return 2
    if args.max_bytes < 1:
        print("ERROR 大小上限必须为正数", file=sys.stderr)
        return 2
    try:
        manifest = load_object(Path(args.manifest))
        registry = load_object(Path(args.sources))
        requests = manifest.get("requests", [])
        if not isinstance(requests, list) or not requests:
            raise ValueError("manifest.requests 必须是非空数组")
        whitelist = allowed_hosts(registry)
        results = []
        output = Path(args.output)
        for index, item in enumerate(requests):
            if index:
                time.sleep(args.delay)
            results.append(
                snapshot(item, output, whitelist, args.timeout, args.max_bytes)
            )
        report = {
            "schema_version": 1,
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "snapshots": results,
        }
        report_path = output / f"run-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"抓取完成: {len(results)} 个官方页面；报告 {report_path}")
        return 0
    except (OSError, ValueError, PermissionError, HTTPError, URLError, json.JSONDecodeError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
