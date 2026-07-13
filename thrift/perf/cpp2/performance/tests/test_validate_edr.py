#!/usr/bin/env python3

import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "validate_edr.py"


class ValidateEdrCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.folly = self.root / "folly"
        self.fbthrift = self.root / "fbthrift"
        self.performance = (
            self.fbthrift / "thrift" / "perf" / "cpp2" / "performance"
        )
        self.edr_dir = self.performance / "edr"
        self.edr_dir.mkdir(parents=True)
        self.folly_sha = self._init_git_repo(self.folly)
        self.fbthrift_sha = self._init_git_repo(self.fbthrift)
        self._write_routes()
        self._write(self.performance / "AGENT_PROTOCOL.md", "# Protocol\n")
        self._write(self.performance / "evidence.md", "# Evidence\n")
        self._write_edr("EDR-0001", "valid", status="REJECTED_IN_ENVELOPE")
        self._write_frontier(["EDR-0001-valid.md"])

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--performance-dir",
                str(self.performance),
                "--folly",
                str(self.folly),
                "--fbthrift",
                str(self.fbthrift),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def _init_git_repo(self, path: pathlib.Path) -> str:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "EDR Test"],
            check=True,
        )
        self._write(path / "seed.txt", "seed\n")
        subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(path), "commit", "-qm", "seed"], check=True
        )
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def _write(self, path: pathlib.Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_routes(self) -> None:
        fb_route = (
            "Read thrift/perf/cpp2/performance/AGENT_PROTOCOL.md before work.\n"
        )
        folly_route = (
            "Read ../fbthrift/thrift/perf/cpp2/performance/AGENT_PROTOCOL.md.\n"
        )
        for name in ("AGENTS.md", "CLAUDE.md"):
            self._write(self.fbthrift / name, fb_route)
            self._write(self.folly / name, folly_route)

    def _write_frontier(self, filenames: list[str]) -> None:
        links = "\n".join(
            f"- [{filename}](edr/{filename})" for filename in filenames
        )
        self._write(self.performance / "FRONTIER.md", f"# Frontier\n\n{links}\n")

    def _write_edr(
        self,
        edr_id: str,
        slug: str,
        *,
        status: str,
        include_reopen_if: bool = True,
        folly_commit: str | None = None,
        supersedes: tuple[str, ...] = (),
        superseded_by: tuple[str, ...] = (),
    ) -> pathlib.Path:
        reopen = (
            "reopen_if:\n  - environment changes materially\n"
            if include_reopen_if
            else ""
        )
        supersedes_text = self._yaml_list("supersedes", supersedes)
        superseded_by_text = self._yaml_list("superseded_by", superseded_by)
        content = (
            "---\n"
            f"id: {edr_id}\n"
            "title: Test experiment\n"
            f"status: {status}\n"
            "created: 2026-07-13\n"
            "updated: 2026-07-13\n"
            "folly_branch: test\n"
            f"folly_commit: {folly_commit or self.folly_sha}\n"
            "fbthrift_branch: test\n"
            f"fbthrift_commit: {self.fbthrift_sha}\n"
            "components:\n"
            "  - TestComponent\n"
            "files:\n"
            "  - path/to/file.cpp\n"
            "mechanisms:\n"
            "  - test mechanism\n"
            "metrics:\n"
            "  - qps\n"
            "workloads:\n"
            "  - sum\n"
            "evidence:\n"
            "  - ../evidence.md\n"
            f"{reopen}{supersedes_text}{superseded_by_text}"
            "---\n\n"
            f"# {edr_id}: Test experiment\n"
        )
        path = self.edr_dir / f"{edr_id}-{slug}.md"
        self._write(path, content)
        return path

    @staticmethod
    def _yaml_list(key: str, values: tuple[str, ...]) -> str:
        if not values:
            return f"{key}: []\n"
        items = "".join(f"  - {value}\n" for value in values)
        return f"{key}:\n{items}"

    def test_valid_repository_passes(self) -> None:
        result = self._run()
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("validated 1 EDR(s)", result.stdout)

    def test_missing_reopen_if_fails(self) -> None:
        self._write_edr(
            "EDR-0001",
            "valid",
            status="REJECTED_IN_ENVELOPE",
            include_reopen_if=False,
        )
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("reopen_if", result.stdout)

    def test_duplicate_id_fails(self) -> None:
        self._write_edr("EDR-0001", "duplicate", status="PROPOSED")
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("duplicate EDR id EDR-0001", result.stdout)

    def test_broken_frontier_link_and_unindexed_terminal_edr_fail(self) -> None:
        self._write_frontier(["EDR-9999-missing.md"])
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("broken Markdown link", result.stdout)
        self.assertIn("terminal EDR EDR-0001 is not indexed", result.stdout)

    def test_asymmetric_supersede_relation_fails(self) -> None:
        self._write_edr(
            "EDR-0001",
            "valid",
            status="SUPERSEDED",
            superseded_by=("EDR-0002",),
        )
        self._write_edr("EDR-0002", "replacement", status="SUPPORTED")
        self._write_frontier(["EDR-0001-valid.md", "EDR-0002-replacement.md"])
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("does not list EDR-0001 in supersedes", result.stdout)

    def test_unresolvable_commit_fails(self) -> None:
        self._write_edr(
            "EDR-0001",
            "valid",
            status="REJECTED_IN_ENVELOPE",
            folly_commit="0" * 40,
        )
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("folly_commit does not resolve", result.stdout)

    def test_missing_agent_route_fails(self) -> None:
        (self.folly / "AGENTS.md").unlink()
        result = self._run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("folly/AGENTS.md is missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
