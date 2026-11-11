#!/usr/bin/env bash
# UserPromptSubmit hook for the speak plugin. At the Claude Code prompt, type:
#
#   speak       hear Claude's last reply
#   speak all   hear the whole last turn, the "let me check…" narration included
#   shh         stop
#   stop        stop too, but only while speak is reading: otherwise the word goes to
#               Claude as usual
#
# or the commands /speak, /speak:all and /speak:shh (/shh too), which key bindings send.
# (On builds that load mods, hooks/speak.ts answers the commands before they get here.)
#
# Claude Code runs this on every prompt, so anything else leaves at the first check
# without starting a single process. The trigger words are blocked: they never reach
# Claude, so listening costs no tokens and doesn't wait on the model.

input=$(cat)

shopt -s nocasematch
ws='([[:space:]]|\\[nrt])*'
trigger="\"prompt\"[[:space:]]*:[[:space:]]*\"${ws}(/speak(:all|:shh)?|/shh|speak(${ws}all)?|shh|stop)${ws}\""
[[ $input =~ $trigger ]] || exit 0
word=${BASH_REMATCH[0]}

umask 077 # the cache folder and log are this user's alone
cache="${XDG_CACHE_HOME:-$HOME/.cache}/claude-speak"

# A speak process that leads its own process group: PIDs get reused, so check before signalling.
is_speak() {
  # shellcheck disable=SC2046 # split on purpose: "<pid> <pgid> <command…>"
  set -- "$1" $(ps -o pgid= -o command= -p "$1" 2>/dev/null)
  [ "$2" = "$1" ] && [[ "$*" == *speak.py* ]]
}

# The process reading right now, if any. speak.py writes its PID to $cache/reading while it
# reads (loading included), and this hook writes the PID of a speak it has just started.
reading_pid() {
  local pid
  pid=$(cat "$cache/reading" 2>/dev/null) || return 1
  case $pid in '' | *[!0-9]*) return 1 ;; esac
  is_speak "$pid" && printf '%s' "$pid"
}

# Stop the reading at once with SIGUSR1: speak.py stops playing and keeps the voice loaded;
# a speak still starting up ends. The voice server is told too, in case it was taking over.
interrupt() {
  local server
  kill -USR1 -- "-$1" 2>/dev/null
  server=$(cat "$cache/playing.pid" 2>/dev/null)
  if [ -n "$server" ] && [ "$server" != "$1" ] && is_speak "$server"; then
    kill -USR1 -- "-$server" 2>/dev/null
  fi
}
models="${CLAUDE_SPEAK_MODEL_DIR:-$cache}"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Swallow the prompt and show this line instead. Claude Code always heads it with
# "UserPromptSubmit operation blocked by hook:"; suppressOriginalPrompt drops the
# "Original prompt: speak" line it would otherwise add (older versions ignore it).
block() {
  local text=${1//\\/\\\\} # escaped for JSON: backslashes, quotes, and no control characters
  text=${text//\"/\\\"}
  text=${text//[$'\001'-$'\037']/ }
  printf '{"decision":"block","reason":"%s","hookSpecificOutput":{"hookEventName":"UserPromptSubmit","suppressOriginalPrompt":true}}\n' "$text"
  exit 0
}

stop_playing() {
  local pid
  pid=$(cat "$cache/playing.pid" 2>/dev/null) || return 1
  rm -f "$cache/playing.pid"
  case $pid in '' | *[!0-9]*) return 1 ;; esac
  is_speak "$pid" || return 1
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

case $OSTYPE in msys* | cygwin* | win32*)
  # speak.py needs macOS or Linux (Unix sockets, process groups, signals). Say so, rather than
  # answer "Reading" and fail out of sight. stop is left for Claude.
  [[ $word == *stop* ]] && exit 0
  block "speak works on macOS and Linux; Windows isn't supported yet."
  ;;
esac

if [[ $word == *stop* ]] || [[ $word == *shh* ]]; then
  if pid=$(reading_pid); then
    interrupt "$pid"
    block "🔇 Stopped."
  fi
  [[ $word == *stop* ]] && exit 0 # nothing reading: an ordinary prompt, for Claude
fi

uv=$(find_uv)
mkdir -p "$cache" && chmod 700 "$cache" 2>/dev/null
log="$cache/last.log"
[ -f "$log" ] && [ "$(wc -c <"$log")" -gt 1000000 ] && : >"$log" # keep the log small

if [[ $word == *shh* ]]; then
  # Nothing marked as reading. An older speak, or one started by a hotkey, may still be:
  # ask the loaded voice to stop (it stays loaded for the next speak). Without uv, end it.
  # --offline: if speak's packages were never downloaded, fail at once rather than fetch
  # them, which could outlast this hook's timeout.
  if [ -n "$uv" ] && reply=$("$uv" run --offline --quiet --script "$here/speak.py" --stop 2>>"$log"); then
    block "🔇 ${reply:-Stopped.}"
  fi
  if stop_playing; then block "🔇 Stopped."; fi
  block "🔇 Nothing was playing."
fi

[ -n "$uv" ] || block "speak needs uv, which is free: curl -LsSf https://astral.sh/uv/install.sh | sh, then type speak again."

args=()
path_re='"transcript_path"[[:space:]]*:[[:space:]]*"([^"]*)"'
id_re='"session_id"[[:space:]]*:[[:space:]]*"([A-Za-z0-9_-]+)"'
# Both when given: speak.py reads the path, or finds the session's transcript by its id
# (all the mod knows), and never another session's.
[[ $input =~ $path_re ]] && [ -n "${BASH_REMATCH[1]}" ] && args+=(--transcript "${BASH_REMATCH[1]}")
[[ $input =~ $id_re ]] && args+=(--session "${BASH_REMATCH[1]}")
[[ $word == *all* ]] && args+=(--all)

# Detached, in its own process group, with every stream redirected, so Claude Code
# doesn't wait for it and nothing that ends this hook can end the reading. If the
# voice is already loaded this just hands it the text; otherwise it loads it.
set -m
nohup "$uv" run --quiet --script "$here/speak.py" "${args[@]}" </dev/null >>"$log" 2>&1 &
printf '%s\n' "$!" >"$cache/reading" # so stop works while it starts up; speak.py takes over

quality=${CLAUDE_SPEAK_QUALITY:-}
if [ "$quality" != fast ] && [ "$quality" != high ]; then
  # As speak.py picks: the full model is the faster one on Intel and AMD, int8 elsewhere.
  case $(uname -m) in x86_64 | amd64 | i?86) quality=high ;; *) quality=fast ;; esac
fi
model="kokoro-v1.0.int8.onnx" size="140 MB"
[ "$quality" = high ] && model="kokoro-v1.0.onnx" size="350 MB"
if [ -s "$models/$model" ] && [ -s "$models/voices-v1.0.bin" ]; then
  block "🔊 Reading. Type shh to stop."
fi
block "🔊 First time: downloading the voice (about $size, once per computer). It starts reading when that's done. Type shh to stop."
