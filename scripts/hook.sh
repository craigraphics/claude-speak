#!/usr/bin/env bash
# UserPromptSubmit hook for the speak plugin. At the Claude Code prompt, type:
#
#   speak       hear Claude's last reply
#   speak all   hear the whole last turn, the "let me check…" narration included
#   shh         stop
#
# or the plugin's commands /speak, /speak-all and /shh (also /speak:speak and so on,
# which is what a key binding sends).
#
# Claude Code runs this on every prompt, so anything else leaves at the first check
# without starting a single process. The trigger words are blocked: they never reach
# Claude, so listening costs no tokens and doesn't wait on the model.

input=$(cat)

shopt -s nocasematch
ws='([[:space:]]|\\[nrt])*'
trigger="\"prompt\"[[:space:]]*:[[:space:]]*\"${ws}(/(speak:)?)?(speak${ws}all|speak-all|speak|shh)${ws}\""
[[ $input =~ $trigger ]] || exit 0
word=${BASH_REMATCH[0]}

cache="${XDG_CACHE_HOME:-$HOME/.cache}/claude-speak"
models="${CLAUDE_SPEAK_MODEL_DIR:-$cache}"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Swallow the prompt and show this line instead. Claude Code always heads it with
# "UserPromptSubmit operation blocked by hook:"; suppressOriginalPrompt drops the
# "Original prompt: speak" line it would otherwise add (older versions ignore it).
block() {
  printf '{"decision":"block","reason":"%s","hookSpecificOutput":{"hookEventName":"UserPromptSubmit","suppressOriginalPrompt":true}}\n' "$1"
  exit 0
}

stop_playing() {
  local pid
  pid=$(cat "$cache/playing.pid" 2>/dev/null) || return 1
  rm -f "$cache/playing.pid"
  case $pid in '' | *[!0-9]*) return 1 ;; esac
  # Only if that PID is still a reading that leads its own process group: PIDs get reused.
  # shellcheck disable=SC2046 # split on purpose: "<pgid> <command…>"
  set -- $(ps -o pgid= -o command= -p "$pid" 2>/dev/null)
  [ "$1" = "$pid" ] && [[ "$*" == *speak.py* ]] || return 1
  kill -TERM -- "-$pid" 2>/dev/null # the group, so a fallback `say` stops too
}

find_uv() {
  local c
  for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
    /opt/homebrew/bin/uv /usr/local/bin/uv /home/linuxbrew/.linuxbrew/bin/uv; do
    if [ -n "$c" ] && [ -x "$c" ]; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

uv=$(find_uv)
mkdir -p "$cache"
log="$cache/last.log"
[ -f "$log" ] && [ "$(wc -c <"$log")" -gt 1000000 ] && : >"$log" # keep the log small

if [[ $word == *shh* ]]; then
  # Ask the loaded voice to stop (it stays loaded for the next speak). Without uv, end it.
  if [ -n "$uv" ] && reply=$("$uv" run --quiet --script "$here/speak.py" --stop 2>>"$log"); then
    block "🔇 ${reply:-Stopped.}"
  fi
  if stop_playing; then block "🔇 Stopped."; fi
  block "🔇 Nothing was playing."
fi

[ -n "$uv" ] || block "speak needs uv, which is free: curl -LsSf https://astral.sh/uv/install.sh | sh, then type speak again."

args=()
path_re='"transcript_path"[[:space:]]*:[[:space:]]*"([^"]*)"'
id_re='"session_id"[[:space:]]*:[[:space:]]*"([A-Za-z0-9_-]+)"'
if [[ $input =~ $path_re ]] && [ -n "${BASH_REMATCH[1]}" ]; then
  args+=(--transcript "${BASH_REMATCH[1]}")
elif [[ $input =~ $id_re ]]; then
  args+=(--session "${BASH_REMATCH[1]}") # from the mod, which knows the session but not the path
fi
[[ $word == *all* ]] && args+=(--all)

# Detached, in its own process group, with every stream redirected, so Claude Code
# doesn't wait for it and nothing that ends this hook can end the reading. If the
# voice is already loaded this just hands it the text; otherwise it loads it.
set -m
nohup "$uv" run --quiet --script "$here/speak.py" "${args[@]}" </dev/null >>"$log" 2>&1 &

model="kokoro-v1.0.int8.onnx" size="140 MB"
[ "${CLAUDE_SPEAK_QUALITY:-}" = high ] && model="kokoro-v1.0.onnx" size="350 MB"
if [ -s "$models/$model" ] && [ -s "$models/voices-v1.0.bin" ]; then
  block "🔊 Reading. Type shh to stop."
fi
block "🔊 First time: downloading the voice (about $size, once per computer). It starts reading when that's done. Type shh to stop."
