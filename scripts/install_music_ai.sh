#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly install_root=/opt/music-ai
readonly config_dir=/etc/music-ai
readonly state_dir=/var/lib/music-ai
readonly model_source=/root/models/nemotron-bolt-q4.gguf
readonly model_dir=/var/lib/music-llm
readonly model_target=/var/lib/music-llm/model.gguf
readonly llama_server=/llama.cpp/build/bin/llama-server
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly project_root

die() {
  printf 'install_music_ai: %s\n' "$1" >&2
  exit 1
}
require_real_path() {
  local path=$1
  [[ ! -L "$path" ]] || die "Refusing symlink at sensitive path: $path"
}
require_directory() {
  local path=$1 mode=$2
  require_real_path "$path"
  if [[ -e "$path" ]]; then
    [[ -d "$path" ]] || die "Expected directory: $path"
  else
    install -d -o root -g root -m "$mode" -- "$path"
  fi
  chown root:root -- "$path"
  chmod "$mode" -- "$path"
}

[[ $EUID -eq 0 ]] || die 'Run as root inside CT111.'
for binary in /usr/bin/install /usr/bin/python3 /usr/bin/systemctl /usr/bin/id /usr/bin/getent /usr/bin/ln /usr/bin/stat /usr/bin/chown /usr/bin/chmod /usr/bin/mkdir /usr/bin/mktemp /usr/sbin/groupadd /usr/sbin/useradd; do
  [[ -x "$binary" ]] || die "Required system binary missing: $binary"
done
[[ -f "$project_root/app/__init__.py" && -f "$project_root/app/music_agent.py" && -f "$project_root/app/music_agent_core.py" ]] || die 'Run from a checkout containing the music agent modules.'
[[ -f "$project_root/requirements.lock" ]] || die 'The project requirements.lock is required.'
[[ -x "$llama_server" ]] || die "llama-server not found: $llama_server"
[[ -f "$model_source" && ! -L "$model_source" ]] || die "Expected readable model file: $model_source"
[[ -r "$model_source" && $(stat -c '%U:%a' -- "$model_source") == 'root:644' ]] || die 'Model must remain root-owned mode 0644.'

require_directory "$config_dir" 700
require_directory "$state_dir" 700
require_real_path /usr/local/bin/music-ai
require_real_path "$install_root"
if [[ -e "$install_root" ]]; then
  [[ -d "$install_root" ]] || die "Expected directory: $install_root"
  for entry in "$install_root"/* "$install_root"/.[!.]* "$install_root"/..?*; do
    [[ -e "$entry" ]] || continue
    case "$entry" in
    "$install_root/app" | "$install_root/.venv") ;;
    *) die "Refusing unrelated content in $install_root: ${entry##*/}" ;;
    esac
  done
  if [[ -e "$install_root/app" ]]; then
    [[ -d "$install_root/app" ]] || die "Expected directory: $install_root/app"
    for entry in "$install_root/app"/* "$install_root/app"/.[!.]* "$install_root/app"/..?*; do
      [[ -e "$entry" ]] || continue
      case "${entry##*/}" in
      __init__.py | music_agent.py | music_agent_core.py | music_agent_server.py | music_agent_batch.py | music_agent_catalog.py | music_agent_schema.py) ;;
      __pycache__)
        [[ -d "$entry" && ! -L "$entry" ]] || die 'Refusing unsafe app/__pycache__ path.'
        for cache_file in "$entry"/*; do
          [[ -e "$cache_file" ]] || continue
          [[ -f "$cache_file" && ! -L "$cache_file" && "${cache_file##*.}" == pyc ]] || die 'Refusing unrelated content in app/__pycache__.'
        done
        ;;
      *) die "Refusing unrelated content in $install_root/app: ${entry##*/}" ;;
      esac
    done
  fi
else
  install -d -o root -g root -m 755 -- "$install_root"
fi

if ! getent group music-llm >/dev/null; then
  /usr/sbin/groupadd --system music-llm
fi
if ! id music-llm >/dev/null 2>&1; then
  /usr/sbin/useradd --system --gid music-llm --home-dir /var/lib/music-llm --no-create-home --shell /usr/sbin/nologin music-llm
fi
llm_uid=$(id -u music-llm)
llm_group=$(id -gn music-llm)
[[ "$llm_uid" != 0 && "$llm_group" == music-llm ]] || die 'Existing music-llm account must be unprivileged with primary group music-llm.'
require_directory "$model_dir" 755
if [[ -e "$model_target" || -L "$model_target" ]]; then
  [[ -f "$model_target" && ! -L "$model_target" ]] || die 'Existing model target is not a regular file.'
  [[ "$model_source" -ef "$model_target" ]] || die 'Existing model target is not the same inode as source.'
else
  ln -- "$model_source" "$model_target"
fi
chown root:root -- "$model_target"
chmod 644 -- "$model_target"

require_real_path "$install_root/app"
install -d -o root -g root -m 755 -- "$install_root/app"
for module in __init__.py music_agent.py music_agent_core.py music_agent_server.py music_agent_batch.py music_agent_catalog.py music_agent_schema.py; do
  require_real_path "$install_root/app/$module"
  install -o root -g root -m 644 -- "$project_root/app/$module" "$install_root/app/$module"
done
require_real_path "$install_root/.venv"
if [[ ! -x "$install_root/.venv/bin/python" ]] || ! "$install_root/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
  /usr/bin/python3 -m venv "$install_root/.venv"
fi
"$install_root/.venv/bin/python" -m pip install --disable-pip-version-check \
  --constraint "$project_root/requirements.lock" 'httpx==0.28.1'

tmp_wrapper=$(mktemp /usr/local/bin/.music-ai.XXXXXX)
trap 'rm -f -- "$tmp_wrapper"' EXIT
cat >"$tmp_wrapper" <<'EOF'
#!/usr/bin/env sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
cd /opt/music-ai
exec /opt/music-ai/.venv/bin/python -m app.music_agent "$@"
EOF
install -o root -g root -m 755 -- "$tmp_wrapper" /usr/local/bin/music-ai
rm -f -- "$tmp_wrapper"
trap - EXIT

require_real_path /etc/systemd/system/music-ai-llm.service
install -o root -g root -m 644 -- "$project_root/deploy/music-ai-llm.service" /etc/systemd/system/music-ai-llm.service
require_real_path /etc/systemd/system/music-ai-web.service
install -o root -g root -m 644 -- "$project_root/deploy/music-ai-web.service" /etc/systemd/system/music-ai-web.service
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --now music-ai-llm.service
if [[ -f "$config_dir/web.env" && ! -L "$config_dir/web.env" ]]; then
  /usr/bin/systemctl enable --now music-ai-web.service
fi
printf 'Installed music-ai. Configure %s/config.json before using the CLI.\n' "$config_dir"
