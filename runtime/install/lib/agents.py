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
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "agents"


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
    return all(SEGMENT.match(part) and part not in {".", ".."} for part in path.split("/"))


def _target(path: object) -> bool:
    if not isinstance(path, str):
        return False
    if path.startswith("~/"):
        return _inside(path[2:])
    return path.startswith("/") and _inside(path[1:])


def parse(agent_id: str, raw: object) -> Agent:
    if not ID.match(agent_id):
        raise ValueError("the id is not a plain name")
    if not isinstance(raw, dict):
        raise ValueError("agent.json is not an object")
    if not _inside(raw.get("home")):
        raise ValueError("home must be a path inside the account's home")
    instructions = raw.get("instructions", [])
    if not isinstance(instructions, list) or not all(_target(t) for t in instructions):
        raise ValueError("instructions must be /absolute or ~/ paths")
    skills = raw.get("skills")
    if skills is not None and not (isinstance(skills, str) and ID.match(skills)):
        raise ValueError("skills must be a plain name")
    detect = raw.get("detect", {})
    ignore = detect.get("ignore", []) if isinstance(detect, dict) else None
    if not isinstance(ignore, list) or not all(isinstance(n, str) and SEGMENT.match(n) for n in ignore):
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-dir", type=Path, default=DEFAULT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("skills")
    instructions = sub.add_parser("instructions")
    where = instructions.add_mutually_exclusive_group(required=True)
    where.add_argument("--system", action="store_true")
    where.add_argument("--home")
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
