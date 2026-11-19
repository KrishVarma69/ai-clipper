#!/usr/bin/env python3
"""AI Clipper environment setup (standard library only, runs on Python 3.8+).

Creates a virtual environment in a per-user data folder (NOT inside the plugin
folder, which Claude Code replaces on every update), installs the Python
packages, and checks for ffmpeg and an NVIDIA GPU.

    python3 setup_env.py            # create/update the venv
    python3 setup_env.py --check    # only report what's missing
    python3 setup_env.py --gpu      # also install NVIDIA CUDA libraries for Whisper

Data folder (override with the AI_CLIPPER_HOME environment variable):
    Windows: %LOCALAPPDATA%\\ai-clipper
    macOS:   ~/Library/Application Support/ai-clipper
    Linux:   $XDG_DATA_HOME/ai-clipper  (default ~/.local/share/ai-clipper)
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
MIN_PY = (3, 10)
IMPORTS = ["yt_dlp", "faster_whisper", "cv2", "numpy", "yaml", "requests"]


def data_dir() -> Path:
    env = os.environ.get("AI_CLIPPER_HOME")
    if env:
        return Path(env).expanduser()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-clipper"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ai-clipper"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ai-clipper"


def venv_dir() -> Path:
    return data_dir() / "venv"


def venv_python() -> Path:
    if platform.system() == "Windows":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def env_file() -> Path:
    return data_dir() / "env.json"


def load_env() -> dict:
    try:
        return json.loads(env_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def requirements_hash() -> str:
    try:
        return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


# ---------------------------------------------------------------- detection
# Homebrew's plain "ffmpeg" formula has no libass (needed to burn in captions);
# "ffmpeg-full" has it but is keg-only (not on PATH), so look for it explicitly.
FFMPEG_DIRS = ["/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"]
_ffmpeg_cache: dict = {}


def _has_libass(ffmpeg: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
        return any(line.split()[1:2] == ["subtitles"] for line in out.stdout.splitlines() if line.strip())
    except Exception:
        return False


def ffmpeg_bin(name: str = "ffmpeg") -> str | None:
    """Best ffmpeg/ffprobe: $AI_CLIPPER_FFMPEG_DIR, then ffmpeg-full, then PATH (prefer one with libass)."""
    if name in _ffmpeg_cache:
        return _ffmpeg_cache[name]
    exe = name + (".exe" if platform.system() == "Windows" else "")
    dirs = []
    if os.environ.get("AI_CLIPPER_FFMPEG_DIR"):
        dirs.append(os.environ["AI_CLIPPER_FFMPEG_DIR"])
    dirs += FFMPEG_DIRS
    found = []
    for d in dirs:
        p = Path(d) / exe
        if p.exists():
            found.append(str(p))
    on_path = shutil.which(name)
    if on_path:
        found.append(on_path)
    result = None
    if found:
        if name == "ffmpeg":
            result = next((f for f in found if _has_libass(f)), found[0])
        else:
            # Use the ffprobe that sits next to the chosen ffmpeg when possible.
            ff = ffmpeg_bin("ffmpeg")
            sib = Path(ff).with_name(exe) if ff else None
            result = str(sib) if sib and sib.exists() else found[0]
    _ffmpeg_cache[name] = result
    return result


def ffmpeg_ok() -> bool:
    return bool(ffmpeg_bin("ffmpeg")) and bool(ffmpeg_bin("ffprobe"))


def ffmpeg_libass_ok() -> bool:
    ff = ffmpeg_bin("ffmpeg")
    return bool(ff) and _has_libass(ff)


def has_nvidia_gpu() -> bool:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        out = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=15)
        return out.returncode == 0 and "GPU" in out.stdout
    except Exception:
        return False


def py_version(cmd: list[str]) -> tuple[int, int] | None:
    try:
        out = subprocess.run(cmd + ["-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return None
        major, minor = out.stdout.split()
        return int(major), int(minor)
    except Exception:
        return None


def find_python() -> list[str] | None:
    """Find a Python >= 3.10 to build the venv with."""
    if sys.version_info[:2] >= MIN_PY:
        return [sys.executable]
    candidates: list[list[str]] = []
    for minor in (13, 12, 11, 10, 14):
        candidates.append([f"python3.{minor}"])
    if platform.system() == "Windows":
        for minor in (13, 12, 11, 10):
            candidates.append(["py", f"-3.{minor}"])
    candidates += [["python3"], ["python"]]
    extra_dirs = ["/opt/homebrew/bin", "/usr/local/bin"]
    for cmd in candidates:
        exe = shutil.which(cmd[0])
        if not exe:
            for d in extra_dirs:
                p = Path(d) / cmd[0]
                if p.exists():
                    exe = str(p)
                    break
        if not exe:
            continue
        full = [exe] + cmd[1:]
        v = py_version(full)
        if v and v >= MIN_PY:
            return full
    return None


def packages_ok() -> tuple[bool, str]:
    py = venv_python()
    if not py.exists():
        return False, "virtual environment not created yet"
    code = "import importlib,sys\nmissing=[]\n"
    code += "for m in %r:\n" % (IMPORTS,)
    code += "    try: importlib.import_module(m)\n    except Exception as e: missing.append(m+' ('+type(e).__name__+')')\n"
    code += "print(','.join(missing))"
    try:
        out = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=120)
    except Exception as e:  # pragma: no cover
        return False, str(e)
    missing = out.stdout.strip()
    if out.returncode != 0:
        return False, out.stderr.strip()[-300:]
    return (not missing), (f"missing: {missing}" if missing else "ok")


def install_hint_ffmpeg() -> str:
    system = platform.system()
    if system == "Windows":
        return "winget install --id Gyan.FFmpeg -e   (then close and reopen your terminal / Claude Code)"
    if system == "Darwin":
        return ("brew install ffmpeg-full   (the plain 'ffmpeg' formula can't burn in captions; "
                "no Homebrew? see https://brew.sh)")
    if shutil.which("apt-get"):
        return "sudo apt-get update && sudo apt-get install -y ffmpeg"
    if shutil.which("dnf"):
        return "sudo dnf install -y ffmpeg   (may need RPM Fusion enabled)"
    if shutil.which("pacman"):
        return "sudo pacman -S ffmpeg"
    return "install ffmpeg with your package manager (https://ffmpeg.org/download.html)"


def install_hint_python() -> str:
    system = platform.system()
    if system == "Windows":
        return "winget install --id Python.Python.3.12 -e   (then reopen your terminal)"
    if system == "Darwin":
        return "brew install python@3.12"
    if shutil.which("apt-get"):
        return "sudo apt-get install -y python3 python3-venv python3-pip"
    return "install Python 3.10 or newer from https://www.python.org/downloads/"


def report() -> dict:
    py = find_python()
    pk_ok, pk_msg = packages_ok()
    env = load_env()
    status = {
        "os": f"{platform.system()} {platform.machine()}",
        "python": " ".join(py) if py else None,
        "python_ok": bool(py),
        "ffmpeg_ok": ffmpeg_ok(),
        "ffmpeg": ffmpeg_bin("ffmpeg"),
        "ffmpeg_libass": ffmpeg_libass_ok(),
        "venv": str(venv_dir()),
        "venv_ok": venv_python().exists(),
        "packages_ok": pk_ok,
        "packages_msg": pk_msg,
        "nvidia_gpu": has_nvidia_gpu(),
        "requirements_current": env.get("requirements_hash") == requirements_hash(),
    }
    status["ready"] = bool(status["python_ok"] and status["ffmpeg_ok"] and status["ffmpeg_libass"] and pk_ok)
    return status


def print_report(st: dict) -> None:
    def mark(ok):
        return "OK " if ok else "MISSING"
    print(f"System:          {st['os']}")
    print(f"[{mark(st['python_ok'])}] Python 3.10+   {st['python'] or '-> ' + install_hint_python()}")
    print(f"[{mark(st['ffmpeg_ok'])}] ffmpeg         {st['ffmpeg'] if st['ffmpeg_ok'] else '-> ' + install_hint_ffmpeg()}")
    if st["ffmpeg_ok"]:
        print(f"[{mark(st['ffmpeg_libass'])}] captions       "
              f"{'libass ok' if st['ffmpeg_libass'] else 'this ffmpeg has no libass -> ' + install_hint_ffmpeg()}")
    print(f"[{mark(st['venv_ok'])}] venv           {st['venv']}")
    print(f"[{mark(st['packages_ok'])}] packages       {st['packages_msg']}")
    if st["packages_ok"] and not st["requirements_current"]:
        print("[UPDATE ] packages       plugin was updated; re-run setup to refresh packages")
    print(f"        NVIDIA GPU     {'yes (Whisper will use CUDA)' if st['nvidia_gpu'] else 'no (Whisper uses the CPU, int8)'}")
    print("STATUS: " + ("READY" if st["ready"] else "NOT READY"))


# ------------------------------------------------------------------- setup
def run(cmd: list[str]) -> int:
    print("  $ " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    return subprocess.call(cmd)


def setup(gpu: bool | None) -> int:
    print(f"Data folder: {data_dir()}")
    py = find_python()
    if not py:
        print("Python 3.10 or newer was not found.")
        print("Install it with:  " + install_hint_python())
        return 2
    data_dir().mkdir(parents=True, exist_ok=True)
    if not venv_python().exists():
        print("Creating virtual environment...")
        if run(py + ["-m", "venv", str(venv_dir())]) != 0:
            print("Could not create the venv. On Debian/Ubuntu: sudo apt-get install python3-venv")
            return 2
    vpy = str(venv_python())
    print("Installing Python packages (first time takes a few minutes)...")
    run([vpy, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    if run([vpy, "-m", "pip", "install", "-q", "--upgrade", "-r", str(REQUIREMENTS)]) != 0:
        print("Package install failed. Check your internet connection and try again.")
        return 2
    nvidia = has_nvidia_gpu()
    if gpu is None:
        gpu = nvidia and platform.system() in ("Windows", "Linux")
    if gpu:
        print("NVIDIA GPU found: installing CUDA libraries for Whisper (cuBLAS + cuDNN)...")
        if run([vpy, "-m", "pip", "install", "-q", "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]) != 0:
            print("CUDA libraries failed to install; Whisper will fall back to the CPU.")
            gpu = False
    env = load_env()
    env.update({"requirements_hash": requirements_hash(), "cuda": bool(gpu), "python": " ".join(py)})
    env_file().write_text(json.dumps(env, indent=2), encoding="utf-8")
    print()
    st = report()
    print_report(st)
    if not st["ffmpeg_ok"] or not st["ffmpeg_libass"]:
        print()
        print("One more step: install ffmpeg (with caption support) with")
        print("    " + install_hint_ffmpeg())
    return 0 if st["ready"] else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Set up AI Clipper's Python environment")
    ap.add_argument("--check", action="store_true", help="only report status")
    ap.add_argument("--json", action="store_true", help="print status as JSON")
    ap.add_argument("--gpu", dest="gpu", action="store_true", default=None, help="install CUDA libraries")
    ap.add_argument("--no-gpu", dest="gpu", action="store_false", help="skip CUDA libraries")
    a = ap.parse_args(argv)
    if a.check:
        st = report()
        if a.json:
            print(json.dumps(st, indent=2))
        else:
            print_report(st)
        return 0 if st["ready"] else 1
    return setup(a.gpu)


if __name__ == "__main__":
    sys.exit(main())
