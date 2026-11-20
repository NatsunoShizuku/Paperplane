#!/usr/bin/env python3
"""Build a self-contained, zero-config offline Paperplane release bundle.

Bundles a relocatable standalone CPython 3.12 runtime (python-build-standalone)
with all dependencies pre-installed alongside the Paperplane base program.
End users can extract and double-click run-gui / run-cli with zero Python setup
and zero network access required for installation.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PBS_TAG = "20261110"
PBS_PY_VERSION = "3.12.16"
PBS_BASE_URL = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}"
)

TARGETS: dict[str, dict[str, str]] = {
    "macos-arm64": {
        "triple": "aarch64-apple-darwin",
        "os": "darwin",
        "archive": "tar.gz",
    },
    "macos-x64": {
        "triple": "x86_64-apple-darwin",
        "os": "darwin",
        "archive": "tar.gz",
    },
    "windows-x64": {
        "triple": "x86_64-pc-windows-msvc",
        "os": "windows",
        "archive": "zip",
    },
}

STDLIB_PRUNE_DIRS = ("test", "idlelib", "turtledemo", "tkinter", "pydoc_data")


def detect_host_target() -> str:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if sys_name == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        if machine in ("x86_64", "amd64"):
            return "macos-x64"
    elif sys_name == "windows":
        if machine in ("x86_64", "amd64"):
            return "windows-x64"
    raise SystemExit(f"Unsupported host platform for native build: {sys_name}/{machine}")


def read_project_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    ver = str(data.get("project", {}).get("version", "0.0.0")).strip()
    return ver if ver.startswith("v") else f"v{ver}"


def pbs_asset_name(target: str) -> str:
    triple = TARGETS[target]["triple"]
    return f"cpython-{PBS_PY_VERSION}+{PBS_TAG}-{triple}-install_only_stripped.tar.gz"


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[build] Using cached runtime archive: {dest}")
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"[build] Downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Paperplane-Release-Builder/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 256)
    tmp.replace(dest)
    print(f"[build] Downloaded {dest.stat().st_size / (1024 * 1024):.1f} MB -> {dest}")
    return dest


def extract_runtime(archive: Path, runtime_dir: Path) -> None:
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    tmp_extract = runtime_dir.parent / "_runtime_extract"
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)
    tmp_extract.mkdir(parents=True, exist_ok=True)
    print(f"[build] Extracting standalone Python runtime...")
    with tarfile.open(archive, "r:gz") as tf:
        try:
            tf.extractall(tmp_extract, filter="tar")
        except TypeError:
            tf.extractall(tmp_extract)
    py_root = tmp_extract / "python"
    if not py_root.is_dir():
        raise SystemExit(f"Unexpected archive layout: missing 'python/' in {archive}")
    py_root.rename(runtime_dir)
    shutil.rmtree(tmp_extract, ignore_errors=True)


def runtime_python(runtime_dir: Path, target: str) -> Path:
    if TARGETS[target]["os"] == "windows":
        return runtime_dir / "python.exe"
    return runtime_dir / "bin" / "python3"


def parse_requirements(req_file: Path) -> tuple[list[str], list[str]]:
    """Split requirements.txt into required deps and optional acceleration deps (cryptg)."""
    required: list[str] = []
    optional: list[str] = []
    for raw in req_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("cryptg"):
            optional.append(line)
        else:
            required.append(line)
    return required, optional


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if extra:
        env.update(extra)
    return env


def run_cmd(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    print(f"[build] $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env, cwd=cwd)


def install_dependencies(py: Path, target: str, pip_index_url: str | None) -> str:
    required, optional = parse_requirements(ROOT / "requirements.txt")
    env = clean_env()
    base_pip = [
        str(py),
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--no-warn-script-location",
    ]
    if pip_index_url:
        base_pip += ["-i", pip_index_url]

    print(f"[build] Installing {len(required)} core dependencies into bundled runtime...")
    run_cmd(base_pip + required, env=env)

    for opt in optional:
        print(f"[build] Installing optional dependency: {opt} ...")
        try:
            run_cmd(base_pip + [opt], env=env)
        except subprocess.CalledProcessError:
            print(
                f"[build] Warning: optional dependency '{opt}' failed to install; "
                "continuing without it (Telethon will fall back to pure Python AES)."
            )

    freeze = subprocess.run(
        [str(py), "-m", "pip", "freeze", "--disable-pip-version-check"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return freeze.stdout.strip()


def prune_runtime(runtime_dir: Path, target: str) -> None:
    if TARGETS[target]["os"] == "windows":
        stdlib = runtime_dir / "Lib"
    else:
        stdlib = runtime_dir / "lib" / "python3.12"

    for name in STDLIB_PRUNE_DIRS:
        p = stdlib / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    # Remove pip/setuptools caches if any
    for p in runtime_dir.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def copy_project_files(bundle_dir: Path, target: str) -> None:
    shutil.copytree(
        ROOT / "paperplane",
        bundle_dir / "paperplane",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for fname in ("config.example.toml", "README.md", "README-zh.md", "pyproject.toml", "requirements.txt"):
        src = ROOT / fname
        if src.exists():
            shutil.copy2(src, bundle_dir / fname)
    # 离线包同时内置一份可直接编辑的 config.toml（由模板复制，不含任何个人凭证）
    example_cfg = ROOT / "config.example.toml"
    if example_cfg.exists():
        shutil.copy2(example_cfg, bundle_dir / "config.toml")

    if TARGETS[target]["os"] == "windows":
        launchers = ("run-gui.bat", "run-cli.bat")
    else:
        launchers = ("run-gui.command", "run-cli.command")

    for lname in launchers:
        src = ROOT / lname
        dst = bundle_dir / lname
        shutil.copy2(src, dst)
        if TARGETS[target]["os"] == "darwin":
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_runtime_info(bundle_dir: Path, target: str, version: str, freeze_out: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = (
        f"Paperplane Offline Release Bundle\n"
        f"Version: {version}\n"
        f"Target: {target}\n"
        f"Bundled Python: CPython {PBS_PY_VERSION} (python-build-standalone {PBS_TAG})\n"
        f"Built At (UTC): {now}\n"
        f"\n"
        f"Installed Packages (pip freeze):\n"
        f"{freeze_out}\n"
    )
    (bundle_dir / "RUNTIME_INFO.txt").write_text(content, encoding="utf-8")


def smoke_test(py: Path, bundle_dir: Path, run_tests: bool, pip_index_url: str | None) -> None:
    print("[build] Running isolated smoke test using bundled runtime...")
    env = clean_env({"PYTHONPATH": str(bundle_dir), "PYTHONDONTWRITEBYTECODE": "1"})
    check_code = (
        "import sys, paperplane, telethon, nicegui, rich, questionary, tomlkit, python_socks; "
        "import paperplane.core, paperplane.gui, paperplane.service, paperplane.cli_ui; "
        "has_cryptg = True\n"
        "try:\n"
        "    import cryptg\n"
        "except ImportError:\n"
        "    has_cryptg = False\n"
        "print('Smoke OK | Python', sys.version.split()[0], '| cryptg:', has_cryptg)"
    )
    run_cmd([str(py), "-c", check_code], env=env, cwd=bundle_dir)
    run_cmd([str(py), "-m", "paperplane", "--help"], env=env, cwd=bundle_dir)

    if run_tests:
        print("[build] Running pytest suite against bundled runtime (isolated test deps)...")
        test_deps = ROOT / "build" / "test-deps"
        if not (test_deps / "pytest").exists():
            test_deps.mkdir(parents=True, exist_ok=True)
            pip_cmd = [
                str(py),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--target",
                str(test_deps),
                "pytest",
            ]
            if pip_index_url:
                pip_cmd += ["-i", pip_index_url]
            run_cmd(pip_cmd, env=clean_env())
        test_env = clean_env({
            "PYTHONPATH": f"{bundle_dir}{os.pathsep}{test_deps}",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        run_cmd(
            [str(py), "-m", "pytest", "-v", "-p", "no:cacheprovider", str(ROOT / "tests")],
            env=test_env,
            cwd=bundle_dir,
        )


def create_archive(bundle_dir: Path, output_dir: Path, target: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    kind = TARGETS[target]["archive"]
    if kind == "zip":
        archive_path = output_dir / f"{bundle_dir.name}.zip"
        if archive_path.exists():
            archive_path.unlink()
        print(f"[build] Creating {archive_path} ...")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(bundle_dir.rglob("*")):
                arcname = item.relative_to(bundle_dir.parent)
                if item.is_dir():
                    continue
                zf.write(item, arcname)
    else:
        archive_path = output_dir / f"{bundle_dir.name}.tar.gz"
        if archive_path.exists():
            archive_path.unlink()
        print(f"[build] Creating {archive_path} ...")
        with tarfile.open(archive_path, "w:gz", compresslevel=6) as tf:
            tf.add(bundle_dir, arcname=bundle_dir.name, recursive=True)

    sha = hashlib.sha256()
    with open(archive_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    sha_file = archive_path.with_name(archive_path.name + ".sha256")
    sha_file.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"[build] Done: {archive_path} ({size_mb:.1f} MB)")
    print(f"[build] SHA256: {digest}")
    return archive_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build offline zero-config Paperplane release archive.")
    ap.add_argument(
        "--target",
        choices=sorted(TARGETS.keys()),
        default=None,
        help="Target platform (defaults to current host platform).",
    )
    ap.add_argument(
        "--version",
        default=None,
        help="Release version tag (defaults to version in pyproject.toml, e.g. v0.2.0).",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory for final release archives (default: dist/).",
    )
    ap.add_argument(
        "--pip-index-url",
        default=None,
        help="Optional PyPI mirror URL for pip install.",
    )
    ap.add_argument(
        "--run-tests",
        action="store_true",
        help="Run pytest suite using the bundled runtime before archiving.",
    )
    args = ap.parse_args()

    host_target = detect_host_target()
    target = args.target or host_target
    if target != host_target:
        raise SystemExit(
            f"Cannot natively build target '{target}' on host '{host_target}' because pip must "
            f"execute the bundled {target} runtime. Run on a {target} runner or use GitHub Actions."
        )

    version = args.version or read_project_version()
    if not version.startswith("v"):
        version = f"v{version}"

    cache_dir = ROOT / "build" / "cache"
    stage_root = ROOT / "build" / "stage"
    bundle_name = f"Paperplane-{version}-{target}"
    bundle_dir = stage_root / bundle_name
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    asset_name = pbs_asset_name(target)
    runtime_archive = download_file(f"{PBS_BASE_URL}/{asset_name}", cache_dir / asset_name)
    runtime_dir = bundle_dir / "runtime"
    extract_runtime(runtime_archive, runtime_dir)

    py = runtime_python(runtime_dir, target)
    if not py.exists():
        raise SystemExit(f"Bundled Python executable not found at {py}")

    freeze_out = install_dependencies(py, target, args.pip_index_url)
    prune_runtime(runtime_dir, target)
    copy_project_files(bundle_dir, target)
    write_runtime_info(bundle_dir, target, version, freeze_out)
    smoke_test(py, bundle_dir, args.run_tests, args.pip_index_url)
    create_archive(bundle_dir, args.output_dir, target)


if __name__ == "__main__":
    main()
