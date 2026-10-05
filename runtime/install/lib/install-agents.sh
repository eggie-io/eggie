#!/usr/bin/env bash
# Writes Eggie's instructions block into every per-account file the agent
# manifests name, and the ~/projects link, into one home directory. Skills are
# installed separately, with npx; system-wide instructions by install.sh.
# Run by install.sh as root, once per home:
#   install-agents.sh <runtime-dir> <home> <owner uid:gid>
set -euo pipefail

SRC=$1
HOME_DIR=$2
OWNER=$3
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BEGIN='<!-- eggie:begin -->'
END='<!-- eggie:end -->'
TARGET=/opt/eggie/projects

# Captured first: a failure inside a process substitution would go unnoticed.
targets="$(python3 "$LIB/agents.py" --agents-dir "$SRC/agents" instructions --home "$HOME_DIR")"

# The user may keep their own text in these files: only the block between the
# markers is ours to replace.
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  # Only directories created here are chowned: mkdir -p as root would leave
  # missing parents (e.g. ~/.config) root-owned, and existing ones stay as they are.
  created=()
  dir="$(dirname "$file")"
  while [[ "$dir" != "$HOME_DIR" && "$dir" == "$HOME_DIR"/* && ! -e "$dir" ]]; do
    created+=("$dir")
    dir="$(dirname "$dir")"
  done
  mkdir -p "$(dirname "$file")"
  for dir in ${created[@]+"${created[@]}"}; do chown "$OWNER" "$dir"; done
  touch "$file"
  sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$file"
  if [[ -s "$file" && -n "$(tail -c1 "$file")" ]]; then
    echo >> "$file"
  fi
  { echo "$BEGIN"; cat "$SRC/instructions/eggie.md"; echo "$END"; } >> "$file"
  chown "$OWNER" "$file"
done <<< "$targets"

if [[ ! -e "$HOME_DIR/projects" && ! -L "$HOME_DIR/projects" ]]; then
  ln -s "$TARGET" "$HOME_DIR/projects"
elif [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  :
elif [[ -e "$HOME_DIR/projects" || -L "$HOME_DIR/projects" ]]; then
  echo "left $HOME_DIR/projects alone: it already exists and is not Eggie's link"
fi

if [[ -L "$HOME_DIR/projects" && "$(readlink "$HOME_DIR/projects")" == "$TARGET" ]]; then
  chown -h "$OWNER" "$HOME_DIR/projects"
fi
