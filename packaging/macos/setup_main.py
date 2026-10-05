"""Entry point for the Eggie.app bundle.

The CLI and the setup window are one program with two front doors, exactly as
on Windows — where the installer's shortcut runs `setup.exe setup`, passing the
subcommand itself. Finder passes no arguments at all, so a bundle whose main
executable were the bare CLI would open, print its help to a console nobody is
watching, and exit. This adds the missing word.
"""
import sys

from host.cli import app

# LaunchServices still appends a process serial number to some apps it opens.
# It is not a command, and click would reject the whole invocation over it.
args = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
app(args or ["setup"])
