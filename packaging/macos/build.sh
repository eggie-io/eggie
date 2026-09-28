#!/usr/bin/env bash
# Builds Omelet.app and wraps it in a double-clickable .pkg.
#
# The Windows twin (build.ps1) is the shape to follow: freeze, smoke-test the
# frozen binary before packaging it, then hand the result to the OS installer
# builder. A build that only proves "PyInstaller exited 0" ships a bundle whose
# missing asset the user discovers mid-setup.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
here="$repo/packaging/macos"
venv_python="$repo/.venv/bin/python"
app="$repo/dist/Omelet.app"
staging="$repo/build/pkgroot"

version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$repo/pyproject.toml" | head -1)"
[ -n "$version" ] || { echo "no version in pyproject.toml" >&2; exit 1; }
pkg="$repo/dist/OmeletSetup-$version-$(uname -m).pkg"

[ -x "$venv_python" ] || {
    echo "No virtualenv at $venv_python. Run: python3.12 -m venv .venv" >&2; exit 1; }
"$venv_python" -c "import PyInstaller" 2>/dev/null || {
    echo "PyInstaller missing. Run: $venv_python -m pip install -e \".[dev]\"" >&2; exit 1; }
# omelet.spec names this as a hidden import by string. If the name is wrong,
# PyInstaller silently omits it -- the build still succeeds, and the only
# symptom is a user's double-click reporting "install the Edge WebView2
# runtime" (the Windows message; on the Mac it's a generic pywebview
# failure), which is the wrong diagnosis for a packaging bug. Failing here,
# on the machine that actually has pywebview installed, turns that into a
# build-time error instead.
"$venv_python" -c "import webview.platforms.cocoa" 2>/dev/null || {
    echo "pywebview's Cocoa backend did not import; the hidden import in omelet.spec is wrong." >&2
    exit 1; }

echo "==> Building Omelet $version for $(uname -m)"
cd "$repo"
export OMELET_VERSION="$version"
"$venv_python" -m PyInstaller --noconfirm --clean "$here/omelet.spec"

cli="$app/Contents/MacOS/omelet"
[ -x "$cli" ] || { echo "$cli was not built." >&2; exit 1; }
gui="$app/Contents/MacOS/omelet-setup"
[ -x "$gui" ] || { echo "$gui was not built." >&2; exit 1; }
# Both executables live in one directory on a case-insensitive filesystem, where
# two names differing only in case are one file. Counting them is what catches
# a rename that collides; the build otherwise succeeds and ships one binary.
count="$(ls -1 "$app/Contents/MacOS" | wc -l | tr -d ' ')"
[ "$count" = "2" ] || {
    echo "expected 2 executables in Contents/MacOS, found $count:" >&2
    ls -1 "$app/Contents/MacOS" >&2; exit 1; }

# `version` never touches disk, so it passes on a bundle missing a datas entry;
# selfcheck resolves each bundled asset the way its real caller does, and now
# also imports the four desktop modules and the webview backend the spec's
# hiddenimports names above -- `set -e` turns either failure into a build
# failure, not a user's.
"$cli" version
"$cli" selfcheck
# The spec builds two Analysis objects with two separate PYZs (see omelet.spec)
# because the CLI and the GUI differ in more than a console flag -- the desktop
# modules and the webview backend only need to resolve into the GUI's own
# bundle. Running selfcheck against "$cli" alone proved the wrong binary: a
# HIDDEN entry dropped from (or diverging on) the `gui = Analysis(...)` line
# would still leave the CLI's selfcheck printing OK lines and this script
# exiting 0, while Omelet.app -- what a double-click actually launches --
# showed a Dock icon and died on its first draw. That is the exact failure
# selfcheck exists to catch.
"$gui" selfcheck

# The bundle is only an app if its plist points at the windowed executable and
# does not mark it background-only. BUNDLE infers both wrongly here (see the
# spec), so the corrections are asserted rather than trusted.
main_exe="$(/usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" \
    "$app/Contents/Info.plist")"
[ "$main_exe" = "omelet-setup" ] || {
    echo "Omelet.app would launch '$main_exe', not the setup window." >&2; exit 1; }
if /usr/libexec/PlistBuddy -c "Print :LSBackgroundOnly" \
       "$app/Contents/Info.plist" 2>/dev/null | grep -q true; then
    echo "Omelet.app is marked background-only; its window cannot come forward." >&2
    exit 1
fi

# Signing is optional and off by default: a Developer ID costs money and a PoC
# build is installed by the person who made it. Unsigned, Gatekeeper requires a
# right-click > Open on the .pkg — see docs/release-testing.md.
#
# --deep re-signs every Mach-O in the bundle, which is right while everything in
# it comes from PyInstaller. It stops being right the moment a third-party
# binary is bundled: limactl is ad-hoc signed by Lima with
# com.apple.security.virtualization (vz.entitlements), Apple's Virtualization
# framework refuses vz without it, and re-signing here would drop it silently --
# breaking signed builds only, on the user's machine. Bundle limactl and this
# has to sign it separately, with those entitlements.
if [ -n "${OMELET_CODESIGN_ID:-}" ]; then
    echo "==> Signing the app as $OMELET_CODESIGN_ID"
    codesign --force --deep --options runtime --timestamp \
        --sign "$OMELET_CODESIGN_ID" "$app"
    codesign --verify --strict --verbose=2 "$app"
fi

rm -rf "$staging"
mkdir -p "$staging"
cp -R "$app" "$staging/"

# Component plist first: without it pkgbuild marks the app relocatable, and the
# installer would silently retarget an Omelet.app the user had moved elsewhere
# instead of writing to /Applications.
component="$repo/build/component.plist"
pkgbuild --analyze --root "$staging" "$component" >/dev/null
"$venv_python" - "$component" <<'PY'
import plistlib, sys
path = sys.argv[1]
with open(path, "rb") as fh:
    bundles = plistlib.load(fh)
for bundle in bundles:
    bundle["BundleIsRelocatable"] = False
with open(path, "wb") as fh:
    plistlib.dump(bundles, fh)
PY

mkdir -p "$repo/dist"
component_pkg="$repo/build/Omelet-component.pkg"
pkgbuild --root "$staging" \
         --component-plist "$component" \
         --identifier dev.omelet.app \
         --version "$version" \
         --install-location /Applications \
         --scripts "$here/scripts" \
         "$component_pkg"

distribution="$repo/build/distribution.xml"
sed -e "s/@VERSION@/$version/g" -e "s/@ARCH@/$(uname -m)/g" \
    "$here/distribution.xml" > "$distribution"

productbuild_args=(--distribution "$distribution"
                   --package-path "$repo/build"
                   --resources "$here/resources")
if [ -n "${OMELET_INSTALLER_ID:-}" ]; then
    echo "==> Signing the installer as $OMELET_INSTALLER_ID"
    productbuild_args+=(--sign "$OMELET_INSTALLER_ID" --timestamp)
fi
productbuild "${productbuild_args[@]}" "$pkg"

echo "==> dist/$(basename "$pkg")"
if [ -z "${OMELET_INSTALLER_ID:-}" ]; then
    echo "    Unsigned: Gatekeeper blocks a double-click on a downloaded copy."
    echo "    Open it with right-click > Open, or run: installer -pkg \"$pkg\" -target /"
fi
