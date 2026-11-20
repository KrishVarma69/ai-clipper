"""Performance profiles: detect weak computers and make the pipeline lighter on them.

performance: auto    -> "lite" on a weak computer, else "normal"
performance: lite    -> always lite
performance: normal  -> never lite

A computer counts as weak when it has no fast transcription hardware (Apple Silicon GPU or
an NVIDIA GPU) and either 4 or fewer CPU threads, or 8 GB of RAM or less with 4 or fewer
CPU cores. Standard library only.
"""
from __future__ import annotations

import os
import platform
import subprocess
from functools import lru_cache

# What "lite" changes. Applied on top of config.default.yaml and UNDER the user's
# clipper.yaml / campaign / CLI flags, so anything the user sets explicitly wins.
LITE_PRESET = {
    "whisper_model": "base",       # ~2.5x faster than "small" on a CPU
    "whisper_beam_size": 1,        # greedy decoding: much faster, slightly less accurate
    "whisper_threads": "lite",     # physical cores minus one (keeps the PC usable)
    "low_priority": True,          # Whisper and ffmpeg run below normal priority
    "max_height": 720,             # smaller download, cheaper decoding
    "face": {"sample_every": 1.0},
    "video": {"encoder": "auto", "preset": "superfast"},
}

WEAK_THREADS = 4
WEAK_RAM_GB = 8.5   # an "8 GB" machine reports a bit under 8


# ------------------------------------------------------------------ machine
def logical_cpus() -> int:
    return os.cpu_count() or 2


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _windows_physical_cores() -> int | None:
    import ctypes
    from ctypes import wintypes

    class SLPI(ctypes.Structure):  # SYSTEM_LOGICAL_PROCESSOR_INFORMATION
        _fields_ = [("mask", ctypes.c_size_t), ("relationship", ctypes.c_int),
                    ("reserved", ctypes.c_ulonglong * 2)]

    k32 = ctypes.windll.kernel32
    size = wintypes.DWORD(0)
    k32.GetLogicalProcessorInformation(None, ctypes.byref(size))
    if not size.value:
        return None
    buf = (SLPI * (size.value // ctypes.sizeof(SLPI)))()
    if not k32.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
        return None
    cores = sum(1 for e in buf if e.relationship == 0)  # RelationProcessorCore
    return cores or None


def _linux_physical_cores() -> int | None:
    pairs, phys, core = set(), None, None
    with open("/proc/cpuinfo", encoding="utf-8") as f:
        for line in f:
            if line.startswith("physical id"):
                phys = line.split(":", 1)[1].strip()
            elif line.startswith("core id"):
                core = line.split(":", 1)[1].strip()
            elif not line.strip():
                if core is not None:
                    pairs.add((phys, core))
                phys = core = None
    if core is not None:
        pairs.add((phys, core))
    return len(pairs) or None


@lru_cache(maxsize=1)
def physical_cpus() -> int:
    system = platform.system()
    n = None
    try:
        if system == "Windows":
            n = _windows_physical_cores()
        elif system == "Darwin":
            v = _sysctl("hw.physicalcpu")
            n = int(v) if v else None
        elif system == "Linux":
            n = _linux_physical_cores()
    except Exception:
        n = None
    return max(1, min(n or logical_cpus(), logical_cpus()))


@lru_cache(maxsize=1)
def ram_gb() -> float | None:
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            class MEMSTAT(ctypes.Structure):  # MEMORYSTATUSEX
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys / 1024 ** 3
            return None
        if system == "Darwin":
            v = _sysctl("hw.memsize")
            return int(v) / 1024 ** 3 if v else None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except Exception:
        return None


def machine_text() -> str:
    ram = ram_gb()
    return (f"{physical_cpus()} cores / {logical_cpus()} threads"
            + (f", {ram:.0f} GB RAM" if ram else ""))


@lru_cache(maxsize=1)
def is_weak_machine() -> tuple[bool, str]:
    """(weak?, reason). Fast GPUs for Whisper rule it out; then CPU threads and RAM decide."""
    from .transcribe import apple_silicon, cuda_available
    if apple_silicon():
        return False, "Apple Silicon (transcribes on the GPU)"
    threads, cores, ram = logical_cpus(), physical_cpus(), ram_gb()
    weak_cpu = threads <= WEAK_THREADS
    weak_ram = ram is not None and ram <= WEAK_RAM_GB and cores <= WEAK_THREADS
    if not (weak_cpu or weak_ram):
        return False, machine_text()
    if cuda_available():
        return False, machine_text() + " + NVIDIA GPU"
    return True, machine_text()


# ------------------------------------------------------------------ profile
def resolve_profile(requested) -> dict:
    """{'name': 'lite'|'normal', 'reason': str}."""
    req = str(requested or "auto").strip().lower()
    if req in ("lite", "light", "low", "weak"):
        return {"name": "lite", "reason": "set to lite"}
    if req in ("normal", "full", "off", "high"):
        return {"name": "normal", "reason": "set to normal"}
    weak, why = is_weak_machine()
    return {"name": "lite" if weak else "normal",
            "reason": f"auto: {'weak' if weak else 'capable'} computer ({why})"}


def profile_text(cfg: dict) -> str:
    p = cfg.get("_profile") or {"name": "normal", "reason": ""}
    if p["name"] != "lite":
        return f"Performance: normal ({p['reason']})."
    return (f"Performance: LITE ({p['reason']}). Using Whisper '{cfg.get('whisper_model')}', "
            f"beam {cfg.get('whisper_beam_size')}, {whisper_threads(cfg)} threads, "
            f"{cfg.get('max_height')}p downloads, {_encoder_text(cfg)}, "
            f"low priority. Set performance: normal in clipper.yaml for full quality.")


def _encoder_text(cfg: dict) -> str:
    enc = str((cfg.get("video") or {}).get("encoder") or "libx264").lower()
    return "hardware video encoder if available" if enc in ("auto", "hardware", "gpu") else f"{enc} encoder"


def whisper_threads(cfg: dict) -> int:
    v = cfg.get("whisper_threads", "auto")
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        return max(1, int(v))
    if str(v).lower() == "lite":
        # Leave a core free, but never drop below 2 threads.
        return max(1, min(logical_cpus(), max(2, physical_cpus() - 1)))
    return logical_cpus()


# ----------------------------------------------------------------- priority
_priority_set = False


def lower_priority(cfg: dict) -> None:
    """Run this process (and the ffmpeg / Whisper work it starts) below normal priority."""
    global _priority_set
    if _priority_set or not cfg.get("low_priority"):
        return
    _priority_set = True
    try:
        if platform.system() == "Windows":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x4000  # child processes inherit it
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass
