#!/usr/bin/env bash
# Removes Eggie, the VM, and every project inside it.
#
# macOS .pkg has no uninstaller of its own, so this is the counterpart to the
# Inno [UninstallRun] entry on Windows -- same order, and the same warning:
# project files live inside the VM, not on this Mac.
#
# Run it as yourself, NOT with sudo. Lima keeps VMs per user under ~/.lima, so
# root would look for a VM that is not there and leave yours running; the two
# steps that need root ask for it themselves.
set -euo pipefail

app="/Applications/Eggie.app"
cli="$app/Contents/MacOS/eggie"
link="/usr/local/bin/eggie"

if [ "$(id -u)" = "0" ]; then
    echo "Run this as yourself, without sudo: the VM belongs to your account." >&2
    exit 1
fi

cat <<'WARNING'
This permanently deletes the Eggie virtual machine and every project inside
it. Project files live in the VM, not on this Mac, so nothing is recoverable
afterwards.
WARNING
read -r -p "Continue? [y/N] " reply
case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Nothing was removed."; exit 1 ;;
esac

# Quit through the app's own terminate path, which does not stop the VM: the
# purge below destroys it anyway, and a running app would keep the bundle busy.
osascript -e 'quit app "Eggie"' >/dev/null 2>&1 || true
for _ in 1 2 3 4 5; do
    pgrep -f "$app/Contents/MacOS/" >/dev/null 2>&1 || break
    sleep 1
done

if [ -x "$cli" ]; then
    "$cli" uninstall --purge || echo "The VM could not be removed; continuing." >&2
elif command -v eggie >/dev/null 2>&1; then
    eggie uninstall --purge || echo "The VM could not be removed; continuing." >&2
else
    echo "No eggie command found; skipping the VM." >&2
fi

echo "Removing the app (this asks for your password)."
sudo rm -rf "$app"
[ -L "$link" ] && sudo rm -f "$link"
# Without this the installer still believes a newer Eggie is present and will
# refuse to downgrade a later reinstall.
sudo pkgutil --forget io.eggie.app >/dev/null 2>&1 || true

echo "Removed."
