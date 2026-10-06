#!/usr/bin/env bash
# Installs the runtime into this VM. Run as root by get.sh from the unpacked
# runtime/install directory; re-running it is safe.
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(dirname "$INSTALL_DIR")"

REF="${1:?usage: install.sh <runtime ref> [--repair]}"
REPAIR=0
if [[ "${2:-}" == --repair ]]; then
  REPAIR=1
fi
SKILLS_CLI=skills@1.5.26
# Unpinned on purpose: pinning a tag here is a value change, not a code change.
# Until it is pinned, a box's skills are not identifiable from runtime.version.
SKILLS_SOURCE="${EGGIE_SKILLS_SOURCE:-eggie-io/eggie-skills}"
# skills@1.5.26 declares node >=22.20.0; Ubuntu 24.04's own nodejs is 18.
NODE_MIN=22.20.0
TOKEN_CREATED=0

# A failed reinstall must not look installed; get.sh already resolved the ref.
rm -f /opt/eggie/runtime.version

export DEBIAN_FRONTEND=noninteractive

# 1. docker-ce from the official repo.
# Guard on the package, not on `command -v docker`: Docker Desktop's WSL
# integration puts its own docker CLI on PATH, which made this skip the install
# and then fail at `systemctl enable` with no docker.service.
if ! dpkg -s docker-ce >/dev/null 2>&1; then
  if ! { apt-get update && apt-get install -y ca-certificates curl; }; then
    echo "could not install Docker: download.docker.com or the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
  install -m 0755 -d /etc/apt/keyrings
  if ! curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      -o /etc/apt/keyrings/docker.asc; then
    echo "could not install Docker: download.docker.com or the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  if ! { apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; }; then
    echo "could not install Docker: download.docker.com or the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
fi

# setfacl, for steps 4 and 11. Lima's Ubuntu image ships without it.
if ! dpkg -s acl >/dev/null 2>&1; then
  if ! { apt-get update && apt-get install -y acl; }; then
    echo "could not install acl: the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
fi

# Absolute path throughout: if Docker Desktop's CLI is on PATH it would
# talk to Desktop's engine instead of this VM's dockerd.
test -x /usr/bin/docker || { echo 'docker-ce did not install /usr/bin/docker' >&2; exit 1; }

# 2. enable docker (systemd must be on — set via wsl.conf during create())
systemctl enable --now docker

# 3. edge network (idempotent)
/usr/bin/docker network inspect edge >/dev/null 2>&1 || /usr/bin/docker network create edge

# 4. project root, group-writable before anything starts.
# The API container runs as a non-root user, and its only shared credential
# with this VM is the docker group it joins via stack.yml's group_add -- so the
# group, not an image-specific uid the host would have to keep in sync, is what
# /opt/eggie opens up to. Anything in the docker group is already root-
# equivalent here, so this grants no access it did not have. setgid makes the
# project directories the API creates later inherit the group; without it the
# API cannot even open /opt/eggie/state.db and restart:always loops it.
mkdir -p /opt/eggie/projects
chgrp -R docker /opt/eggie
chmod -R g+rwX /opt/eggie
find /opt/eggie -type d -exec chmod g+s {} +
# The API runs under umask 022, so what it creates in a project would come out
# 2755 and shut the login accounts out. A default ACL replaces the umask for
# new entries, whoever makes them: the API, a container, a coding agent.
find /opt/eggie/projects -type d -exec setfacl -d -m g::rwx {} +

# The GitHub token is the API's alone: the sweep above just widened it, so
# its modes are put back every run.
install -d -m 2770 -o root -g docker /opt/eggie/github
chmod 2770 /opt/eggie/github
[[ -e /opt/eggie/github/token ]] && chmod 600 /opt/eggie/github/token
[[ -e /opt/eggie/github/desired.json ]] && chmod 640 /opt/eggie/github/desired.json

# 5. this VM's real docker GID, for stack.yml's group_add.
# The chgrp above used whatever GID this VM's docker group has, while the API
# image bakes in 999 -- where those differ the API can write neither
# /opt/eggie nor the socket. Compose reads .env from the directory holding the
# compose file, so writing it here is all the wiring needed.
if ! DOCKER_GID="$(getent group docker | cut -d: -f3)" || [[ -z "$DOCKER_GID" ]]; then
  echo 'no docker group in this VM after installing docker-ce' >&2
  exit 1
fi
# The release tag is the only copy of the image version; stack.yml reads it from here.
# shellcheck disable=SC1091
[[ -s /opt/eggie/runtime.env ]] && source /opt/eggie/runtime.env
if ! IMAGE_VERSION="$(bash "$INSTALL_DIR/lib/image-version.sh" "$REF" "${EGGIE_RUNTIME_REPO:-https://github.com/eggie-io/eggie}")"; then
  exit 1
fi
printf 'EGGIE_DOCKER_GID=%s\nEGGIE_VERSION=%s\n' "$DOCKER_GID" "$IMAGE_VERSION" > /opt/eggie/.env

# 6. the shared secret between the host and the API.
# Only if absent: bootstrap re-runs are normal, and regenerating it every
# time would invalidate a token the host is already holding. Must land
# before the API starts, and after the chmod sweep above or its mode gets
# widened along with everything else.
# The pipeline reads exactly 32 bytes from /dev/urandom before anything
# downstream sees them: bounding an infinite `tr < /dev/urandom` with a
# later `head -c` instead kills tr with SIGPIPE the moment head stops
# reading, and set -o pipefail then fails the whole script over a byte count
# that was never wrong.
if [[ ! -s /opt/eggie/api.token ]]; then
  TOKEN_CREATED=1
  # `install` sets the mode on creation, before any content lands in the
  # file -- a plain `>` redirect creates it under root's umask (644) first
  # and only narrows it on the next line, leaving it briefly world-readable.
  install -m 640 /dev/null /opt/eggie/api.token
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > /opt/eggie/api.token
fi
# Root-owned but group-readable: the API's own uid is non-root, and docker
# group membership is already root-equivalent here (it owns the socket), so
# it is the group a credential the API itself must read has to grant.
# Reasserted every run, not just on first creation, so a pre-existing file
# from before this ever ran still ends up correct.
chgrp docker /opt/eggie/api.token
chmod 640 /opt/eggie/api.token

# 7. traefik, the API and the web page, as one compose stack.
# Always pull: this is how an API update reaches an already-provisioned VM,
# so both the first install and every update need the network.
# --profile tunnel: the first public URL must not wait on an image download.
install -m 644 "$RUNTIME_DIR/stack.yml" /opt/eggie/stack.yml
# The output is kept as well as shown. An image with no build for this VM's
# architecture and a registry that cannot be reached fail the same way here and
# differ only in the daemon's wording -- and the two need opposite things from
# the user. Reporting "check your network and proxy" to someone whose registry
# answered perfectly sends them to inspect the one part that is working; on
# Apple Silicon, where an amd64-only image is the common case, that is the first
# thing they hit.
PULL_LOG="$(mktemp)"
if ! /usr/bin/docker compose -f /opt/eggie/stack.yml --profile tunnel pull 2>&1 | tee "$PULL_LOG"; then
  if grep -qiE 'no matching manifest|no match for platform' "$PULL_LOG"; then
    echo "could not pull the Eggie images: one of them is not published for" >&2
    echo "this machine's architecture ($(uname -m)). The registry answered" >&2
    echo "fine -- that image needs a build for this architecture." >&2
  else
    echo "could not pull the Eggie images: the registry was unreachable." >&2
    echo "Check the network connection or proxy and run setup again." >&2
  fi
  rm -f "$PULL_LOG"
  exit 1
fi
rm -f "$PULL_LOG"
/usr/bin/docker compose -f /opt/eggie/stack.yml up -d
# The API reads its token once, at startup, and `up -d` leaves an unchanged
# container running.
if (( TOKEN_CREATED || REPAIR )); then
  /usr/bin/docker compose -f /opt/eggie/stack.yml up -d --force-recreate api
fi

# 8. git for `eggie clone`, gh for GitHub work, Node for `npx skills`.
if ! dpkg -s git >/dev/null 2>&1; then
  if ! { apt-get update && apt-get install -y git; }; then
    echo "could not install git: the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
fi
# Ubuntu 24.04 packages no gh, so this is GitHub's own repo. Its keyring ships
# dearmored, unlike NodeSource's, so no gpg and no gnupg dependency here.
# Guarded on the package rather than `command -v gh`: a gh reaching this VM from
# somewhere else -- a Docker Desktop mount, a user's own install -- would skip
# the repo too, and every later upgrade with it. That is the docker-ce trap.
if ! dpkg -s gh >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  if ! curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; then
    echo "could not reach cli.github.com to install the GitHub CLI" >&2
    exit 1
  fi
  chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  if ! { apt-get update && apt-get install -y gh; }; then
    echo "could not install the GitHub CLI: cli.github.com or the Ubuntu package mirrors may be unreachable" >&2
    exit 1
  fi
fi

# The GitHub clone runs as the API's uid; the agents' accounts differ from it
# and are all docker-group, root-equivalent here, so the ownership check
# guards nothing. git 2.43 has no prefix form of this setting.
if ! git config --system --get-all safe.directory 2>/dev/null | grep -qxF '*'; then
  git config --system --add safe.directory '*'
fi

node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local have
  have="$(node -p 'process.versions.node')" || return 1
  [[ "$(printf '%s\n' "$NODE_MIN" "$have" | sort -V | head -n 1)" == "$NODE_MIN" ]]
}
if ! node_ok; then
  if ! command -v gpg >/dev/null 2>&1; then
    if ! { apt-get update && apt-get install -y gnupg; }; then
      echo "could not install gnupg: the Ubuntu package mirrors may be unreachable" >&2
      exit 1
    fi
  fi
  install -m 0755 -d /etc/apt/keyrings
  if ! curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg; then
    echo "could not reach NodeSource to install Node.js 22" >&2
    exit 1
  fi
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  if ! { apt-get update && apt-get install -y nodejs; }; then
    echo "could not reach NodeSource to install Node.js 22" >&2
    exit 1
  fi
  node_ok || { echo "Node.js $NODE_MIN or newer did not install" >&2; exit 1; }
fi

# 9. the in-VM eggie command, the instructions every session loads, the shell's start
#    directory, and SSH host keys that outlive a reboot.
install -m 755 "$RUNTIME_DIR/cli/eggie.py" /usr/local/bin/eggie
if ! system_instructions="$(python3 "$INSTALL_DIR/lib/agents.py" --agents-dir "$RUNTIME_DIR/agents" instructions --system)"; then
  echo "the coding-agent manifests in this runtime are damaged" >&2
  exit 1
fi
while IFS= read -r target; do
  [[ -n "$target" ]] && install -D -m 644 "$RUNTIME_DIR/instructions/eggie.md" "$target"
done <<< "$system_instructions"
install -m 644 "$INSTALL_DIR/profile/eggie-cwd.sh" /etc/profile.d/eggie-cwd.sh
# SSH host keys that survive a reboot (see the file). Lima only, detected the
# same way as step 13: on a cloud VM, a disk image cloned from this one must
# still get keys of its own, which is what cloud-init's default is for.
if [[ -d /mnt/lima-cidata && -d /etc/cloud/cloud.cfg.d ]]; then
    install -m 644 "$INSTALL_DIR/cloud/99-eggie-keep-ssh-host-keys.cfg" /etc/cloud/cloud.cfg.d/
fi

# 10. copies earlier provisioning made, which npx now owns or nothing reads.
# The last three are what an engine-v* install left behind: engine.version
# sits beside runtime.version and would mislead the next person to debug the
# box, and agent.token is a live 0640 docker-readable secret nothing reads
# any more.
rm -rf /etc/codex/skills/eggie-setup /opt/eggie/bin /opt/eggie/agents \
  /opt/eggie/.bootstrapped \
  /opt/eggie/engine /opt/eggie/engine.version /opt/eggie/agent.token \
  /etc/skel/.claude/skills/eggie-setup /etc/skel/.agents/skills/eggie-setup
if [[ -f /etc/skel/.codex/AGENTS.md ]]; then
  sed -i '\|^<!-- eggie:begin -->$|,\|^<!-- eggie:end -->$|d' /etc/skel/.codex/AGENTS.md
fi
if [[ -L /etc/skel/projects && "$(readlink /etc/skel/projects)" == /opt/eggie/projects ]]; then
  rm -f /etc/skel/projects
fi

# 11. per account: docker group, agent instructions, ~/projects, skills.
if ! skill_agents="$(python3 "$INSTALL_DIR/lib/agents.py" --agents-dir "$RUNTIME_DIR/agents" skills)"; then
  echo "the coding-agent manifests in this runtime are damaged" >&2
  exit 1
fi
read -ra skill_agents <<< "$skill_agents"
accounts() {
  echo "root:0:0:/root"
  getent passwd | bash "$INSTALL_DIR/lib/login-users.sh" /etc/shells
}
while IFS=: read -r name uid gid home; do
  if [[ "$name" != root ]]; then
    usermod -aG docker "$name"
    # And by uid: a session open before this line keeps its old group list --
    # on Lima that is every session, multiplexed over the ssh control master
    # opened at boot. -P: a project's symlinks are not followed out of it.
    setfacl -R -P -m "u:$name:rwX" /opt/eggie/projects
    find /opt/eggie/projects -type d -exec setfacl -d -m "u:$name:rwx" {} +
    setfacl -m "u:$name:r" /opt/eggie/api.token
  fi
  bash "$INSTALL_DIR/lib/install-agents.sh" "$RUNTIME_DIR" "$home" "$uid:$gid"
  # stdin is the account list this loop is reading.
  if ! runuser -u "$name" -- env HOME="$home" DISABLE_TELEMETRY=1 npx -y "$SKILLS_CLI" add "$SKILLS_SOURCE" -s '*' -g -a "${skill_agents[@]}" -y </dev/null; then
    echo "could not install Eggie's skills for $name: the npm registry or GitHub may be unreachable" >&2
    exit 1
  fi
done < <(accounts)

# 12. GitHub: apply on every change of the API's desired state, and once now
# so a repair or a newly added account catches up.
install -m 644 "$INSTALL_DIR/systemd/eggie-github.path" \
  "$INSTALL_DIR/systemd/eggie-github.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eggie-github.path
if ! systemctl start eggie-github.service; then
  echo "could not apply the GitHub connection to this machine's accounts" >&2
  exit 1
fi

# 12b. the boot-time updater. Enabled only: starting it here would run an
# update inside this install.
install -m 644 "$INSTALL_DIR/systemd/eggie-update.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable eggie-update.service

# 12c. coding agents: the root-side runner the API pokes to detect connected
# agents and run their setup. The API writes counters here, never into a home.
install -d -m 2770 -o root -g docker /opt/eggie/agent-status /opt/eggie/agent-status/setup
chmod 2770 /opt/eggie/agent-status /opt/eggie/agent-status/setup
install -m 644 "$INSTALL_DIR/systemd/eggie-agents.path" \
  "$INSTALL_DIR/systemd/eggie-agents.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eggie-agents.path
# Not fatal: the console only loses its "connected" line until the next poll.
systemctl start eggie-agents.service || echo "could not check this machine's coding agents" >&2

# 13. what the console's "Connect an agent" guide needs to know.
if grep -qi microsoft /proc/sys/kernel/osrelease; then vm=wsl
elif [[ -d /mnt/lima-cidata ]]; then vm=lima
else vm=other
fi
# sed, not head: head exiting early would SIGPIPE the pipeline under pipefail.
agent_user=$(getent passwd | bash "$INSTALL_DIR/lib/login-users.sh" /etc/shells | cut -d: -f1 | sed -n 1p)
printf '{"vm": "%s", "user": "%s"}\n' "$vm" "$agent_user" > /opt/eggie/connect.json
chmod 644 /opt/eggie/connect.json

# 14. marker, last: a failure above must leave no marker behind.
echo "$REF" > /opt/eggie/runtime.version
echo "Eggie runtime $REF installed"
