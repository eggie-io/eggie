import os
import sys
from pathlib import Path

from .wsl2 import Wsl2Provider
from .lima import LimaProvider, default_data_root, find_limactl
from .desktop_win import WindowsDesktop
from .desktop_mac import MacDesktop
from .lima_install import managed_limactl


def default_install_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / "Eggie" / "vm"
    return default_data_root() / "vm"


def get_desktop():
    """The platform the window lives on, as distinct from the VM it drives."""
    if sys.platform == "win32":
        return WindowsDesktop()
    if sys.platform == "darwin":
        return MacDesktop()
    raise RuntimeError(f"unsupported host platform: {sys.platform} "
                       "(only Windows/WSL2 and macOS/Lima are supported)")


def get_provider():
    if sys.platform == "win32":
        rootfs = Path(os.environ["EGGIE_ROOTFS"]) if os.environ.get("EGGIE_ROOTFS") else None
        return Wsl2Provider(install_dir=default_install_dir(), rootfs=rootfs)
    if sys.platform == "darwin":
        # Resolved here rather than left to PATH: the provider is handed a path
        # the same way the WSL2 one is handed a rootfs, and the setup window
        # runs without a shell's PATH. See find_limactl. The managed copy --
        # what setup's install_runtime step put under data_root -- wins over
        # any Homebrew Lima on this same machine.
        root = default_data_root()
        return LimaProvider(config=Path(__file__).parent / "eggie.yaml",
                            limactl=find_limactl(managed=managed_limactl(root)),
                            data_root=root)
    raise RuntimeError(f"unsupported host platform: {sys.platform} "
                       "(only Windows/WSL2 and macOS/Lima are supported)")
