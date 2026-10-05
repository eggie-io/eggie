from __future__ import annotations

from pathlib import Path
import yaml


def load_compose(path) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def container_port(entry) -> int:
    if isinstance(entry, dict):  # long syntax
        return int(entry["target"])
    text = str(entry)
    text = text.split("/", 1)[0]          # drop /tcp|/udp
    parts = text.split(":")
    return int(parts[-1])                  # container port is last


def exposed_ports(service: dict) -> list[int]:
    if service.get("ports"):
        return [container_port(p) for p in service["ports"]]
    if service.get("expose"):
        return [int(p) for p in service["expose"]]
    return []
