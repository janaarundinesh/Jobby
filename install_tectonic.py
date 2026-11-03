"""Install Tectonic, the LaTeX engine the Resume tab compiles with.

Tectonic is optional: without it the editor keeps using its in-app preview. The
launchers for macOS and Windows run this script before starting the server, so
the first launch after a clean checkout leaves a working **Compile** button.

Nothing here is pip-installable (Tectonic is a single Rust binary), so the script
downloads the official release from GitHub into ``tools/tectonic/`` instead of
asking for administrator rights. An engine already on PATH — Homebrew, MacPorts,
Scoop, Cargo — is used as it is.

    python3 install_tectonic.py            # install if missing
    python3 install_tectonic.py --force    # try again after a failure
    python3 install_tectonic.py --quiet    # only speak when something happens
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools", "tectonic")
MARKER = os.path.join(ROOT, "data", ".tectonic-install-attempt")
RELEASE_API = "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"
PINNED_VERSION = "0.17.0"
RETRY_HOURS = 24
BINARY_NAMES = ("tectonic.exe", "tectonic")
MANUAL_HELP = (
    "Install it yourself with one of these, then reload the dashboard:\n"
    "  macOS    brew install tectonic\n"
    "  Windows  https://tectonic-typesetting.github.io/install.html\n"
    "  any      cargo install tectonic"
)


def target_suffix():
    """The release asset suffix for this machine, or None if unsupported."""
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return "aarch64-apple-darwin"
        return "x86_64-apple-darwin"
    if sys.platform.startswith("win"):
        # Only an x86_64 build is published; it runs on ARM Windows too.
        return "x86_64-pc-windows-msvc"
    if sys.platform.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "aarch64-unknown-linux-musl"
        return "x86_64-unknown-linux-musl"
    return None


def parse_release(payload, suffix):
    """Pick the download for suffix out of a GitHub release response."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "")
    version = tag.split("@")[-1].lstrip("v")
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if not name.endswith(("-" + suffix + ".tar.gz", "-" + suffix + ".zip")):
            continue
        url = asset.get("browser_download_url")
        if not url:
            continue
        return {"version": version, "name": name, "url": url}
    return None


def downloaded_binary(folder=None):
    """An engine already installed by this script, if any."""
    base = folder or TOOLS
    for name in BINARY_NAMES:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return path
    return None


def system_binary():
    """An engine somewhere on PATH (Homebrew, Cargo, Scoop, ...)."""
    return shutil.which("tectonic")


def find_engine(folder=None):
    return downloaded_binary(folder) or system_binary()


def write_atomic(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=directory or ".")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def download(url, timeout=180):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "jobby-tectonic-installer",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def discover(suffix=None):
    """The release asset to download: the latest one, or a pinned fallback."""
    suffix = suffix or target_suffix()
    if not suffix:
        raise RuntimeError("This platform has no Tectonic build")
    try:
        payload = json.loads(download(RELEASE_API).decode("utf-8"))
        asset = parse_release(payload, suffix)
        if asset:
            return asset
    except (urllib.error.URLError, ValueError, OSError):
        pass
    extension = ".zip" if suffix.startswith("x86_64-pc-windows") else ".tar.gz"
    name = "tectonic-%s-%s%s" % (PINNED_VERSION, suffix, extension)
    tag = "tectonic%%40%s" % PINNED_VERSION
    return {
        "version": PINNED_VERSION,
        "name": name,
        "url": "https://github.com/tectonic-typesetting/tectonic/releases/download/%s/%s"
        % (tag, name),
    }


def extract_binary(archive_path, dest_dir, member_name=None):
    """Copy the engine out of a release archive and make it executable."""
    os.makedirs(dest_dir, exist_ok=True)
    candidates = []
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if os.path.basename(info.filename).lower() in BINARY_NAMES:
                    candidates.append((info.filename, info.file_size, "zip"))
            if not candidates:
                raise RuntimeError("No tectonic binary inside %s" % archive_path)
            name = _pick_member(candidates, member_name)
            with archive.open(name) as source:
                data = source.read()
    else:
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive.getmembers():
                if member.name and member.isfile() and os.path.basename(member.name).lower() in BINARY_NAMES:
                    candidates.append((member.name, member.size, "tar"))
            if not candidates:
                raise RuntimeError("No tectonic binary inside %s" % archive_path)
            name = _pick_member(candidates, member_name)
            extracted = archive.extractfile(name)
            if extracted is None:
                raise RuntimeError("Could not read %s" % name)
            data = extracted.read()
    target = os.path.join(dest_dir, os.path.basename(name))
    write_atomic(target, data)
    if not sys.platform.startswith("win"):
        os.chmod(target, 0o755)
    return target


def _pick_member(candidates, member_name):
    if member_name:
        for name, _size, _kind in candidates:
            if name == member_name:
                return name
    # Prefer the plain binary over anything like a debug artifact.
    candidates.sort(key=lambda item: (len(item[0]), item[0]))
    return candidates[0][0]


def install_engine(dest_dir=None, fetch_release=None, fetch_file=None, quiet=False):
    """Download and unpack Tectonic; returns the installed path."""
    destination = dest_dir or TOOLS
    asset = (fetch_release or (lambda: discover()))()
    if not asset:
        raise RuntimeError("No Tectonic release found for this platform")
    if not quiet:
        print("Downloading Tectonic %s…" % asset.get("version", ""))
    if fetch_file:
        data = fetch_file(asset["url"])
    else:
        data = download(asset["url"])
    handle, archive_path = tempfile.mkstemp(suffix=os.path.splitext(asset["name"])[1])
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        binary = extract_binary(archive_path, destination)
    finally:
        if os.path.exists(archive_path):
            os.unlink(archive_path)
    return binary


def recently_attempted():
    try:
        stamp = os.path.getmtime(MARKER)
    except OSError:
        return False
    return (time.time() - stamp) < RETRY_HOURS * 3600


def mark_attempt():
    try:
        write_atomic(MARKER, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()).encode())
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Tectonic for the resume compiler")
    parser.add_argument("--force", action="store_true", help="try again even after a recent failure")
    parser.add_argument("--quiet", action="store_true", help="say nothing when an engine is already available")
    args = parser.parse_args(argv)

    found = find_engine()
    if found:
        if not args.quiet:
            print("LaTeX engine: " + found)
        return 0
    if recently_attempted() and not args.force:
        if not args.quiet:
            print("Tectonic is not available; Compile will use the in-app preview.")
        return 0

    mark_attempt()
    print("Installing Tectonic for the resume compiler (one time)…")
    try:
        binary = install_engine(quiet=args.quiet)
        check = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=120
        )
        if check.returncode != 0:
            raise RuntimeError(
                "the downloaded Tectonic would not run: %s" % check.stderr.strip()
            )
    except Exception as error:  # offline, no release for this machine, bad archive
        print("Could not install Tectonic automatically: %s" % error)
        print(MANUAL_HELP)
        print("The dashboard still works; Compile will use the in-app preview.")
        return 1
    print("Tectonic installed at " + binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
