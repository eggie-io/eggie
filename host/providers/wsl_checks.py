import re

from ..core.provider import Diagnosis, CheckResult
from .wsl_encoding import decode_wsl

MIN_BUILD = 19045
MIN_FREE_GB = 10.0


def diagnose_wsl2(runner, wsl="wsl.exe") -> Diagnosis:
    checks: list[CheckResult] = []

    ver = runner([wsl, "--version"])
    ver_text = decode_wsl(ver.stdout)
    has_version = ver.returncode == 0 and "WSL version" in ver_text
    checks.append(CheckResult(
        "wsl --version available", has_version,
        None if has_version else "run `wsl --update` (in-box WSL is too old)"))

    # Conflicts (Hyper-V / VirtualBox / AV) silently break VM creation.
    # We cannot always detect them; surface guidance as a non-blocking note.
    checks.append(CheckResult(
        "no known hypervisor conflict", True,
        "if create fails: check VirtualBox/antivirus and that "
        "VirtualMachinePlatform is enabled"))

    return Diagnosis(checks)


def parse_wsl_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"WSL version:\s*([\d.]+)", text)
    if not match:
        return None
    return tuple(int(p) for p in match.group(1).split("."))


def preflight_checks(*, wsl_version_text: str, build: int,
                     hypervisor_present: bool, firmware_virtualization: bool,
                     free_gb: float, wsl_features_enabled: bool) -> Diagnosis:
    checks: list[CheckResult] = []

    build_ok = build >= MIN_BUILD
    checks.append(CheckResult(
        f"Windows build {build} supports WSL2", build_ok,
        None if build_ok else
        f"Windows build {MIN_BUILD} or newer is required; run Windows Update",
        code=None if build_ok else "windows_build_too_old"))

    # A running hypervisor proves virtualization works even when the firmware
    # flag reads False, which it does once Hyper-V has claimed the CPU.
    virt_ok = hypervisor_present or firmware_virtualization
    checks.append(CheckResult(
        "CPU virtualization available", virt_ok,
        None if virt_ok else
        "Restart into BIOS/UEFI setup and enable Intel VT-x or AMD-V "
        "(often called 'Virtualization Technology' or 'SVM Mode')",
        code=None if virt_ok else "cpu_virtualization_off"))

    checks.append(CheckResult(
        "Windows virtualization features enabled", wsl_features_enabled,
        None if wsl_features_enabled else
        "We'll turn on the Windows features WSL2 needs "
        "(Virtual Machine Platform and Windows Subsystem for Linux) "
        "for you; your PC will need to restart afterward",
        remedy=None if wsl_features_enabled else "enable_wsl_features"))

    wsl_ok = parse_wsl_version(wsl_version_text) is not None
    checks.append(CheckResult(
        "Store WSL installed", wsl_ok,
        None if wsl_ok else "we will install it for you",
        remedy=None if wsl_ok else "update_wsl"))

    disk_ok = free_gb >= MIN_FREE_GB
    checks.append(CheckResult(
        f"At least {MIN_FREE_GB:.0f} GB free disk space", disk_ok,
        None if disk_ok else
        f"only {free_gb:.1f} GB free; free up space and run setup again",
        code=None if disk_ok else "low_disk_space"))

    return Diagnosis(checks)
