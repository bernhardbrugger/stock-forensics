"""The ``forensics`` command line, end to end, with Apple's recorded data as fake SEC."""

from __future__ import annotations

import functools
import os
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from stock_forensics import cli
from stock_forensics.cli import OutputFormat, app, default_output_path, load_env_file
from stock_forensics.providers.sec_edgar import SecEdgarClient
from stock_forensics.report import DISCLAIMER, ForensicReport, analyze
from tests.report_fixtures import AS_OF, FakeSec, make_client

runner = CliRunner()


@pytest.fixture
def fake_sec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Work in an empty folder, answer with the fake SEC and pin the analysis date."""
    monkeypatch.chdir(tmp_path)
    state: dict[str, object] = {"fake": FakeSec(), "no_cache": None}

    def build(*, no_cache: bool) -> SecEdgarClient:
        state["no_cache"] = no_cache
        fake = state["fake"]
        assert callable(fake)
        return make_client(tmp_path / "cache", fake)

    monkeypatch.setattr(cli, "build_client", build)
    monkeypatch.setattr(cli, "analyze", functools.partial(analyze, as_of=AS_OF))
    return state


def test_default_run_writes_an_html_report(fake_sec: dict[str, object], tmp_path: Path) -> None:
    result = runner.invoke(app, ["AAPL"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout == ""
    report = tmp_path / "AAPL-forensics.html"
    assert report.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert "Apple Inc." in report.read_text(encoding="utf-8")
    for step in range(1, 8):
        assert f"[{step}/7]" in result.stderr
    assert "Caution" in result.stderr
    assert "AAPL-forensics.html" in result.stderr
    assert "Not financial advice" in result.stderr  # the note is on screen, not only in the file
    assert fake_sec["no_cache"] is False


def test_ticker_is_case_insensitive(fake_sec: dict[str, object], tmp_path: Path) -> None:
    result = runner.invoke(app, ["aapl"])
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "AAPL-forensics.html").exists()


@pytest.mark.parametrize(
    ("fmt", "ext", "start"), [("md", "md", "# Apple Inc."), ("json", "json", "{")]
)
def test_other_formats(
    fake_sec: dict[str, object], tmp_path: Path, fmt: str, ext: str, start: str
) -> None:
    result = runner.invoke(app, ["AAPL", "--format", fmt])
    assert result.exit_code == 0, result.stderr
    text = (tmp_path / f"AAPL-forensics.{ext}").read_text(encoding="utf-8")
    assert text.startswith(start)
    assert DISCLAIMER in text


def test_json_output_is_the_complete_report(fake_sec: dict[str, object], tmp_path: Path) -> None:
    out = tmp_path / "nested.json"
    result = runner.invoke(app, ["AAPL", "-f", "json", "-o", str(out)])
    assert result.exit_code == 0, result.stderr
    loaded = ForensicReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert loaded.ticker == "AAPL"
    assert loaded.scores is not None and loaded.insiders is not None


def test_out_dash_writes_the_report_to_stdout(fake_sec: dict[str, object], tmp_path: Path) -> None:
    result = runner.invoke(app, ["AAPL", "-f", "md", "-o", "-"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("# Apple Inc. (AAPL)")
    assert "[1/7]" in result.stderr and "[1/7]" not in result.stdout
    assert not list(tmp_path.glob("AAPL-forensics.*"))


def test_no_cache_flag_reaches_the_client(fake_sec: dict[str, object]) -> None:
    result = runner.invoke(app, ["AAPL", "--no-cache", "-f", "json", "-o", "-"])
    assert result.exit_code == 0, result.stderr
    assert fake_sec["no_cache"] is True


def test_unknown_ticker_fails_with_a_clear_message(
    fake_sec: dict[str, object], tmp_path: Path
) -> None:
    result = runner.invoke(app, ["NOSUCH"])
    assert result.exit_code == 1
    assert "NOSUCH" in result.stderr and "not in the SEC ticker list" in result.stderr
    assert not list(tmp_path.glob("*-forensics.*"))


def test_sec_outage_fails_cleanly(fake_sec: dict[str, object]) -> None:
    fake_sec["fake"] = FakeSec(fail={"/submissions/": 503})
    result = runner.invoke(app, ["AAPL"])
    assert result.exit_code == 1
    assert "Could not load data from the SEC" in result.stderr
    assert "Traceback" not in result.stderr


def test_partial_failure_still_delivers_the_report(
    fake_sec: dict[str, object], tmp_path: Path
) -> None:
    fake_sec["fake"] = FakeSec(fail={"companyfacts": 500})
    result = runner.invoke(app, ["AAPL"])
    assert result.exit_code == 0, result.stderr
    assert "Missing sections: metrics, scores, statements" in result.stderr
    html = (tmp_path / "AAPL-forensics.html").read_text(encoding="utf-8")
    assert "Some parts of this report are missing" in html


def test_unwritable_output_fails_cleanly(fake_sec: dict[str, object], tmp_path: Path) -> None:
    result = runner.invoke(app, ["AAPL", "-o", str(tmp_path)])  # a folder, not a file
    assert result.exit_code == 1
    assert "Could not write" in result.stderr


def test_missing_user_agent_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    result = runner.invoke(app, ["AAPL"])
    assert result.exit_code == 2
    assert "SEC_USER_AGENT" in result.stderr
    assert ".env" in result.stderr


def test_user_agent_is_read_from_dot_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})  # isolated copy, restored after the test
    (tmp_path / ".env").write_text(
        'SEC_USER_AGENT="forensics-test tests@example.invalid"\n', "utf-8"
    )
    seen: list[str | None] = []

    def build(*, no_cache: bool) -> SecEdgarClient:
        seen.append(os.environ.get("SEC_USER_AGENT"))
        raise httpx.ConnectError("stop here")

    monkeypatch.setattr(cli, "build_client", build)
    result = runner.invoke(app, ["AAPL"])
    assert seen == ["forensics-test tests@example.invalid"]
    assert result.exit_code != 0  # the fake client factory aborts on purpose


def test_empty_ticker_is_a_usage_error(fake_sec: dict[str, object]) -> None:
    result = runner.invoke(app, ["  "])
    assert result.exit_code == 2


def test_version_and_help(fake_sec: dict[str, object]) -> None:
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0 and version.stdout.startswith("stock-forensics ")
    help_ = runner.invoke(app, ["--help"])
    assert help_.exit_code == 0
    for word in ("--format", "--out", "--no-cache", "Not financial advice"):
        assert word in help_.stdout


def test_default_output_path() -> None:
    assert default_output_path("brk.b", OutputFormat.MD) == Path("BRK-B-forensics.md")


def test_env_file_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "environ", {"ALREADY": "set"})
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n\nPLAIN=value\nexport EXPORTED=yes\nQUOTED="two words"\n'
        "SINGLE='x y'\nALREADY=overwritten\nNOEQUALS\nEMPTY=\n  SPACED = padded  \n",
        encoding="utf-8",
    )
    load_env_file(env)
    assert os.environ == {
        "ALREADY": "set",  # a variable that is already set always wins
        "PLAIN": "value",
        "EXPORTED": "yes",
        "QUOTED": "two words",
        "SINGLE": "x y",
        "EMPTY": "",
        "SPACED": "padded",
    }


def test_missing_env_file_is_fine(tmp_path: Path) -> None:
    load_env_file(tmp_path / "nope.env")
