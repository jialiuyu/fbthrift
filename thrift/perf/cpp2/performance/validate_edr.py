#!/usr/bin/env python3

"""Validate the cross-repository performance EDR knowledge base."""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from typing import Iterable


ALLOWED_STATUSES = {
    "PROPOSED",
    "RUNNING",
    "SUPPORTED",
    "REJECTED_IN_ENVELOPE",
    "CONTEXT_DEPENDENT",
    "INCONCLUSIVE",
    "SUPERSEDED",
}

TERMINAL_STATUSES = ALLOWED_STATUSES - {"PROPOSED", "RUNNING"}

REQUIRED_SCALARS = {
    "id",
    "title",
    "status",
    "created",
    "updated",
    "folly_branch",
    "folly_commit",
    "fbthrift_branch",
    "fbthrift_commit",
}

REQUIRED_LISTS = {
    "components",
    "files",
    "mechanisms",
    "metrics",
    "workloads",
    "evidence",
    "reopen_if",
    "supersedes",
    "superseded_by",
}

NONEMPTY_LISTS = {
    "components",
    "files",
    "mechanisms",
    "metrics",
    "workloads",
    "evidence",
}

EDR_ID_RE = re.compile(r"^EDR-\d{4}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


class FrontmatterError(ValueError):
    pass


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_frontmatter(path: pathlib.Path) -> dict[str, object]:
    """Parse the flat YAML subset used by EDR Markdown files."""

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise FrontmatterError(f"{path}: frontmatter must start with ---")

    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise FrontmatterError(f"{path}: frontmatter closing --- is missing") from error

    data: dict[str, object] = {}
    current_list: str | None = None
    for lineno, raw_line in enumerate(lines[1:end], start=2):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith("  - "):
            if current_list is None:
                raise FrontmatterError(
                    f"{path}:{lineno}: list item has no parent field"
                )
            item = _unquote(raw_line[4:].strip())
            if not item:
                raise FrontmatterError(f"{path}:{lineno}: list item is empty")
            value = data[current_list]
            if not isinstance(value, list):
                raise FrontmatterError(
                    f"{path}:{lineno}: {current_list} is not a list"
                )
            value.append(item)
            continue

        if raw_line.startswith((" ", "\t")):
            raise FrontmatterError(
                f"{path}:{lineno}: nested mappings are not supported"
            )
        if ":" not in raw_line:
            raise FrontmatterError(f"{path}:{lineno}: expected key: value")

        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise FrontmatterError(f"{path}:{lineno}: field name is empty")
        if key in data:
            raise FrontmatterError(f"{path}:{lineno}: duplicate field {key}")

        if raw_value in {"", "[]"}:
            data[key] = []
            current_list = key if raw_value == "" else None
        else:
            data[key] = _unquote(raw_value)
            current_list = None

    return data


def _relative(path: pathlib.Path, base: pathlib.Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _validate_fields(
    path: pathlib.Path,
    data: dict[str, object],
    performance_dir: pathlib.Path,
) -> list[str]:
    errors: list[str] = []
    label = _relative(path, performance_dir)

    for field in sorted(REQUIRED_SCALARS):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: missing or empty required field {field}")

    for field in sorted(REQUIRED_LISTS):
        value = data.get(field)
        if not isinstance(value, list):
            errors.append(f"{label}: missing required list field {field}")
        elif field in NONEMPTY_LISTS and not value:
            errors.append(f"{label}: required list field {field} is empty")

    edr_id = data.get("id")
    if isinstance(edr_id, str):
        if not EDR_ID_RE.fullmatch(edr_id):
            errors.append(f"{label}: invalid EDR id {edr_id}")
        expected_prefix = f"{edr_id}-"
        if not path.name.startswith(expected_prefix):
            errors.append(f"{label}: filename does not start with {expected_prefix}")

    status = data.get("status")
    if isinstance(status, str):
        if status not in ALLOWED_STATUSES:
            errors.append(f"{label}: invalid status {status}")
        elif status in TERMINAL_STATUSES:
            reopen_if = data.get("reopen_if")
            if not isinstance(reopen_if, list) or not reopen_if:
                errors.append(f"{label}: terminal status requires non-empty reopen_if")

    for field in ("folly_commit", "fbthrift_commit"):
        value = data.get(field)
        if isinstance(value, str) and not FULL_SHA_RE.fullmatch(value):
            errors.append(f"{label}: {field} must be a full 40-character SHA")

    evidence = data.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, str) or _is_external_link(item):
                continue
            target = (path.parent / item.split("#", 1)[0]).resolve()
            if not target.exists():
                errors.append(f"{label}: evidence path does not exist: {item}")

    return errors


def _is_external_link(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))


def _markdown_link_errors(performance_dir: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(performance_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK_RE.findall(text):
            target = target.strip().split()[0].strip("<>")
            if _is_external_link(target):
                continue
            relative_target = target.split("#", 1)[0]
            if not relative_target:
                continue
            resolved = (path.parent / relative_target).resolve()
            if not resolved.exists():
                label = _relative(path, performance_dir)
                errors.append(f"{label}: broken Markdown link: {target}")
    return errors


def _commit_resolves(repo: pathlib.Path, commit: str) -> bool:
    if not repo.is_dir() or not FULL_SHA_RE.fullmatch(commit):
        return False
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _route_errors(repo: pathlib.Path, repo_name: str) -> list[str]:
    errors: list[str] = []
    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = repo / filename
        label = f"{repo_name}/{filename}"
        if not path.is_file():
            errors.append(f"{label} is missing")
            continue
        if "AGENT_PROTOCOL.md" not in path.read_text(encoding="utf-8"):
            errors.append(f"{label} does not route to AGENT_PROTOCOL.md")
    return errors


def _as_id_list(data: dict[str, object], field: str) -> list[str]:
    value = data.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _relation_errors(records: dict[str, dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for edr_id, data in sorted(records.items()):
        for old_id in _as_id_list(data, "supersedes"):
            old = records.get(old_id)
            if old is None:
                errors.append(f"{edr_id}: supersedes references missing {old_id}")
            elif edr_id not in _as_id_list(old, "superseded_by"):
                errors.append(
                    f"{old_id} does not list {edr_id} in superseded_by"
                )
        for new_id in _as_id_list(data, "superseded_by"):
            new = records.get(new_id)
            if new is None:
                errors.append(f"{edr_id}: superseded_by references missing {new_id}")
            elif edr_id not in _as_id_list(new, "supersedes"):
                errors.append(f"{new_id} does not list {edr_id} in supersedes")
    return errors


def _frontier_errors(
    performance_dir: pathlib.Path,
    records: dict[str, dict[str, object]],
    paths_by_id: dict[str, pathlib.Path],
) -> list[str]:
    frontier = performance_dir / "FRONTIER.md"
    if not frontier.is_file():
        return ["FRONTIER.md is missing"]

    text = frontier.read_text(encoding="utf-8")
    indexed_names = {
        pathlib.PurePosixPath(target.split("#", 1)[0]).name
        for target in MARKDOWN_LINK_RE.findall(text)
        if target.startswith("edr/")
    }
    errors: list[str] = []
    for edr_id, data in sorted(records.items()):
        if data.get("status") in TERMINAL_STATUSES:
            path = paths_by_id[edr_id]
            if path.name not in indexed_names:
                errors.append(f"terminal EDR {edr_id} is not indexed by FRONTIER.md")
    return errors


def _folly_copy_errors(folly_repo: pathlib.Path) -> list[str]:
    errors: list[str] = []
    docs = folly_repo / "folly" / "docs"
    if not docs.is_dir():
        return errors
    for path in sorted(docs.rglob("EDR-*.md")):
        errors.append(f"folly contains an EDR copy: {path}")
    for path in sorted(docs.rglob("FRONTIER.md")):
        errors.append(f"folly contains a Frontier copy: {path}")
    return errors


def validate_repository(
    performance_dir: pathlib.Path,
    folly_repo: pathlib.Path,
    fbthrift_repo: pathlib.Path,
) -> list[str]:
    """Return a stable list of validation errors for a paired worktree."""

    performance_dir = performance_dir.resolve()
    folly_repo = folly_repo.resolve()
    fbthrift_repo = fbthrift_repo.resolve()
    errors: list[str] = []

    errors.extend(_route_errors(folly_repo, "folly"))
    errors.extend(_route_errors(fbthrift_repo, "fbthrift"))
    errors.extend(_folly_copy_errors(folly_repo))

    protocol = performance_dir / "AGENT_PROTOCOL.md"
    if not protocol.is_file():
        errors.append("AGENT_PROTOCOL.md is missing")

    edr_dir = performance_dir / "edr"
    if not edr_dir.is_dir():
        errors.append("edr directory is missing")
        return sorted(set(errors))

    records: dict[str, dict[str, object]] = {}
    paths_by_id: dict[str, pathlib.Path] = {}
    for path in sorted(edr_dir.glob("EDR-*.md")):
        try:
            data = parse_frontmatter(path)
        except (OSError, FrontmatterError) as error:
            errors.append(str(error))
            continue
        errors.extend(_validate_fields(path, data, performance_dir))
        edr_id = data.get("id")
        if not isinstance(edr_id, str):
            continue
        if edr_id in records:
            errors.append(f"duplicate EDR id {edr_id}")
            continue
        records[edr_id] = data
        paths_by_id[edr_id] = path

    for edr_id, data in sorted(records.items()):
        for field, repo in (
            ("folly_commit", folly_repo),
            ("fbthrift_commit", fbthrift_repo),
        ):
            commit = data.get(field)
            if isinstance(commit, str) and not _commit_resolves(repo, commit):
                errors.append(
                    f"{edr_id}: {field} does not resolve in {repo}: {commit}"
                )

    errors.extend(_relation_errors(records))
    errors.extend(_frontier_errors(performance_dir, records, paths_by_id))
    errors.extend(_markdown_link_errors(performance_dir))
    return sorted(set(errors))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--performance-dir", required=True, type=pathlib.Path)
    parser.add_argument("--folly", required=True, type=pathlib.Path)
    parser.add_argument("--fbthrift", required=True, type=pathlib.Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    errors = validate_repository(args.performance_dir, args.folly, args.fbthrift)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    edr_count = len(list((args.performance_dir / "edr").glob("EDR-*.md")))
    print(f"validated {edr_count} EDR(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
