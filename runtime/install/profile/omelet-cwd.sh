# Sourced by /etc/profile, under dash as well as bash: POSIX sh only.
# Only interactive shells that started in $HOME move; agents, scp and
# VS Code's server run non-interactive and keep their own directory.
case $- in
  *i*)
    if [ "$PWD" = "$HOME" ] && [ -d "$HOME/projects" ]; then
      cd "$HOME/projects" || true
    fi
    ;;
esac
