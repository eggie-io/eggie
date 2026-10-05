#!/usr/bin/env python3
"""Reads the coding-agent manifests (runtime/agents/<id>/agent.json) for the
install scripts and the root-side agent runner. Stdlib only: it runs on the
VM's own python3.
  agents.py [--agents-dir DIR] skills
  agents.py [--agents-dir DIR] instructions (--system | --home HOME)
  agents.py [--agents-dir DIR] run STATUS_DIR --path PATH   (accounts on stdin)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "agents"

SETUP_TIMEOUT = "10m"
MAX_PASSES = 5
STATES = {"installing", "ready", "failed"}


@dataclass(frozen=True)
class Agent:
    id: str
    home: str
    instructions: tuple[str, ...]
    skills: str | None
    ignore: frozenset[str]
    setup: str | None


def _inside(path: object) -> bool:
    if not isinstance(path, str) or path == "" or path.startswith("/"):
        return False
    return all(SEGMENT.fullmatch(part) and part not in {".", ".."} for part in path.split("/"))


def _target(path: object) -> bool:
    if not isinstance(path, str):
        return False
    if path.startswith("~/"):
        return _inside(path[2:])
    return path.startswith("/") and _inside(path[1:])


def parse(agent_id: str, raw: object) -> Agent:
    if not (isinstance(agent_id, str) and ID.fullmatch(agent_id)):
        raise ValueError("the id is not a plain name")
    if not isinstance(raw, dict):
        raise ValueError("agent.json is not an object")
    if not _inside(raw.get("home")):
        raise ValueError("home must be a path inside the account's home")
    instructions = raw.get("instructions", [])
    if not isinstance(instructions, list) or not all(_target(t) for t in instructions):
        raise ValueError("instructions must be /absolute or ~/ paths")
    skills = raw.get("skills")
    if skills is not None and not (isinstance(skills, str) and ID.fullmatch(skills)):
        raise ValueError("skills must be a plain name")
    detect = raw.get("detect", {})
    ignore = detect.get("ignore", []) if isinstance(detect, dict) else None
    if not isinstance(ignore, list) or not all(isinstance(n, str) and SEGMENT.fullmatch(n) for n in ignore):
        raise ValueError("detect.ignore must be a list of names")
    setup = raw.get("setup")
    if setup is not None and not (isinstance(setup, dict) and isinstance(setup.get("run"), str)
                                  and setup["run"].strip()):
        raise ValueError("setup.run must be a command")
    return Agent(agent_id, raw["home"], tuple(instructions), skills, frozenset(ignore),
                 setup["run"] if setup else None)


def load(agents_dir: Path) -> list[Agent]:
    index = json.loads((agents_dir / "index.json").read_text())
    ids = index.get("agents") if isinstance(index, dict) else None
    if not isinstance(ids, list):
        raise ValueError("index.json has no agent list")
    agents = []
    for agent_id in ids:
        try:
            agents.append(parse(agent_id, json.loads((agents_dir / str(agent_id) / "agent.json").read_text())))
        except (OSError, ValueError) as e:
            print(f"skipping agent {agent_id}: {e}", file=sys.stderr)
    return agents


def connected(agent: Agent, homes: list[str]) -> bool:
    for home in homes:
        try:
            names = os.listdir(Path(home) / agent.home)
        except OSError:
            continue
        if any(name not in agent.ignore for name in names):
            return True
    return False


def _number(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _previous(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def _write(path: Path, value: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".status-")
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _run_setup(agent: Agent, accounts: list[tuple[str, str]], status_dir: Path, path: str) -> bool:
    log = os.open(status_dir / f"setup-{agent.id}.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    ok = True
    with os.fdopen(log, "w") as out:
        for name, home in accounts:
            out.write(f"== {name}\n")
            out.flush()
            argv = ["timeout", SETUP_TIMEOUT, "runuser", "-u", name, "--", "env",
                    f"HOME={home}", f"PATH={path}", "bash", "-c", agent.setup]
            try:
                code = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out,
                                      stderr=subprocess.STDOUT).returncode
            except OSError as e:
                out.write(f"{e}\n")
                code = 1
            ok = ok and code == 0
    return ok


def run_pass(agents: list[Agent], accounts: list[tuple[str, str]], status_dir: Path,
             path: str, generation: int) -> None:
    status_path = status_dir / "status.json"
    previous = _previous(status_path)
    homes = [home for _, home in accounts]
    out: dict[str, dict] = {}
    for agent in agents:
        before = previous.get(agent.id) if isinstance(previous.get(agent.id), dict) else {}
        setup = before.get("setup") if before.get("setup") in STATES else None
        # Passes are serialized, so an "installing" left behind was interrupted.
        if setup == "installing":
            setup = "failed"
        done = before.get("setup_generation")
        out[agent.id] = {"connected": connected(agent, homes), "setup": setup,
                         "setup_generation": done if isinstance(done, int) else 0}

    for agent in agents:
        entry = out[agent.id]
        wanted = _number(status_dir / "setup" / agent.id)
        if agent.setup is None or wanted <= entry["setup_generation"]:
            continue
        entry["setup_generation"] = wanted
        if entry["connected"]:
            entry["setup"] = "ready"
            continue
        entry["setup"] = "installing"
        _write(status_path, {"generation": generation, "agents": out})
        entry["setup"] = "ready" if _run_setup(agent, accounts, status_dir, path) else "failed"

    for agent in agents:
        out[agent.id]["connected"] = connected(agent, homes)
    _write(status_path, {"generation": generation, "agents": out})


def run(agents: list[Agent], accounts: list[tuple[str, str]], status_dir: Path, path: str) -> None:
    # Triggers that arrive while a pass runs are dropped by systemd, so look again.
    for _ in range(MAX_PASSES):
        start = _number(status_dir / "check")
        run_pass(agents, accounts, status_dir, path, start)
        if _number(status_dir / "check") == start:
            return


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("skills")
    instructions = sub.add_parser("instructions")
    where = instructions.add_mutually_exclusive_group(required=True)
    where.add_argument("--system", action="store_true")
    where.add_argument("--home")
    runner = sub.add_parser("run")
    runner.add_argument("status_dir", type=Path)
    runner.add_argument("--path", required=True)
    args = parser.parse_args(argv)

    try:
        agents = load(args.agents_dir)
    except (OSError, ValueError) as e:
        print(f"cannot read the agent list: {e}", file=sys.stderr)
        return 1

    if args.command == "skills":
        print(" ".join(a.skills for a in agents if a.skills))
    elif args.command == "instructions":
        for agent in agents:
            for target in agent.instructions:
                if args.system and target.startswith("/"):
                    print(target)
                elif args.home and target.startswith("~/"):
                    print(f"{args.home.rstrip('/')}/{target[2:]}")
    elif args.command == "run":
        accounts = []
        for line in sys.stdin:
            parts = line.rstrip("\n").split(":")
            if len(parts) == 4:
                accounts.append((parts[0], parts[3]))
        run(agents, accounts, args.status_dir, args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
