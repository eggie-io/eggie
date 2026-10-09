from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from .commands import cmd_clone, cmd_down, cmd_logs, cmd_new, cmd_status, cmd_up
from .env import Env
from .errors import EggieError
from .secrets import cmd_secret_list, cmd_secret_request, cmd_secret_rm, cmd_secret_set


def _secret_usage_error(message: str) -> NoReturn:
    # argparse would quote the offending argument, which may be a typed value.
    print('usage: eggie secret set NAME | request NAME "hint" | list | rm NAME'
          " — values are never taken on the command line", file=sys.stderr)
    sys.exit(2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eggie",
        description="Run projects in this VM behind Eggie's router. Projects "
                    "live in ~/projects, one folder each. Start them only with "
                    "`eggie up`, never `docker compose up`.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    up = sub.add_parser("up", help="start or restart the project in this folder "
                                   "and print its URL")
    up.add_argument("directory", nargs="?")
    status = sub.add_parser("status", help="show this project, or every project")
    status.add_argument("directory", nargs="?")
    logs = sub.add_parser("logs", help="show what this project's containers printed")
    logs.add_argument("service", nargs="?")
    sub.add_parser("down", help="stop the project in this folder")
    new = sub.add_parser("new", help="create an empty project folder in ~/projects")
    new.add_argument("name")
    clone = sub.add_parser("clone", help="download a git repository into "
                                         "~/projects and start it")
    clone.add_argument("url")
    clone.add_argument("name", nargs="?")
    secret = sub.add_parser("secret", help="keys and passwords this project's "
                                           "containers get as environment variables")
    secret.error = _secret_usage_error
    secret_sub = secret.add_subparsers(dest="secret_command", required=True,
                                       metavar="<action>")
    secret_set = secret_sub.add_parser(
        "set", help="save a secret; the value is read from the terminal or stdin")
    secret_set.error = _secret_usage_error
    secret_set.add_argument("name")
    secret_set.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    secret_request = secret_sub.add_parser(
        "request", help="ask the owner for a secret; shows on the project's Secrets page")
    secret_request.error = _secret_usage_error
    secret_request.add_argument("name")
    secret_request.add_argument("hint")
    secret_sub.add_parser("list", help="show secret names and open requests")
    secret_rm = secret_sub.add_parser("rm", help="remove a secret or a request")
    secret_rm.error = _secret_usage_error
    secret_rm.add_argument("name")
    return parser


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if args.command == "secret":
            _secret_usage_error("")
        parser.error("unrecognized arguments: " + " ".join(extra))
    env = env or Env()
    commands = {
        "up": lambda: cmd_up(env, args.directory),
        "status": lambda: cmd_status(env, args.directory),
        "logs": lambda: cmd_logs(env, args.service),
        "down": lambda: cmd_down(env),
        "new": lambda: cmd_new(env, args.name),
        "clone": lambda: cmd_clone(env, args.url, args.name),
        "secret": lambda: {
            "set": lambda: cmd_secret_set(env, args.name, args.extra),
            "request": lambda: cmd_secret_request(env, args.name, args.hint),
            "list": lambda: cmd_secret_list(env),
            "rm": lambda: cmd_secret_rm(env, args.name),
        }[args.secret_command](),
    }
    try:
        commands[args.command]()
    except EggieError as e:
        print(str(e), file=env.err)
        return 1
    return 0


def run() -> NoReturn:
    # zipapp's generated __main__ calls the entry point and drops its return
    # value; this is what the archive runs so a handled error still exits 1.
    sys.exit(main())


if __name__ == "__main__":
    run()
