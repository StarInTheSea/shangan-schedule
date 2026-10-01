"""Shared helpers for binding official hosts to declared authorities."""

from __future__ import annotations

from typing import Any


def authority_records(registry: dict[str, Any]) -> list[dict[str, Any]]:
    authorities = registry.get("authorities", [])
    if not isinstance(authorities, list):
        return []
    return [item for item in authorities if isinstance(item, dict)]


def allowed_hosts(registry: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for authority in authority_records(registry):
        authority_hosts = authority.get("allowed_hosts", [])
        if not isinstance(authority_hosts, list):
            continue
        hosts.update(
            str(host).lower().rstrip(".")
            for host in authority_hosts
            if str(host).strip()
        )
    return hosts


def authorities_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(authority.get("id", "")): authority
        for authority in authority_records(registry)
        if str(authority.get("id", ""))
    }


def host_is_allowed(host: str, whitelist: set[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == item or normalized.endswith(f".{item}")
        for item in whitelist
    )
