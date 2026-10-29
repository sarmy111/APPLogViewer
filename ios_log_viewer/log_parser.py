from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from .models import AppInfo, LogEntry


_TIMESTAMP_PATTERNS = (
    re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"),
    re.compile(
        r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    ),
)
_PROCESS_PATTERN = re.compile(
    r"(?<!\S)(?P<process>[A-Za-z0-9_.+\-/]+)\[(?P<pid>\d+)\](?=\s|:|<|$)"
)
_ERROR_PATTERN = re.compile(
    r"(\b(?:error|exception|fatal|crash(?:ed)?|abort(?:ed)?|assert(?:ion)?\s+failed)\b"
    r"|LogError|NullReferenceException|EXC_BAD_ACCESS)",
    re.IGNORECASE,
)
_WARNING_PATTERN = re.compile(r"(\bwarn(?:ing)?\b|LogWarning)", re.IGNORECASE)

_SYSTEM_PROCESSES = {
    "backboardd",
    "configd",
    "kernel",
    "launchd",
    "lockdownd",
    "mobilemail",
    "powerd",
    "runningboardd",
    "SpringBoard",
    "syslogd",
    "UserEventAgent",
    "wifid",
}


def infer_log_level(text: str) -> str:
    if _ERROR_PATTERN.search(text):
        return "ERROR"
    if _WARNING_PATTERN.search(text):
        return "WARNING"
    return "INFO"


def parse_log_line(line: str, fallback_process: str = "") -> LogEntry:
    raw = line.rstrip("\r\n")
    timestamp = ""
    remainder = raw
    for pattern in _TIMESTAMP_PATTERNS:
        match = pattern.match(raw)
        if match:
            timestamp = match.group("timestamp")
            remainder = raw[match.end() :]
            break

    process_match = _PROCESS_PATTERN.search(remainder)
    process = process_match.group("process") if process_match else fallback_process
    return LogEntry(
        timestamp=timestamp,
        process=process,
        raw=raw,
        level=infer_log_level(raw),
    )


def parse_android_log_line(line: str, fallback_process: str = "") -> LogEntry:
    raw = line.rstrip("\r\n")
    pattern = re.compile(
        r"^\s*(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})"
        r"\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<priority>[VDIWEFA])"
        r"\s+(?P<tag>.*?)\s*:\s?(?P<message>.*)$"
    )
    match = pattern.match(raw)
    if not match:
        return LogEntry(
            timestamp="",
            process=fallback_process,
            raw=raw,
            level=infer_log_level(raw),
        )
    priority = match.group("priority")
    level = "ERROR" if priority in {"E", "F", "A"} else (
        "WARNING" if priority == "W" else "INFO"
    )
    return LogEntry(
        timestamp=match.group("timestamp"),
        process=fallback_process,
        raw=raw,
        level=level,
    )


def parse_process_list(output: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        name = ""
        match = re.match(r"^\d+\s+(.+?)$", stripped)
        if match:
            name = match.group(1)
        else:
            match = re.match(r"^(.+?)\s+\((\d+)\)$", stripped)
            if match:
                name = match.group(1)
            else:
                match = re.match(r"^(.+?)\s+\d+$", stripped)
                if match:
                    name = match.group(1)
        name = re.sub(r"\s+\(\d+\)$", "", name)
        name = re.sub(r"\s+\d+$", "", name)
        name = name.strip().strip("\"'")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def rank_process_candidates(
    app: AppInfo,
    process_names: Iterable[str],
    include_unmatched: bool = False,
) -> list[str]:
    executable = _normalize(app.process_name)
    display = _normalize(app.display_name)
    bundle_tail = _normalize(app.app_id.rsplit(".", 1)[-1])
    targets = [value for value in (executable, display, bundle_tail) if value]

    scored: list[tuple[int, str]] = []
    unmatched: list[str] = []
    for name in process_names:
        normalized = _normalize(name)
        if not normalized:
            continue
        score = 0
        for index, target in enumerate(targets):
            if normalized == target:
                score = max(score, 1000 - index * 50)
            elif target and (normalized.startswith(target) or target.startswith(normalized)):
                score = max(score, 700 - index * 50)
            elif target and target in normalized:
                score = max(score, 500 - index * 50)
            elif target:
                ratio = SequenceMatcher(None, normalized, target).ratio()
                if ratio >= 0.72:
                    score = max(score, int(ratio * 400))
        if score:
            scored.append((score, name))
        elif name.casefold() not in {value.casefold() for value in _SYSTEM_PROCESSES}:
            unmatched.append(name)

    scored.sort(key=lambda item: (-item[0], item[1].casefold()))
    result = [name for _, name in scored]
    if include_unmatched and not result:
        result.extend(sorted(unmatched, key=str.casefold))
    return result


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
