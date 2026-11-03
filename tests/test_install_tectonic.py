"""Tests for the Tectonic installer: no network, no download, no engine needed."""

import io
import os
import pathlib
import sys
import tarfile
import types
import zipfile

import pytest

import install_tectonic
import server


ROOT = pathlib.Path(__file__).resolve().parents[1]

RELEASE = {
    "tag_name": "tectonic@0.17.0",
    "assets": [
        {
            "name": "tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
            "browser_download_url": "https://example.invalid/tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
        },
        {
            "name": "tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
            "browser_download_url": "https://example.invalid/tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
        },
        {
            "name": "tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
            "browser_download_url": "https://example.invalid/tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
        },
        {"name": "notes.txt", "browser_download_url": "https://example.invalid/notes.txt"},
    ],
}


def pretend_system(monkeypatch, platform_name, machine):
    """Point the installer at another platform without touching the real sys."""
    monkeypatch.setattr(install_tectonic, "sys", types.SimpleNamespace(platform=platform_name))
    monkeypatch.setattr(
        install_tectonic, "platform", types.SimpleNamespace(machine=lambda: machine)
    )


def tar_archive(path, member="tectonic-0.17.0-aarch64-apple-darwin/tectonic", payload=b"#!/bin/sh\n"):
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path


def zip_archive(path, member="tectonic-0.17.0-x86_64-pc-windows-msvc/tectonic.exe", payload=b"MZ"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, payload)
    return path


def test_target_suffix_covers_both_macs_and_windows(monkeypatch):
    pretend_system(monkeypatch, "darwin", "arm64")
    assert install_tectonic.target_suffix() == "aarch64-apple-darwin"

    pretend_system(monkeypatch, "darwin", "x86_64")
    assert install_tectonic.target_suffix() == "x86_64-apple-darwin"

    # Intel builds run on ARM Windows too, so one Windows asset covers both.
    pretend_system(monkeypatch, "win32", "AMD64")
    assert install_tectonic.target_suffix() == "x86_64-pc-windows-msvc"

    pretend_system(monkeypatch, "win32", "ARM64")
    assert install_tectonic.target_suffix() == "x86_64-pc-windows-msvc"

    pretend_system(monkeypatch, "sunos5", "sparc")
    assert install_tectonic.target_suffix() is None


def test_parse_release_picks_the_matching_asset():
    mac = install_tectonic.parse_release(RELEASE, "aarch64-apple-darwin")
    assert mac["version"] == "0.17.0"
    assert mac["name"] == "tectonic-0.17.0-aarch64-apple-darwin.tar.gz"
    assert mac["url"].startswith("https://example.invalid/")

    windows = install_tectonic.parse_release(RELEASE, "x86_64-pc-windows-msvc")
    assert windows["name"].endswith(".zip")

    assert install_tectonic.parse_release(RELEASE, "sparc-sun-solaris") is None
    assert install_tectonic.parse_release({}, "x86_64-apple-darwin") is None
    assert install_tectonic.parse_release(None, "x86_64-apple-darwin") is None


def test_extract_binary_from_a_tarball(tmp_path):
    archive = tar_archive(tmp_path / "asset.tar.gz")
    binary = install_tectonic.extract_binary(str(archive), str(tmp_path / "tools"))
    assert binary == str(tmp_path / "tools" / "tectonic")
    with open(binary, "rb") as handle:
        assert handle.read() == b"#!/bin/sh\n"
    if not sys.platform.startswith("win"):
        assert os.access(binary, os.X_OK)


def test_extract_binary_from_a_zip(tmp_path):
    archive = zip_archive(tmp_path / "asset.zip")
    binary = install_tectonic.extract_binary(str(archive), str(tmp_path / "tools"))
    assert binary == str(tmp_path / "tools" / "tectonic.exe")
    with open(binary, "rb") as handle:
        assert handle.read() == b"MZ"


def test_extract_binary_refuses_an_archive_without_the_engine(tmp_path):
    archive = tmp_path / "asset.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "nothing to see")
    with pytest.raises(RuntimeError, match="No tectonic binary"):
        install_tectonic.extract_binary(str(archive), str(tmp_path / "tools"))


def test_install_engine_unpacks_the_download(tmp_path):
    archive = tmp_path / "release.tar.gz"
    tar_archive(archive)
    payload = archive.read_bytes()
    asset = {
        "version": "0.17.0",
        "name": "tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
        "url": "https://example.invalid/release.tar.gz",
    }
    binary = install_tectonic.install_engine(
        dest_dir=str(tmp_path / "tools"),
        fetch_release=lambda: asset,
        fetch_file=lambda url: payload,
        quiet=True,
    )
    assert os.path.isfile(binary)
    assert os.path.basename(binary).startswith("tectonic")


def test_find_engine_prefers_the_project_copy(tmp_path, monkeypatch):
    local = tmp_path / "tectonic"
    local.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(install_tectonic.shutil, "which", lambda name: "/usr/local/bin/tectonic")
    assert install_tectonic.find_engine(str(tmp_path)) == str(local)

    local.unlink()
    assert install_tectonic.find_engine(str(tmp_path)) == "/usr/local/bin/tectonic"


def test_the_server_finds_the_engine_the_installer_fetches(tmp_path, monkeypatch):
    """The two halves must agree on where the download lands."""
    # Both default to <project>/tools/tectonic; if that drifts, Compile would
    # silently ignore the engine the launcher just installed.
    assert install_tectonic.TOOLS == server.TECTONIC_DIR

    tools = tmp_path / "tools" / "tectonic"
    tools.mkdir(parents=True)
    name = "tectonic.exe" if sys.platform.startswith("win") else "tectonic"
    engine = tools / name
    engine.write_bytes(b"#!/bin/sh\n")

    monkeypatch.setattr(install_tectonic, "TOOLS", str(tools))
    assert install_tectonic.downloaded_binary() == str(engine)

    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tools))
    monkeypatch.setattr(server.resumes.shutil, "which", lambda found: "/usr/bin/tectonic-from-path")
    assert server.latex_engine() == str(engine)

    engine.unlink()
    assert server.latex_engine() == "/usr/bin/tectonic-from-path"


def test_main_is_quiet_when_an_engine_already_exists(monkeypatch, capsys):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: "/opt/homebrew/bin/tectonic")
    assert install_tectonic.main([]) == 0
    assert "LaTeX engine: /opt/homebrew/bin/tectonic" in capsys.readouterr().out

    assert install_tectonic.main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_main_does_not_retry_every_launch(monkeypatch, capsys):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: None)
    monkeypatch.setattr(install_tectonic, "recently_attempted", lambda: True)
    monkeypatch.setattr(
        install_tectonic,
        "install_engine",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not download")),
    )
    assert install_tectonic.main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_main_retries_after_a_failure_when_forced(monkeypatch):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: None)
    monkeypatch.setattr(install_tectonic, "recently_attempted", lambda: True)
    attempted = []
    monkeypatch.setattr(install_tectonic, "mark_attempt", lambda: attempted.append(True))
    monkeypatch.setattr(
        install_tectonic,
        "install_engine",
        lambda **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert install_tectonic.main(["--force", "--quiet"]) == 1
    assert attempted == [True]


def test_main_reports_a_failed_install_without_crashing(monkeypatch, capsys):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: None)
    monkeypatch.setattr(install_tectonic, "recently_attempted", lambda: False)
    monkeypatch.setattr(install_tectonic, "mark_attempt", lambda: None)
    monkeypatch.setattr(
        install_tectonic,
        "install_engine",
        lambda **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert install_tectonic.main([]) == 1
    out = capsys.readouterr().out
    assert "offline" in out
    assert "still works" in out
    assert "brew install tectonic" in out


def test_main_installs_and_checks_the_binary(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: None)
    monkeypatch.setattr(install_tectonic, "recently_attempted", lambda: False)
    monkeypatch.setattr(install_tectonic, "mark_attempt", lambda: None)
    binary = tmp_path / "tools" / "tectonic"
    binary.parent.mkdir()
    binary.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(install_tectonic, "install_engine", lambda **kwargs: str(binary))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return install_tectonic.subprocess.CompletedProcess(command, 0, "Tectonic 0.17.0", "")

    monkeypatch.setattr(install_tectonic.subprocess, "run", fake_run)
    assert install_tectonic.main([]) == 0
    assert calls == [[str(binary), "--version"]]
    assert "Tectonic installed at" in capsys.readouterr().out


def test_main_gives_up_when_the_downloaded_engine_will_not_run(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(install_tectonic, "find_engine", lambda folder=None: None)
    monkeypatch.setattr(install_tectonic, "recently_attempted", lambda: False)
    monkeypatch.setattr(install_tectonic, "mark_attempt", lambda: None)
    binary = tmp_path / "tectonic"
    binary.write_bytes(b"not really a binary")
    monkeypatch.setattr(install_tectonic, "install_engine", lambda **kwargs: str(binary))
    monkeypatch.setattr(
        install_tectonic.subprocess,
        "run",
        lambda command, **kwargs: install_tectonic.subprocess.CompletedProcess(
            command, 1, "", "bad CPU type"
        ),
    )
    assert install_tectonic.main([]) == 1
    out = capsys.readouterr().out
    assert "bad CPU type" in out
    assert "still works" in out


def test_both_launchers_install_the_engine_before_starting():
    """macOS and Windows get the same one-time install, whichever is double-clicked."""
    for name in ("Start Jobby.command", "Start Jobby.bat"):
        launcher = ROOT / name
        assert launcher.is_file(), "%s is missing" % name
        text = launcher.read_text(encoding="utf-8", errors="replace")
        assert "install_tectonic.py --quiet" in text, name
        # The install has to happen first, or the server starts without an engine.
        assert text.index("install_tectonic.py") < text.index("server.py"), name
        # The first run opens the dashboard; the restarts behind it must not open
        # a second one. macOS does that with --open; on Windows the window is
        # asked for once, before the restart loop.
        if name.endswith(".command"):
            assert "--open" in text, name
        else:
            assert "Jobby.vbs" in text[: text.index(":run")], name
            restarts = text[text.index(":run") : text.index(":givingup")]
            assert "Jobby.vbs" not in restarts, name
