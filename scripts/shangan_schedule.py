#!/usr/bin/env python3
"""Deterministic helpers for the shangan-schedule Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from source_registry import allowed_hosts, authorities_by_id, host_is_allowed


EXAM_TYPES = {"national", "provincial", "municipal", "selected_graduate"}
EVENT_STAGES = {
    "announcement",
    "registration",
    "qualification",
    "payment",
    "admit_card",
    "written_exam",
    "score",
}
EVENT_STATUSES = {"confirmed", "tentative", "changed"}
REVIEW_STATUSES = {"candidate", "approved", "rejected"}
COVERAGE_STATUSES = {"verified", "candidate", "pending", "not_found"}
SCOPE_MODES = {"full", "partial"}
SAFE_REVIEW_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
EVIDENCE_DATE = re.compile(
    r"(?:20\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|(?:20\d{2}年)?\d{1,2}月(?:\d{1,2}日)?)"
)
GENERIC_PAGE_STEM = re.compile(
    r"^(?:index|default|home|list|search)(?:[_-].*)?$", re.IGNORECASE
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def valid_temporal_value(value: str, precision: str) -> bool:
    try:
        if precision == "date":
            date.fromisoformat(value)
        elif precision == "datetime":
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return False
        elif precision == "month":
            if len(value) != 7:
                return False
            date.fromisoformat(f"{value}-01")
        else:
            return False
    except ValueError:
        return False
    return True


def registry_regions(authorities: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Return one stable display name per region in registry order."""

    regions: dict[str, str] = {}
    for authority in authorities.values():
        code = str(authority.get("region_code", "")).strip()
        if not code or code in regions:
            continue
        name = str(authority.get("region_name", "")).strip()
        regions[code] = name or ("全国" if code == "CN" else str(authority.get("name", code)))
    return regions


def scope_region_codes(exam_type: str, regions: dict[str, str]) -> list[str]:
    if exam_type == "national":
        return ["CN"] if "CN" in regions else []
    if exam_type == "provincial":
        return [code for code in regions if code != "CN"]
    if exam_type == "selected_graduate":
        return [code for code in regions if code not in {"CN", "660000"}]
    if exam_type == "municipal":
        return [code for code in regions if code != "CN"]
    return []


def recount_coverage_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose counters match its region states."""

    result = deepcopy(scope)
    regions = result.get("regions", [])
    counts = {
        status: sum(region.get("status") == status for region in regions)
        for status in COVERAGE_STATUSES
    }
    result["expected"] = len(regions)
    result.update(counts)
    return result


def merge_coverage_scope(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge a partial refresh into an existing full scope without shrinking it."""

    if incoming.get("scope_mode", "full") != "partial":
        return deepcopy(incoming)
    if existing is None or existing.get("scope_mode", "full") != "full":
        raise ValueError("局部覆盖范围必须合并到已有的完整范围，不能单独晋升")

    incoming_regions = {
        str(region["region_code"]): deepcopy(region)
        for region in incoming.get("regions", [])
    }
    existing_codes = {
        str(region["region_code"]) for region in existing.get("regions", [])
    }
    unexpected = sorted(set(incoming_regions) - existing_codes)
    if unexpected:
        raise ValueError("局部覆盖范围包含完整范围之外的地区: " + ", ".join(unexpected))

    merged = deepcopy(existing)
    merged["scope_mode"] = "full"
    merged["checked_at"] = incoming.get("checked_at", existing.get("checked_at"))
    merged["regions"] = [
        incoming_regions.get(str(region["region_code"]), deepcopy(region))
        for region in existing.get("regions", [])
    ]
    return recount_coverage_scope(merged)


def looks_like_generic_page(url: str) -> bool:
    """Reject obvious home, index, search, and section-list URLs."""

    parsed = urlparse(url)
    fragment_path, separator, fragment_query = parsed.fragment.partition("?")
    fragment_parameters = parse_qs(fragment_query) if separator else {}
    if (
        fragment_path.strip("/").lower() == "txt"
        and any(value.strip() for value in fragment_parameters.get("contentId", []))
    ):
        return False
    path = parsed.path.rstrip()
    if not path or path == "/" or path.endswith("/"):
        return True
    basename = path.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    parent = path.rstrip("/").rsplit("/", 2)[-2] if "/" in path.rstrip("/") else ""
    if GENERIC_PAGE_STEM.fullmatch(stem) and re.search(r"\d{8,}", parent):
        return False
    return bool(GENERIC_PAGE_STEM.fullmatch(stem))


def validate_dataset(dataset: dict[str, Any], sources: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    whitelist = allowed_hosts(sources)
    authorities = authorities_by_id(sources)
    region_registry = registry_regions(authorities)
    baseline_region_codes = {
        str(authority.get("region_code", ""))
        for authority in authorities.values()
        if str(authority.get("region_code", ""))
    }
    if dataset.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if not isinstance(dataset.get("exams"), list):
        return errors + ["exams 必须是数组"]

    coverage = dataset.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("coverage 必须是对象")
    else:
        coverage_values: dict[str, int] = {}
        for field in ("expected", "verified", "pending", "not_found"):
            value = coverage.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"coverage.{field} 必须是非负整数")
            else:
                coverage_values[field] = value
        if coverage_values.get("expected") != len(baseline_region_codes):
            errors.append(
                f"coverage.expected 必须等于官方辖区数 {len(baseline_region_codes)}"
            )
        if all(
            field in coverage_values
            for field in ("expected", "verified", "pending", "not_found")
        ) and (
            coverage_values["verified"]
            + coverage_values["pending"]
            + coverage_values["not_found"]
            != coverage_values["expected"]
        ):
            errors.append("覆盖状态合计必须等于 expected")
        checked_at = str(coverage.get("checked_at", ""))
        if not valid_temporal_value(checked_at, "datetime"):
            errors.append("coverage.checked_at 必须是带时区的 ISO 时间")

    seen_exam_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    regions_with_approved_events: set[str] = set()
    scoped_event_states: dict[tuple[int, str, str], set[str]] = {}
    for exam_index, exam in enumerate(dataset.get("exams", [])):
        if not isinstance(exam, dict):
            errors.append(f"考试条目必须是对象: exams[{exam_index}]")
            continue
        exam_id = str(exam.get("id", ""))
        if not exam_id:
            errors.append("考试缺少 ID")
        if exam_id in seen_exam_ids:
            errors.append(f"考试 ID 重复: {exam_id}")
        seen_exam_ids.add(exam_id)
        exam_type = str(exam.get("exam_type", ""))
        if exam_type not in EXAM_TYPES:
            errors.append(f"{exam_id or 'unknown'}: 非法考试类型: {exam_type}")
        exam_region_code = str(exam.get("region_code", ""))
        if (
            exam_type == "selected_graduate"
            and exam_region_code not in set(scope_region_codes(exam_type, region_registry))
        ):
            errors.append(
                f"{exam_id or 'unknown'}: 选调生考试地区不适用: {exam_region_code}"
            )
        for field, label in (
            ("title", "考试标题"),
            ("region_code", "地区代码"),
            ("region_name", "地区名称"),
        ):
            if not str(exam.get(field, "")).strip():
                errors.append(f"{exam_id or 'unknown'}: 缺少{label}")
        if not isinstance(exam.get("recruitment_year"), int):
            errors.append(f"{exam_id or 'unknown'}: 招录年度必须是整数")
        if not isinstance(exam.get("events"), list):
            errors.append(f"{exam_id or 'unknown'}: events 必须是数组")
            continue
        for event_index, event in enumerate(exam.get("events", [])):
            if not isinstance(event, dict):
                errors.append(
                    f"事件条目必须是对象: {exam_id or 'unknown'}.events[{event_index}]"
                )
                continue
            event_id = str(event.get("id", ""))
            if not event_id:
                errors.append(f"{exam_id or 'unknown'}: 事件缺少 ID")
            if event_id in seen_event_ids:
                errors.append(f"事件 ID 重复: {event_id}")
            seen_event_ids.add(event_id)
            stage = str(event.get("stage", ""))
            if stage not in EVENT_STAGES:
                errors.append(f"{event_id or 'unknown'}: 非法事件阶段: {stage}")
            status = str(event.get("status", ""))
            if status not in EVENT_STATUSES:
                errors.append(f"{event_id or 'unknown'}: 非法日期状态: {status}")
            if not str(event.get("label", "")).strip():
                errors.append(f"{event_id or 'unknown'}: 缺少事件标签")
            precision = str(event.get("precision", ""))
            temporal_values: dict[str, str] = {}
            for field in ("start", "end"):
                value = str(event.get(field, ""))
                temporal_values[field] = value
                if not valid_temporal_value(value, precision):
                    errors.append(
                        f"{event.get('id', 'unknown')}: {field} 不是有效日期: {value}"
                    )
            if (
                all(valid_temporal_value(value, precision) for value in temporal_values.values())
                and temporal_values["end"] < temporal_values["start"]
            ):
                errors.append(f"{event_id or 'unknown'}: 结束时间早于开始时间")
            source_value = event.get("source", {})
            if not isinstance(source_value, dict):
                errors.append(f"{event_id or 'unknown'}: source 必须是对象")
                source: dict[str, Any] = {}
            else:
                source = source_value
            required_source_fields = {
                "authority_id": "官方机构 ID",
                "title": "来源标题",
                "url": "来源网址",
                "publisher": "发布机构",
                "published_at": "发布日期",
                "retrieved_at": "抓取时间",
                "evidence": "来源证据",
            }
            for field, label in required_source_fields.items():
                if not str(source.get(field, "")).strip():
                    errors.append(f"{event.get('id', 'unknown')}: 缺少{label}")
            source_url = str(source.get("url", ""))
            parsed_source_url = urlparse(source_url)
            host = (parsed_source_url.hostname or "").lower()
            if parsed_source_url.scheme not in ("https", "http"):
                errors.append(f"{event_id or 'unknown'}: 来源网址必须使用 HTTP(S)")
            if not host_is_allowed(host, whitelist):
                errors.append(
                    f"{event.get('id', 'unknown')}: 来源域名不在官方白名单: {host or source_url}"
                )
            authority_id = str(source.get("authority_id", ""))
            authority = authorities.get(authority_id)
            if authority_id and authority is None:
                errors.append(f"{event_id or 'unknown'}: 未知官方机构 ID: {authority_id}")
            elif authority is not None and not host_is_allowed(
                host,
                {str(item).lower() for item in authority.get("allowed_hosts", [])},
            ):
                errors.append(
                    f"{event_id or 'unknown'}: 来源域名不属于声明的官方机构: {host} / {authority_id}"
                )
            if authority is not None and exam_type in {
                "national",
                "provincial",
                "selected_graduate",
            }:
                authority_region = str(authority.get("region_code", ""))
                exam_region = str(exam.get("region_code", ""))
                if exam_region not in baseline_region_codes or authority_region != exam_region:
                    errors.append(
                        f"{event_id or 'unknown'}: 考试地区与官方机构地区不一致: "
                        f"{exam_region} / {authority_region or authority_id}"
                    )
            if authority is not None:
                entry_urls = {
                    str(item).rstrip("/")
                    for item in authority.get("entry_urls", [])
                }
                if source_url.rstrip("/") in entry_urls:
                    errors.append(
                        f"{event_id or 'unknown'}: 来源网址只是入口页，必须使用精确公告链接"
                    )
                elif looks_like_generic_page(source_url):
                    errors.append(
                        f"{event_id or 'unknown'}: 来源网址疑似栏目或入口页，必须使用精确公告链接"
                    )
            evidence = str(source.get("evidence", "")).strip()
            if evidence and not EVIDENCE_DATE.search(evidence):
                errors.append(f"{event_id or 'unknown'}: 来源证据必须包含日期")
            if len(evidence) > 240:
                errors.append(f"{event_id or 'unknown'}: 来源证据超过 240 字")
            published_at = str(source.get("published_at", ""))
            if published_at and not valid_temporal_value(published_at, "date"):
                errors.append(f"{event_id or 'unknown'}: 来源发布日期格式非法")
            retrieved_at = str(source.get("retrieved_at", ""))
            if retrieved_at and not valid_temporal_value(retrieved_at, "datetime"):
                errors.append(f"{event_id or 'unknown'}: 抓取时间必须带时区")

            review_value = event.get("review", {})
            if not isinstance(review_value, dict):
                errors.append(f"{event_id or 'unknown'}: review 必须是对象")
                review: dict[str, Any] = {}
            else:
                review = review_value
            review_status = str(review.get("status", ""))
            if review_status not in REVIEW_STATUSES:
                errors.append(f"{event_id or 'unknown'}: 非法审核状态: {review_status}")
            recruitment_year = exam.get("recruitment_year")
            region_code = str(exam.get("region_code", ""))
            if (
                isinstance(recruitment_year, int)
                and not isinstance(recruitment_year, bool)
                and exam_type in EXAM_TYPES
                and region_code
                and review_status in {"candidate", "approved"}
            ):
                scoped_event_states.setdefault(
                    (recruitment_year, exam_type, region_code), set()
                ).add(review_status)
            if review_status == "approved":
                if exam_type in {"national", "provincial"}:
                    regions_with_approved_events.add(str(exam.get("region_code", "")))
                if not str(review.get("reviewed_at", "")).strip():
                    errors.append(f"{event_id or 'unknown'}: 已批准事件缺少审核时间")
                if not str(review.get("reviewed_by", "")).strip():
                    errors.append(f"{event_id or 'unknown'}: 已批准事件缺少审核人")
                if review.get("source_url_verified") is not True:
                    errors.append(
                        f"{event_id or 'unknown'}: 已批准事件必须确认精确公告链接"
                    )
    if isinstance(coverage, dict):
        verified = coverage.get("verified")
        if (
            isinstance(verified, int)
            and not isinstance(verified, bool)
            and verified > len(regions_with_approved_events)
        ):
            errors.append(
                "coverage.verified 不能超过含已批准事件的辖区数 "
                f"{len(regions_with_approved_events)}"
            )

    coverage_scopes = dataset.get("coverage_scopes")
    if coverage_scopes is not None and not isinstance(coverage_scopes, list):
        errors.append("coverage_scopes 必须是数组")
    elif isinstance(coverage_scopes, list):
        seen_scope_keys: set[tuple[int, str]] = set()
        for scope_index, scope in enumerate(coverage_scopes):
            prefix = f"coverage_scopes[{scope_index}]"
            if not isinstance(scope, dict):
                errors.append(f"{prefix} 必须是对象")
                continue
            year = scope.get("recruitment_year")
            exam_type = str(scope.get("exam_type", ""))
            scope_mode = str(scope.get("scope_mode", "full"))
            if not isinstance(year, int) or isinstance(year, bool):
                errors.append(f"{prefix}.recruitment_year 必须是整数")
            if exam_type not in EXAM_TYPES:
                errors.append(f"{prefix}.exam_type 非法: {exam_type}")
            if scope_mode not in SCOPE_MODES:
                errors.append(f"{prefix}.scope_mode 非法: {scope_mode}")
            if exam_type == "municipal" and scope_mode != "partial":
                errors.append(f"{prefix} 独立市考范围必须是 partial")
            if isinstance(year, int) and not isinstance(year, bool) and exam_type in EXAM_TYPES:
                scope_key = (year, exam_type)
                if scope_key in seen_scope_keys:
                    errors.append(f"{prefix} 年度与考试类型重复: {year}/{exam_type}")
                seen_scope_keys.add(scope_key)

            counts: dict[str, int] = {}
            for field in ("expected", "verified", "candidate", "pending", "not_found"):
                value = scope.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(f"{prefix}.{field} 必须是非负整数")
                else:
                    counts[field] = value
            if all(
                field in counts
                for field in ("expected", "verified", "candidate", "pending", "not_found")
            ) and sum(counts[field] for field in COVERAGE_STATUSES) != counts["expected"]:
                errors.append(f"{prefix} 覆盖状态合计必须等于 expected")
            checked_at = str(scope.get("checked_at", ""))
            if not valid_temporal_value(checked_at, "datetime"):
                errors.append(f"{prefix}.checked_at 必须是带时区的 ISO 时间")

            scope_regions = scope.get("regions")
            if not isinstance(scope_regions, list):
                errors.append(f"{prefix}.regions 必须是数组")
                continue
            if "expected" in counts and len(scope_regions) != counts["expected"]:
                errors.append(f"{prefix}.regions 数量必须等于 expected")
            state_counts = {status: 0 for status in COVERAGE_STATUSES}
            seen_region_codes: set[str] = set()
            for region_index, region in enumerate(scope_regions):
                region_prefix = f"{prefix}.regions[{region_index}]"
                if not isinstance(region, dict):
                    errors.append(f"{region_prefix} 必须是对象")
                    continue
                region_code = str(region.get("region_code", "")).strip()
                region_name = str(region.get("region_name", "")).strip()
                state = str(region.get("status", ""))
                if not region_code or region_code not in baseline_region_codes:
                    errors.append(f"{region_prefix}.region_code 不在官方辖区表: {region_code}")
                if region_code in seen_region_codes:
                    errors.append(f"{region_prefix}.region_code 重复: {region_code}")
                seen_region_codes.add(region_code)
                if not region_name:
                    errors.append(f"{region_prefix}.region_name 不能为空")
                if state not in COVERAGE_STATUSES:
                    errors.append(f"{region_prefix}.status 非法: {state}")
                    continue
                state_counts[state] += 1
                if (
                    isinstance(year, int)
                    and not isinstance(year, bool)
                    and exam_type in EXAM_TYPES
                    and region_code
                ):
                    event_states = scoped_event_states.get(
                        (year, exam_type, region_code), set()
                    )
                    required_review = {
                        "verified": "approved",
                        "candidate": "candidate",
                    }.get(state)
                    if required_review and required_review not in event_states:
                        errors.append(
                            f"{region_prefix} 标为 {state}，但没有对应的 "
                            f"{required_review} 事件"
                        )
            for state in COVERAGE_STATUSES:
                if state in counts and counts[state] != state_counts[state]:
                    errors.append(
                        f"{prefix}.{state} 与 regions 中的状态数量不一致"
                    )
            if exam_type in EXAM_TYPES and scope_mode in SCOPE_MODES:
                allowed_region_codes = set(scope_region_codes(exam_type, region_registry))
                if scope_mode == "full" and exam_type != "municipal":
                    if seen_region_codes != allowed_region_codes:
                        errors.append(
                            f"{prefix} 完整范围地区集合不正确；应包含 "
                            f"{len(allowed_region_codes)} 个地区"
                        )
                elif not seen_region_codes or not seen_region_codes <= allowed_region_codes:
                    errors.append(f"{prefix} 局部范围地区不适用于 {exam_type}")
    return errors


def command_validate(args: argparse.Namespace) -> int:
    dataset = load_json(Path(args.input))
    sources = load_json(Path(args.sources))
    errors = validate_dataset(dataset, sources)
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    print(f"验证通过: {len(dataset.get('exams', []))} 场考试")
    return 0


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def create_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Atomically reserve an immutable identifier before mutating other files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def event_map(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(event["id"]): event
        for exam in dataset.get("exams", [])
        for event in exam.get("events", [])
    }


def command_init(args: argparse.Namespace) -> int:
    sources = load_json(Path(args.sources))
    authorities = authorities_by_id(sources)
    regions = registry_regions(authorities)
    codes = scope_region_codes(args.exam_type, regions)
    if args.region:
        if args.region not in regions:
            print(f"ERROR 地区不在官方辖区表: {args.region}", file=sys.stderr)
            return 1
        if codes and args.region not in codes:
            print(
                f"ERROR 地区 {args.region} 不适用于 {args.exam_type}",
                file=sys.stderr,
            )
            return 1
        codes = [args.region]
    elif args.exam_type == "municipal":
        print("ERROR 独立市考初始化必须指定 --region", file=sys.stderr)
        return 1
    if not codes:
        print(f"ERROR 官方辖区表中没有可用于 {args.exam_type} 的地区", file=sys.stderr)
        return 1

    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    baseline_count = len(regions)
    payload = {
        "schema_version": 1,
        "generated_at": checked_at,
        "coverage": {
            "expected": baseline_count,
            "verified": 0,
            "pending": baseline_count,
            "not_found": 0,
            "checked_at": checked_at,
        },
        "coverage_scopes": [
            {
                "recruitment_year": args.year,
                "exam_type": args.exam_type,
                "scope_mode": "partial" if args.region else "full",
                "expected": len(codes),
                "verified": 0,
                "candidate": 0,
                "pending": len(codes),
                "not_found": 0,
                "checked_at": checked_at,
                "regions": [
                    {
                        "region_code": code,
                        "region_name": regions[code],
                        "status": "pending",
                    }
                    for code in codes
                ],
            }
        ],
        "exams": [],
    }
    output_path = Path(args.output)
    try:
        create_json_exclusive(output_path, payload)
    except FileExistsError:
        print(f"ERROR 初始化文件已存在，不能覆盖: {output_path}", file=sys.stderr)
        return 1
    print(
        f"已初始化: {args.year} / {args.exam_type} / {len(codes)} 个地区 -> {output_path}"
    )
    return 0


def event_fingerprint(event: dict[str, Any]) -> dict[str, Any]:
    source = event.get("source", {})
    return {
        "stage": event.get("stage"),
        "label": event.get("label"),
        "start": event.get("start"),
        "end": event.get("end"),
        "precision": event.get("precision"),
        "status": event.get("status"),
        "source": {
            key: source.get(key)
            for key in (
                "authority_id",
                "title",
                "url",
                "publisher",
                "published_at",
                "evidence",
            )
        },
    }


def markdown_items(items: list[str]) -> list[str]:
    return [f"- `{item}`" for item in items] if items else ["- 无"]


def command_review(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidate)
    candidates = load_json(candidate_path)
    sources = load_json(Path(args.sources))
    errors = validate_dataset(candidates, sources)
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1

    published: dict[str, Any] = {"exams": []}
    if args.published and Path(args.published).exists():
        published = load_json(Path(args.published))
        published_errors = validate_dataset(published, sources)
        if published_errors:
            for error in published_errors:
                print(f"ERROR 现有正式数据校验失败: {error}", file=sys.stderr)
            return 1

    candidate_exams = {
        str(exam.get("id", "")): exam for exam in candidates.get("exams", [])
    }
    published_exam_ids = {
        str(exam.get("id", "")) for exam in published.get("exams", [])
    }
    new_exams = sorted(set(candidate_exams) - published_exam_ids)
    candidate_events = event_map(candidates)
    published_events = event_map(published)
    new_events = sorted(set(candidate_events) - set(published_events))
    changed_events = sorted(
        event_id
        for event_id in set(candidate_events) & set(published_events)
        if event_fingerprint(candidate_events[event_id])
        != event_fingerprint(published_events[event_id])
    )
    source_changes = sorted(
        event_id
        for event_id in set(candidate_events) & set(published_events)
        if candidate_events[event_id].get("source", {}).get("url")
        != published_events[event_id].get("source", {}).get("url")
    )

    gap_lines: list[str] = []
    for scope in candidates.get("coverage_scopes", []):
        gaps = [
            region
            for region in scope.get("regions", [])
            if region.get("status") in {"pending", "not_found"}
        ]
        if gaps:
            names = "、".join(
                f"{region.get('region_name')}（{region.get('status')}）"
                for region in gaps
            )
            gap_lines.append(
                f"- {scope.get('recruitment_year')} / {scope.get('exam_type')}: {names}"
            )
    if not gap_lines:
        gap_lines = ["- 无"]

    lines = [
        "# 候选更新审核",
        "",
        f"- 候选文件：`{candidate_path}`",
        f"- 官方白名单：`{Path(args.sources)}`",
        "- 注意：确定性脚本不检查网页可达性；Agent 和审核人仍须打开精确公告链接。",
        "",
        "## 新增考试",
        "",
        *markdown_items(new_exams),
        "",
        "## 新增事件",
        "",
        *markdown_items(new_events),
        "",
        "## 已有事件变更",
        "",
        *markdown_items(changed_events),
        "",
        "## 来源网址变更",
        "",
        *markdown_items(source_changes),
        "",
        "## 覆盖缺口",
        "",
        *gap_lines,
        "",
        "## 下一步",
        "",
        "请逐条打开官方链接并确认日期与来源。只有用户明确批准的事件才能改为 `approved` 后执行 `promote`。",
        "",
    ]
    report = "\n".join(lines)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(output_path)
        print(f"审核报告已生成: {output_path}")
    else:
        print(report, end="")
    return 0


def command_promote(args: argparse.Namespace) -> int:
    candidates = load_json(Path(args.input))
    sources = load_json(Path(args.sources))
    errors = validate_dataset(candidates, sources)
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1

    if not SAFE_REVIEW_ID.fullmatch(args.review_id):
        print(f"ERROR 审核批次 ID 格式非法: {args.review_id}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    history_path = Path(args.history)
    receipt_path = history_path / f"{args.review_id}.json"
    before_path = history_path / f"{args.review_id}-before.json"
    if receipt_path.exists() or before_path.exists():
        print(f"ERROR 审核批次已存在，不能覆盖: {args.review_id}", file=sys.stderr)
        return 1

    if output_path.exists():
        published = load_json(output_path)
        existing_errors = validate_dataset(published, sources)
        if existing_errors:
            for error in existing_errors:
                print(f"ERROR 现有正式数据校验失败: {error}", file=sys.stderr)
            return 1
    else:
        published = {
            key: deepcopy(value)
            for key, value in candidates.items()
            if key != "exams"
        }
        published["exams"] = []
    before = deepcopy(published)
    before_events = event_map(before)
    exams_by_id = {str(exam["id"]): exam for exam in published.get("exams", [])}

    skipped_unapproved: list[str] = []
    approved_ids: list[str] = []
    for key, value in candidates.items():
        if key not in ("exams", "generated_at", "coverage_scopes"):
            if key == "coverage" and output_path.exists() and candidates.get("coverage_scopes"):
                continue
            published[key] = deepcopy(value)
    if candidates.get("coverage_scopes") is not None:
        scopes_by_key = {
            (int(scope["recruitment_year"]), str(scope["exam_type"])): deepcopy(scope)
            for scope in published.get("coverage_scopes", [])
        }
        try:
            for scope in candidates.get("coverage_scopes", []):
                scope_key = (
                    int(scope["recruitment_year"]),
                    str(scope["exam_type"]),
                )
                scopes_by_key[scope_key] = merge_coverage_scope(
                    scopes_by_key.get(scope_key), scope
                )
        except ValueError as error:
            print(f"ERROR 覆盖范围晋升失败: {error}", file=sys.stderr)
            return 1
        published["coverage_scopes"] = [
            scopes_by_key[key] for key in sorted(scopes_by_key)
        ]
    for candidate_exam in candidates.get("exams", []):
        approved_events = []
        for event in candidate_exam.get("events", []):
            event_id = str(event.get("id", ""))
            if event.get("review", {}).get("status") == "approved":
                approved_events.append(deepcopy(event))
                approved_ids.append(event_id)
            else:
                skipped_unapproved.append(event_id)
        if not approved_events:
            continue

        exam_id = str(candidate_exam["id"])
        target_exam = exams_by_id.get(exam_id)
        if target_exam is None:
            target_exam = {key: deepcopy(value) for key, value in candidate_exam.items() if key != "events"}
            target_exam["events"] = []
            published.setdefault("exams", []).append(target_exam)
            exams_by_id[exam_id] = target_exam
        else:
            for key, value in candidate_exam.items():
                if key != "events":
                    target_exam[key] = deepcopy(value)

        events_by_id = {str(event["id"]): event for event in target_exam.get("events", [])}
        for approved_event in approved_events:
            event_id = str(approved_event["id"])
            if event_id in events_by_id:
                index = target_exam["events"].index(events_by_id[event_id])
                target_exam["events"][index] = approved_event
            else:
                target_exam.setdefault("events", []).append(approved_event)

    published["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    published["exams"] = sorted(published.get("exams", []), key=lambda item: str(item["id"]))
    for exam in published["exams"]:
        exam["events"] = sorted(
            exam.get("events", []), key=lambda item: (str(item.get("start", "")), str(item["id"]))
        )

    published_errors = validate_dataset(published, sources)
    if published_errors:
        for error in published_errors:
            print(f"ERROR 正式数据校验失败: {error}", file=sys.stderr)
        return 1

    after_events = event_map(published)
    added = sorted(event_id for event_id in approved_ids if event_id not in before_events)
    changed = sorted(
        event_id
        for event_id in approved_ids
        if event_id in before_events and before_events[event_id] != after_events[event_id]
    )
    receipt = {
        "schema_version": 1,
        "status": "completed",
        "review_id": args.review_id,
        "promoted_at": published["generated_at"],
        "input": str(Path(args.input)),
        "before_sha256": payload_hash(before),
        "after_sha256": payload_hash(published),
        "added": added,
        "changed": changed,
        "skipped_unapproved": sorted(skipped_unapproved),
        "output": str(output_path),
        "before_snapshot": str(before_path) if output_path.exists() else None,
    }

    history_path.mkdir(parents=True, exist_ok=True)
    reservation = {**receipt, "status": "pending"}
    try:
        create_json_exclusive(receipt_path, reservation)
    except FileExistsError:
        print(f"ERROR 审核批次已存在，不能覆盖: {args.review_id}", file=sys.stderr)
        return 1
    if output_path.exists():
        write_json(before_path, before)
    write_json(output_path, published)
    write_json(receipt_path, receipt)
    print(
        f"晋升完成: 新增 {len(added)}，变更 {len(changed)}，跳过未审核 {len(skipped_unapproved)}"
    )
    return 0


def command_build(args: argparse.Namespace) -> int:
    data_path = Path(args.data)
    assets_path = Path(args.assets)
    output_path = Path(args.output)
    dataset = load_json(data_path)
    sources = load_json(Path(args.sources))
    errors = validate_dataset(dataset, sources)
    if errors:
        raise ValueError("；".join(errors))
    partial_scopes = [
        f"{scope.get('recruitment_year')}/{scope.get('exam_type')}"
        for scope in dataset.get("coverage_scopes", [])
        if scope.get("scope_mode", "full") == "partial"
    ]
    if partial_scopes:
        raise ValueError(
            "局部覆盖范围不能直接构建网站，必须先合并到完整范围: "
            + ", ".join(partial_scopes)
        )
    unapproved = [
        str(event.get("id", "unknown"))
        for exam in dataset.get("exams", [])
        for event in exam.get("events", [])
        if event.get("review", {}).get("status") != "approved"
    ]
    if unapproved:
        raise ValueError(
            "只允许构建已批准事件: " + ", ".join(sorted(unapproved))
        )
    required_assets = ("index.html", "styles.css", "app.js")
    missing = [name for name in required_assets if not (assets_path / name).is_file()]
    if missing:
        raise ValueError(f"网站模板缺少文件: {', '.join(missing)}")

    output_path.mkdir(parents=True, exist_ok=True)
    embedded_data = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    index_template = (assets_path / "index.html").read_text(encoding="utf-8")
    marker = "__SHANGAN_DATA__"
    if marker not in index_template:
        raise ValueError(f"网站模板缺少数据占位符: {marker}")
    (output_path / "index.html").write_text(
        index_template.replace(marker, embedded_data), encoding="utf-8"
    )
    for name in ("styles.css", "app.js"):
        shutil.copyfile(assets_path / name, output_path / name)
    write_json(output_path / "data.json", dataset)
    print(
        f"网站已生成: {output_path}（{len(dataset.get('exams', []))} 场考试）"
    )
    return 0


SKILL_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shangan-schedule",
        description="校验、审核并生成考公时间表",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="校验候选或正式数据")
    validate.add_argument("--input", required=True, help="待校验 JSON")
    validate.add_argument("--sources", default=str(SKILL_ROOT / "references" / "official-sources.json"), help="官方来源白名单 JSON；默认使用 Skill 内置文件")
    validate.set_defaults(handler=command_validate)

    initialize = subparsers.add_parser("init", help="初始化一个年度与考试类型的待核查矩阵")
    initialize.add_argument("--year", required=True, type=int, help="招录年度")
    initialize.add_argument(
        "--type", dest="exam_type", required=True, choices=sorted(EXAM_TYPES),
        help="考试类型",
    )
    initialize.add_argument("--region", help="可选：只初始化一个官方辖区代码")
    initialize.add_argument("--sources", default=str(SKILL_ROOT / "references" / "official-sources.json"), help="官方来源白名单 JSON；默认使用 Skill 内置文件")
    initialize.add_argument("--output", required=True, help="新的候选数据 JSON")
    initialize.set_defaults(handler=command_init)

    review = subparsers.add_parser("review", help="生成候选数据与正式数据的审核差异")
    review.add_argument("--candidate", required=True, help="候选数据 JSON")
    review.add_argument("--published", help="可选：现有正式数据 JSON")
    review.add_argument("--sources", default=str(SKILL_ROOT / "references" / "official-sources.json"), help="官方来源白名单 JSON；默认使用 Skill 内置文件")
    review.add_argument("--output", help="可选：审核报告 Markdown；默认打印到终端")
    review.set_defaults(handler=command_review)

    promote = subparsers.add_parser("promote", help="将已审核候选项晋升为正式数据")
    promote.add_argument("--input", required=True, help="候选数据 JSON")
    promote.add_argument("--sources", default=str(SKILL_ROOT / "references" / "official-sources.json"), help="官方来源白名单 JSON；默认使用 Skill 内置文件")
    promote.add_argument("--output", required=True, help="正式数据 JSON")
    promote.add_argument("--history", required=True, help="不可变审核历史目录")
    promote.add_argument("--review-id", required=True, help="唯一审核批次 ID")
    promote.set_defaults(handler=command_promote)

    build = subparsers.add_parser("build", help="从正式数据生成静态网站")
    build.add_argument("--data", required=True, help="正式数据 JSON")
    build.add_argument("--sources", default=str(SKILL_ROOT / "references" / "official-sources.json"), help="官方来源白名单 JSON；默认使用 Skill 内置文件")
    build.add_argument("--assets", default=str(SKILL_ROOT / "assets" / "site"), help="静态网站模板目录；默认使用 Skill 内置模板")
    build.add_argument("--output", required=True, help="生成目录")
    build.set_defaults(handler=command_build)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
