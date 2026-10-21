"""Command line seam: drive ``chromedbpy`` in-process and assert on stdout."""

from __future__ import annotations

import sys

import pytest

from chromedbpy.cli import main
from profile_fixture import ProfileFixture


def test_cookies_mode_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("BROWSER_PASSWORD", "correct horse battery staple")

    with ProfileFixture(password="correct horse battery staple") as fixture:
        exit_code = main(["-p", str(fixture.path), "-c"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""


def test_local_storage_mode_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")

    exit_code = main(["-p", str(tmp_path), "-ls"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""


def test_no_mode_prints_usage_and_fails(tmp_path, capsys):
    exit_code = main(["-p", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code != 0
    lines = captured.out.splitlines()
    assert lines[0] == "Error: Please specify exactly one of -c or -ls"
    assert lines[1] == "Usage of chromedbpy:"


def test_both_modes_print_usage_and_fail(tmp_path, capsys):
    exit_code = main(["-p", str(tmp_path), "-c", "-ls"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "exactly one of -c or -ls" in captured.out
    assert "Usage of chromedbpy" in captured.out


def test_missing_profile_path_prints_usage_and_fails(capsys):
    exit_code = main(["-c"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "path to browser profile directory" in captured.out
    assert "Usage of chromedbpy" in captured.out


@pytest.mark.parametrize("mode", ["-c", "-ls"])
def test_non_macos_platform_is_unsupported(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setattr(sys, "platform", "linux")

    exit_code = main(["-p", str(tmp_path), mode])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "macOS" in captured.out


def test_session_storage_flag_is_absent(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["-p", str(tmp_path), "-ss"])

    captured = capsys.readouterr()
    assert excinfo.value.code != 0
    assert "-ss" in captured.err