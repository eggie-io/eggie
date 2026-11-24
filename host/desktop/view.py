"""Pure mappings from host/core values to what a screen needs.

No provider and no client -- nothing here ever reaches the VM, the network
or the filesystem. That is what makes this the only module in host/desktop
worth testing, and why api.py stays thin enough to read in one sitting.

No user-facing copy lives here but 1d's tile notes, prices and default name;
ui/index.html holds the rest, so the design board stays its single source.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from host.core import constants
from host.core.connect.flow import LOCAL
from host.core.status import Readiness


@dataclass(frozen=True)
class Screen:
    """What Home draws, and the one thing the page does before or instead.

    `action` is "", "start_install", "load_kitchens" or
    "start_runtime_update"; `name` is the template to draw if the action
    does not take the page elsewhere.
    """
    name: str
    action: str = ""
    message: str = ""


HOME_STATES = ("not_installed", "stopped", "running", "wrong")
# Every name screen_for() can return; each must have a template.
SCREENS = frozenset([
    "account", "account:waiting", "where", "home", "kitchen:new", "kitchen:setup",
    "connect", "connect:running", "connect:ready", "connect:sign-in",
    "install:running", "unreachable", "unresponsive",
    "runtime-update:running", "runtime-update:failed"])


def newest_owned(kitchens: list[dict] | None) -> dict | None:
    """The kitchen the owner most recently made; org kitchens are never chosen."""
    owned = [k for k in kitchens or [] if k.get("is_owner")]
    return max(owned, key=lambda k: k.get("created_at") or "", default=None)


def screen_for(*, signed_in: bool, pending_sign_in: bool, kitchens: list[dict] | None,
               kitchens_error: str, vm_exists: bool, route: str, state: str,
               resumed: bool, just_signed_in: bool, update_tried: bool,
               update_error: str, problem: str) -> Screen:
    """Ordered: the earlier rule wins.

    A resumed launch still has the nothing-recorded shape and must continue
    setup, not restart it; the runtime update starts once per launch, or an
    update that "succeeds" without fixing the API would loop forever.
    """
    if resumed:
        return Screen("install:running", action="start_install")
    if not signed_in:
        return Screen("account:waiting" if pending_sign_in else "account")
    if kitchens is None and not kitchens_error:
        return Screen("home", action="load_kitchens")
    if not kitchens and not vm_exists and not kitchens_error:
        return Screen("where")
    if just_signed_in:
        newest = newest_owned(kitchens)
        if newest:
            return Screen("connect", message=newest["id"])
    if route == "update_runtime":
        if not update_tried:
            return Screen("runtime-update:running", action="start_runtime_update")
        return Screen("runtime-update:failed", message=update_error or problem)
    if route in ("unreachable", "unresponsive"):
        return Screen(route)
    return Screen("home")


_STEPS_TOTAL = 5
_SETUP_STEP_DONE = {"certificate": 0, "server": 1, "address": 2, "dns": 3}
_GETTING_READY = ("provisioning", "booting", "starting")


def setup_progress(status: str, step: str | None) -> tuple[int, int]:
    """How many of the five setup rows are done, for the "N of 5 done" line."""
    if status == "running":
        return (_STEPS_TOTAL, _STEPS_TOTAL)
    if step in _SETUP_STEP_DONE:
        return (_SETUP_STEP_DONE[step], _STEPS_TOTAL)
    # Past DNS, waiting for the server to answer.
    if status in ("booting", "starting"):
        return (4, _STEPS_TOTAL)
    return (0, _STEPS_TOTAL)


def cloud_card(row: dict) -> dict:
    status = row.get("status") or ""
    if status == "running":
        state = "running"
    elif status in _GETTING_READY:
        state = "getting_ready"
    elif status == "stopped":
        state = "stopped"
    elif status == "failed":
        state = "failed"
    else:
        state = "other"
    host = row.get("console_hostname") or ""
    line = " \u00b7 ".join(
        part for part in (host, row.get("size_label"), row.get("region_label")) if part)
    return {
        "id": row["id"],
        "title": row.get("display_name") or row.get("name") or "",
        "state": state,
        "badge": "Running" if state == "running" else "",
        "line": line,
        "steps_done": setup_progress(status, row.get("step"))[0],
        "can_connect": bool(row.get("is_owner")) and status == "running",
        "open_url_host": host,
    }


def setup_rows(status: str, step: str | None) -> list[str]:
    """Each 1e row's state: done, running, failed or waiting."""
    done, total = setup_progress(status, step)
    current = "failed" if status == "failed" else "running"
    return ["done" if i < done else current if i == done else "waiting" for i in range(total)]


# --- making a cloud kitchen (1d) -----------------------------------------
# Same rules as the web app's size tiles; prices and room are the service's call.

def _money(price: dict) -> str:
    major = price["amount"] / 100
    number = f"{major:.0f}" if major == int(major) else f"{major:.2f}"
    currency = str(price.get("currency") or "").upper()
    return f"${number}" if currency == "USD" else f"{currency} {number}"


def size_tiles(instance_catalog: dict, billing_catalog: dict | None) -> list[dict]:
    billing = billing_catalog or {}
    prices = {p.get("size_id"): p for p in billing.get("sizes") or []}
    tiles = []
    for size in instance_catalog.get("sizes") or []:
        line = " · ".join(part for part in (
            f"{size['vcpus']} vCPU" if size.get("vcpus") is not None else "",
            f"{size['memory_gb']} GB" if size.get("memory_gb") is not None else "",
            size.get("description") or "") if part)
        tile = {"id": size["id"], "label": size.get("label") or size["id"], "line": line,
                "price": "", "money": "", "cents": None, "note": "", "enabled": True}
        price = prices.get(size["id"]) or {}
        if not size.get("enabled"):
            tile.update(note="Coming soon", enabled=False)
        elif not billing.get("enabled"):
            # Billing off: the service creates the kitchen with no payment.
            pass
        elif price.get("covered_by_license"):
            tile.update(note="Covered by your license")
        elif not price.get("monthly"):
            tile.update(note="No price yet", enabled=False)
        else:
            money = _money(price["monthly"])
            tile.update(price=f"{money}/mo", money=money, cents=price["monthly"]["amount"])
        tiles.append(tile)
    return tiles


def from_price(tiles: list[dict]) -> str:
    """1c's "From $24 a month": the cheapest size that can be picked, or ""."""
    priced = [t for t in tiles if t["enabled"] and t["cents"] is not None]
    return min(priced, key=lambda t: t["cents"])["money"] if priced else ""


def region_tiles(instance_catalog: dict, size_id: str) -> list[dict]:
    tiles = []
    for region in instance_catalog.get("regions") or []:
        if not region.get("enabled"):
            note = "Coming soon"
        elif size_id not in (region.get("size_ids_with_room") or []):
            note = "No room right now"
        else:
            note = ""
        tiles.append({"id": region["id"], "label": region.get("label") or region["id"],
                      "enabled": not note, "note": note})
    return tiles


def default_kitchen_name(first_name: str) -> str:
    first = (first_name or "").strip()
    return f"{first}’s cloud" if first else "My cloud"


def checkout_url(web_origin: str, name: str, size: str, region: str) -> str:
    query = urlencode({"name": name, "size": size, "region": region, "source": "desktop"})
    return f"{web_origin.rstrip('/')}/kitchens/new/checkout?{query}"


def new_kitchen(before_ids: frozenset[str], rows: list[dict]) -> dict | None:
    """The kitchen the payment made: the newest owned one not there before it."""
    return newest_owned([row for row in rows if row.get("id") not in before_ids])


_CHECKOUT_SLOW_AFTER = 120.0


def checkout_wait(elapsed: float) -> float:
    """Seconds to the next look for the paid kitchen. Never gives up: a cancelled
    payment looks like an open one, so only Back ends the wait."""
    return 3.0 if elapsed < _CHECKOUT_SLOW_AFTER else 10.0


def local_card(route: str, state: str) -> str:
    if route == "home":
        return {"running": "running", "stopped": "stopped",
                "not_installed": "none"}.get(state, "failed")
    return "failed"


def home_view(kitchens: list[dict] | None, kitchens_error: str, local: str,
              vm_exists: bool, agents: dict[str, list[str]] | None = None) -> dict:
    """Cards in order (cloud, then local) and which kind gets the dashed card.
    `agents` names each card's connected agents by card id ("local" for this computer)."""
    agents = agents or {}
    cards = [{"kind": "cloud", **cloud_card(k)} for k in kitchens or []]
    has_cloud = bool(cards)
    if local != "none":
        cards.append({"kind": "local", "id": "local", "state": local})
    for card in cards:
        card["agents"] = list(agents.get(card["id"], []))
    if kitchens_error:
        # Offering to create a kitchen while the service is down would only fail.
        missing = ""
    elif has_cloud and local == "none":
        missing = "local"
    elif local != "none" and not has_cloud:
        missing = "cloud"
    else:
        missing = ""
    return {"cards": cards, "missing": missing, "show_doctor": vm_exists,
            "cloud_error": kitchens_error}


def agent_cards(manifests, *, kind: str, alias: str | None,
                installed: dict[str, bool], connected: dict[str, bool],
                signed_in: dict[str, bool] | None = None) -> list[dict]:
    """3a's agent cards for one kitchen, in manifest order. `kind` and `alias`
    are the kitchen's ("local" / "cloud", its ssh host or None on Windows' own VM).
    `signed_in` only adds to an installed card's line."""
    cards = []
    for m in manifests:
        if connected.get(m.id):
            state = "connected"
        elif installed.get(m.id):
            state = "installed"
        else:
            state = "missing"
        cards.append({"id": m.id, "name": m.name, "icon": m.icon, "state": state,
                      "kind": kind, "ssh_host": alias or "",
                      "signed_in": state == "installed" and bool((signed_in or {}).get(m.id))})
    return cards


def connect_targets(kitchens: list[dict] | None, local: str) -> list[dict]:
    """3a's Connect to: the owner's cloud kitchens, then this computer when it has a
    VM (`local` is local_card's state). One that isn't running is listed, not pickable.
    A kitchen someone else owns is left out: only its owner can let a computer in."""
    targets = []
    for row in kitchens or []:
        if row.get("is_owner"):
            card = cloud_card(row)
            targets.append({"id": card["id"], "title": card["title"], "kind": "cloud",
                            "state": card["state"], "enabled": card["state"] == "running"})
    if local != "none":
        targets.append({"id": "local", "title": LOCAL, "kind": "local",
                        "state": local, "enabled": local == "running"})
    return targets


def route_for(readiness: Readiness) -> tuple[str, str]:
    """`(route, state)` for a probe result.

    Ordered to match `status.probe()`'s own early returns, with one
    deliberate exception: `problem` is checked before `vm_exists`. A provider
    that threw in `exists()` reports vm_exists=False, which is shape-identical
    to "no VM yet" -- and routing that to "not installed" would offer a Set up
    button on a machine where setup cannot run.
    """
    if readiness.unresponsive:
        # Ahead of "unreachable": its Repair runs through the very command
        # that is hanging.
        return ("unresponsive", "")
    if readiness.problem:
        # The api service refusing /health is the only failure the unreachable
        # screen describes truthfully: its copy claims we can see the machine
        # humming, which is only established once `true` ran in it and the
        # runtime marker was read.
        if readiness.vm_reachable and readiness.runtime_version:
            return ("unreachable", "")
        return ("home", "wrong")
    if not readiness.vm_exists:
        return ("home", "not_installed")
    if not readiness.vm_reachable:
        return ("home", "stopped")
    if not readiness.runtime_version:
        return ("home", "wrong")
    if readiness.api_version not in constants.SUPPORTED_API:
        return ("update_runtime", "")
    return ("home", "running")


from host.core.install import Step

# Carried over from host/setup_app/wizard.py. install_runtime is absent on
# purpose: its text comes from provider.runtime().label, because the version
# in it is Lima's fact, not the installer's -- a second copy here would go
# stale the first time the pinned version changes.
STEP_LABELS = {
    "preflight": "Checking this computer",
    "remediate": "Turning on Windows features",
    "reboot_gate": "Restart needed",
    "fetch_image": "Downloading Linux image",
    "create_vm": "Preparing the virtual machine",
    "ssh_alias": "Adding the eggie SSH host",
    "bootstrap": "Installing Eggie",
    "connect": "Connecting to the Eggie service",
    "verify": "Testing the setup",
    "finish": "Finishing up",
}


def step_label(step: Step) -> str:
    return step.label or STEP_LABELS.get(step.name, step.name)


def rows_for(steps: list[Step]) -> list[dict]:
    """One row per step the factory actually returned.

    Never a fixed seven: default_steps drops install_runtime on Windows (wsl
    ships with the OS) and drops remediate, reboot_gate and fetch_image on
    macOS (no features to enable, and limactl fetches its own image). The
    "Step N of M" counter is derived from this list for the same reason.
    """
    return [{"name": s.name, "label": step_label(s), "progress": s.progress}
            for s in steps]


from host.core.install import DeadEnd, InstallError, Progress, RebootRequired


def progress_event(progress: Progress) -> dict:
    return {
        "type": "step",
        "step": progress.step,
        "status": progress.status,
        "message": progress.message,
        # None, never 0.0: only a step that declared progress=True has a
        # fraction, and a zero here would draw an empty bar on every other row.
        "fraction": progress.fraction,
    }


def terminal_event(exc: BaseException | None) -> dict:
    """How the run ended, in the three shapes the board draws differently.

    run_install has already reported a `failed` Progress naming the row, so
    nothing here needs the step name -- only the ending, because each offers
    a different pair of buttons.
    """
    if exc is None:
        return {"type": "done"}
    if isinstance(exc, RebootRequired):
        return {"type": "reboot"}
    if isinstance(exc, DeadEnd):
        # No code can fix this one, so the failed screen's "Try this step
        # again" is the wrong offer and the UI hides it for this type.
        return {"type": "dead_end", "message": f"{exc}"}
    if isinstance(exc, InstallError):
        return {"type": "failed", "message": exc.message, "action": exc.action}
    return {"type": "failed", "message": f"{exc}", "action": ""}
