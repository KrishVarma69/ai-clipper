#!/usr/bin/env python3
"""AI Clipper command-line entry point.

Run it with any Python 3 (even an old system one): it re-launches itself inside
AI Clipper's own virtual environment, which `setup_env.py` creates in your user
data folder.

    python3 clipper_cli.py check
    python3 clipper_cli.py probe <url-or-file>
    python3 clipper_cli.py prepare <url-or-file-or-links.txt> [--start 1:00:00 --end 2:30:00]
    python3 clipper_cli.py transcript <folder> [--chunk N]
    python3 clipper_cli.py render <folder> --moments moments.json [--reframe face|blur|split]
    python3 clipper_cli.py run <url-or-file> --clips 5 --ai heuristic|groq|gemini|openai
    python3 clipper_cli.py --help
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_env  # noqa: E402  (stdlib only)

NO_VENV_COMMANDS = {"check", "setup"}


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == setup_env.venv_dir().resolve()
    except OSError:
        return False


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "setup":
        return setup_env.main(args[1:])
    if cmd == "check" and not in_venv():
        return setup_env.main(["--check"] + args[1:])
    if not in_venv():
        vpy = setup_env.venv_python()
        if not vpy.exists():
            print("AI Clipper isn't set up yet (no Python environment found).")
            print("Inside Claude Code run:  /ai-clipper:setup")
            print(f"Or run:  python3 \"{HERE / 'setup_env.py'}\"")
            return 3
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # Re-launch inside the venv. subprocess (not os.execv) keeps Windows happy.
        try:
            return subprocess.call([str(vpy), str(Path(__file__).resolve())] + args, env=env)
        except KeyboardInterrupt:
            return 130
    from clipper.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
