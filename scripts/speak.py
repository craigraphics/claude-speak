#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#     "kokoro-onnx==0.6.1",
#     "sounddevice==0.5.6",
#     # 1.31 runs the fast (int8) model; Intel Macs stop at 1.23, which falls back to the full model
#     "onnxruntime>=1.31; sys_platform != 'darwin' or platform_machine != 'x86_64'",
#     "onnxruntime<1.24; sys_platform == 'darwin' and platform_machine == 'x86_64'",
# ]
# ///
"""Read Claude Code's last reply out loud with a natural voice that runs on your computer.

    speak.py                      the last reply of the most recent Claude Code session
    speak.py --transcript FILE    the last reply in that session transcript
    speak.py --text "Hello"       any text
    speak.py --dry-run            print what would be read instead of reading it
    speak.py --stop               stop whatever is playing
    speak.py --setup              download the voice now (about 140 MB, once per computer)
    speak.py --voices             list the voices

The voice is Kokoro (https://github.com/thewh1teagle/kokoro-onnx). It is free and works
offline once downloaded. If it cannot run, the computer's own voice reads instead.

The first reading loads the voice and keeps it loaded for a while (10 minutes, or
CLAUDE_SPEAK_IDLE seconds), listening on a socket in the cache folder. Later readings
hand it the text and start almost at once.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Callable, Iterable, Iterator


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


APP = "claude-speak"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP
MODEL_DIR = Path(os.environ.get("CLAUDE_SPEAK_MODEL_DIR") or CACHE).expanduser()
PID_FILE = CACHE / "playing.pid"


def _socket_path() -> Path:
    path = CACHE / "speak.sock"
    if len(str(path)) < 100:  # the limit for a Unix socket path is about 104 bytes
        return path
    return Path(tempfile.gettempdir()) / f"{APP}-{os.getuid()}.sock"


SOCKET = _socket_path()

# Pinned model files: (name, size, SHA-256), as GitHub publishes them for this release.
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
MODELS = {
    # 2-3x faster and a third of the download; the default, so it keeps up on battery too
    "fast": ("kokoro-v1.0.int8.onnx", 114119327, "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c"),
    # full precision, for anyone who hears the difference
    "high": ("kokoro-v1.0.onnx", 325505369, "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
}
VOICES = ("voices-v1.0.bin", 28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")
DEFAULT_QUALITY = "fast"

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.1
IDLE_SECONDS = max(0.0, _env_float("CLAUDE_SPEAK_IDLE", 600))
DEBUG = bool(os.environ.get("CLAUDE_SPEAK_DEBUG"))
TRIGGERS = {"speak", "speak all", "shh"}

# The first letter of a Kokoro voice name is its language.
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br"}

CODE_SKIPPED = "Code block skipped."
NOTHING_TO_READ = "There's no reply to read yet."

_T0 = time.monotonic()


def log(message: str) -> None:
    print(f"[{time.monotonic() - _T0:6.2f}s] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------
# Finding the reply
# --------------------------------------------------------------------------------------


def config_dirs() -> list[Path]:
    """Claude Code config folders on this computer: $CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude-*."""
    dirs: list[Path] = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        dirs.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser())
    dirs += sorted(Path.home().glob(".claude*"))
    return [d for d in dict.fromkeys(dirs) if (d / "projects").is_dir()]


def latest_transcript() -> Path | None:
    """The session transcript written to most recently, across every account on this computer."""
    newest: tuple[float, Path] | None = None
    for d in config_dirs():
        for f in (d / "projects").glob("*/*.jsonl"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, f)
    return newest[1] if newest else None


def session_transcript(session_id: str) -> Path | None:
    """The transcript of one session, found by its id (the file's name) in any config folder."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""):
        return None
    for d in config_dirs():
        for f in (d / "projects").glob(f"*/{session_id}.jsonl"):
            return f
    return None


def entries_newest_first(path: Path) -> Iterator[dict]:
    """Transcript entries from the end backwards. Lines that don't parse (one still being written) are skipped."""
    for raw in reversed(path.read_bytes().splitlines()):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield entry


def _blocks(entry: dict) -> list[dict]:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def is_prompt(entry: dict) -> bool:
    """Something the person sent (a message or a command), which starts a new turn."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    blocks = _blocks(entry)
    if not blocks or any(b.get("type") == "tool_result" for b in blocks):
        return False
    text = " ".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
    return " ".join(text.split()).lower() not in TRIGGERS


def reply_texts(entry: dict) -> list[str]:
    """The visible text Claude wrote in this entry (no thinking, no tool calls)."""
    if entry.get("type") != "assistant" or entry.get("isApiErrorMessage"):
        return []
    if (entry.get("message") or {}).get("model") == "<synthetic>":
        return []
    return [
        b["text"]
        for b in _blocks(entry)
        if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip()
    ]


def is_tool_step(entry: dict) -> bool:
    """A tool call, or its result: the line between "Let me check…" and the final answer."""
    kinds = {b.get("type") for b in _blocks(entry)}
    return (entry.get("type") == "assistant" and "tool_use" in kinds) or (
        entry.get("type") == "user" and "tool_result" in kinds
    )


def last_reply(entries_backwards: Iterable[dict], whole_turn: bool = False) -> str:
    """The text of Claude's latest reply, in order.

    By default that is the last stretch of text Claude wrote: everything after its
    final tool call, which is the answer or summary you'd read. With `whole_turn`
    it is every piece of text since your last message, narration included.
    Subagent entries are skipped, and so are the trigger words themselves.
    """
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries_backwards:
        if entry.get("isSidechain"):
            continue
        if is_prompt(entry):
            if parts:
                break
            continue  # this turn has no text yet: read the turn before it
        texts = reply_texts(entry)
        if not texts:
            if parts and not whole_turn and is_tool_step(entry):
                break
            continue
        msg_id = str((entry.get("message") or {}).get("id") or entry.get("uuid") or "")
        for text in reversed(texts):
            if (msg_id, text) not in seen:
                seen.add((msg_id, text))
                parts.append(text)
    return "\n\n".join(reversed(parts))


# --------------------------------------------------------------------------------------
# Turning markdown into something that sounds natural
# --------------------------------------------------------------------------------------

_FENCE = re.compile(r"^(`{3,}|~{3,})")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_HR = re.compile(r"^([-*_]\s*){3,}$")
_URL = re.compile(r"\bhttps?://(?:www\.)?([^/\s)>\]]+)[^\s)>\]]*")
_PATH = re.compile(r"(?<![\w/.~@:-])((?:~|\.\.?)?/?(?:[\w@+.-]+/)+[\w@+.-]*)(:\d+(?::\d+)?)?")
_HTML = re.compile(r"</?(?:br|p|div|span|details|summary|sup|sub|kbd|b|i|em|strong|code|pre|u|s)\b[^>]*>", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")

_SYMBOLS = [
    (re.compile(r"\s*(?:→|⇒|⟶|->|=>)\s*"), " to "),
    (re.compile(r"\s*(?:←|<-)\s*"), " from "),
    (re.compile(r"\s*↔\s*"), " and "),
    (re.compile(r"≤\s*"), "at most "),
    (re.compile(r"≥\s*"), "at least "),
    (re.compile(r"[≈~](?=\s?\d)"), "about "),
    (re.compile(r"(?<=\d)\s?×\s?(?=\d)"), " times "),
    (re.compile(r"\s&\s"), " and "),
    (re.compile(r"\be\.g\.,?", re.I), "for example,"),
    (re.compile(r"\bi\.e\.,?", re.I), "that is,"),
    (re.compile(r"\bvs\.?(?=\s)"), "versus"),
    (re.compile(r"\bw/(?=\s)"), "with"),
    (re.compile(r"(?<=\d)\s?–\s?(?=\d)"), " to "),
    (re.compile(r"\s*[—–]\s*"), ", "),
    (re.compile(r"…"), "..."),
    (re.compile(r"#(?=\d)"), "number "),
    (re.compile(r"(?<=\d)\s?ms\b"), " milliseconds"),
    (re.compile(r"(?<=\d\.\d)\s?s\b|(?<=\d\.\d\d)\s?s\b"), " seconds"),  # 1.5s, not the 1990s
    (re.compile(r"(?<=\d)\s?KB\b"), " kilobytes"),
    (re.compile(r"(?<=\d)\s?MB\b"), " megabytes"),
    (re.compile(r"(?<=\d)\s?GB\b"), " gigabytes"),
]


def _path(m: re.Match) -> str:
    """Read `src/app/main.ts:42` as "main.ts", and `and/or` as "and or"."""
    path = m.group(1)
    trail = ""
    while path and path[-1] in ".,-":
        trail, path = path[-1] + trail, path[:-1]
    segments = [s for s in path.split("/") if s]
    if not segments:
        return m.group(0)
    is_path = (
        path.startswith(("/", "~", "."))
        or path.endswith("/")
        or len(segments) >= 3
        or "." in segments[-1]
    )
    if is_path:
        return segments[-1] + trail
    if all(s.isalpha() for s in segments):
        joiner = " " if "or" in (s.lower() for s in segments) else " or "  # "and/or", not "and or or"
        return joiner.join(segments) + trail
    return m.group(0)


def _emphasis(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", s)
    s = re.sub(r"\*(?=\S)([^*\n]+?)(?<=\S)\*", r"\1", s)
    s = re.sub(r"(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    return s


def speak_line(line: str, heading: bool = False) -> str:
    s = line.strip()
    if not s or _HR.match(s):
        return ""
    s = _HTML.sub(" ", s)
    s = re.sub(r"^(>\s?)+", "", s)                       # quote
    s = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", s)          # list item
    s = re.sub(r"^\[[ xX]\]\s+", "", s)                  # checkbox
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)      # image
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)       # link: keep the words, drop the address
    s = s.replace("`", "")
    s = _URL.sub(lambda m: m.group(1), s)                # bare address: just the site
    s = _PATH.sub(_path, s)
    s = _emphasis(s)
    for pattern, spoken in _SYMBOLS:
        s = pattern.sub(spoken, s)
    s = _EMOJI.sub("", s)
    s = re.sub(r"(?<=[A-Za-z])_(?=[A-Za-z])", " ", s)  # snake_case and ENV_NAMES
    s = re.sub(r"[*_\\|{}\[\]<>]", " ", s)
    s = re.sub(r"\s*\(\s*", ", ", s)
    s = re.sub(r"\s*\)\s*", ", ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"[,;](?=\s*[,.;:!?])", "", s)
    s = re.sub(r",(\s*,)+", ",", s)
    s = s.strip(" ,;")
    if not s:
        return ""
    if s.endswith(":") or heading:
        s = s.rstrip(":.") + "."
    elif s[-1] not in ".!?":
        s += "."
    return s


def to_speech(markdown: str) -> str:
    """Markdown in, plain sentences out (one paragraph per line)."""
    out: list[str] = []
    fence = ""
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        m = _FENCE.match(stripped)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not stripped[len(m.group(1)):].strip():
                fence = ""
            continue
        if m:
            fence = m.group(1)
            if not out or out[-1] != CODE_SKIPPED:
                out.append(CODE_SKIPPED)
            continue
        if _TABLE_RULE.match(stripped) and "-" in stripped:
            continue
        heading = False
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            stripped = ", ".join(c.strip() for c in stripped.strip("|").split("|") if c.strip())
        elif re.match(r"^#{1,6}\s", stripped):
            stripped = re.sub(r"^#{1,6}\s+", "", stripped)
            heading = True
        line = speak_line(stripped, heading)
        if line:
            out.append(line)
    return "\n".join(out)


def _pause(piece: str) -> float:
    return 0.18 if piece[-1:] in (".", "!", "?") else 0.05


def pieces(text: str) -> list[tuple[str, float]]:
    """Sentence-sized pieces, each with the pause that follows it.

    The voice can't start until the first piece is ready, so it is kept short: one
    sentence, and a long first sentence is cut at its first comma.
    """
    result: list[tuple[str, float]] = []
    for paragraph in text.split("\n"):
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if s]
        if not result and sentences and len(sentences[0]) > 45:
            m = re.match(r"(.{12,60}?[,;:])\s+(?=\S)", sentences[0])
            if m:
                sentences[0:1] = [m.group(1), sentences[0][m.end():]]
        buf = ""
        for sentence in sentences:
            if buf and (not result or len(buf) >= 40 or len(buf) + len(sentence) > 300):
                result.append((buf, _pause(buf)))
                buf = sentence
            else:
                buf = f"{buf} {sentence}".strip()
        if buf:
            result.append((buf, _pause(buf)))
        if result:
            result[-1] = (result[-1][0], 0.45)  # a longer pause between paragraphs
    return result


# --------------------------------------------------------------------------------------
# The voice
# --------------------------------------------------------------------------------------

Alive = Callable[[], bool]


def _always() -> bool:
    return True


def _file_ok(path: Path, size: int) -> bool:
    try:
        return path.stat().st_size == size
    except OSError:
        return False


def model_files(quality: str) -> list[tuple[str, int, str]]:
    return [MODELS.get(quality, MODELS[DEFAULT_QUALITY]), VOICES]


def models_ready(quality: str = DEFAULT_QUALITY, model_dir: Path = MODEL_DIR) -> bool:
    return all(_file_ok(model_dir / name, size) for name, size, _ in model_files(quality))


def _fetch(url: str, part: Path) -> None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": APP})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
    except Exception as e:  # some Python builds lack certificates: curl has its own
        if not shutil.which("curl"):
            raise
        log(f"Python download failed ({e}); retrying with curl")
        subprocess.run(["curl", "-fsSL", "--retry", "2", "-o", str(part), url], check=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def ensure_models(quality: str = DEFAULT_QUALITY, model_dir: Path = MODEL_DIR) -> None:
    """Download the voice once. A lock stops two runs downloading at the same time."""
    if models_ready(quality, model_dir):
        return
    import fcntl

    model_dir.mkdir(parents=True, exist_ok=True)
    with open(model_dir / ".download.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, size, sha in model_files(quality):
            dest = model_dir / name
            if _file_ok(dest, size):
                continue
            part = dest.with_name(dest.name + ".part")
            for attempt in range(3):
                log(f"Downloading {name} ({size / 1e6:.0f} MB)…")
                try:
                    _fetch(MODEL_URL + name, part)
                    if part.stat().st_size != size or _sha256(part) != sha:
                        raise OSError(f"{name} did not match its checksum")
                    os.replace(part, dest)
                    break
                except Exception as e:
                    part.unlink(missing_ok=True)
                    if attempt == 2:
                        raise
                    log(f"Download failed ({e}); trying again")
                    time.sleep(2 * (attempt + 1))


def _threads() -> int:
    """Threads for the voice model. On Apple Silicon only the performance cores: by default
    onnxruntime also uses the efficiency cores, which makes it about twice as slow."""
    env = os.environ.get("CLAUDE_SPEAK_THREADS", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    if sys.platform == "darwin":
        sysctl = "/usr/sbin/sysctl" if os.path.exists("/usr/sbin/sysctl") else "sysctl"  # hooks may get a bare PATH
        try:
            out = subprocess.run([sysctl, "-n", "hw.perflevel0.physicalcpu"], capture_output=True, text=True).stdout
            return max(0, int(out.strip()))
        except (OSError, ValueError):
            pass
    return 0  # let onnxruntime decide


# Every synthesis runs on this one thread. The phonemizer sets espeak up once per
# thread (about a second), so a fresh thread per reading would pay that every time.
SYNTH = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="synth")


def _warm_phonemizer(lang: str) -> None:
    try:
        from kokoro_onnx.tokenizer import Tokenizer

        Tokenizer().phonemize("Hello.", lang)
    except Exception as e:  # only a head start: the first real piece does the same work
        log(f"Phonemizer warm-up skipped ({e})")


def _open_model(quality: str):
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    options = rt.SessionOptions()
    threads = _threads()
    if threads:
        options.intra_op_num_threads = threads
    model = model_files(quality)[0][0]
    session = rt.InferenceSession(str(MODEL_DIR / model), sess_options=options, providers=["CPUExecutionProvider"])
    engine = Kokoro.from_session(session, str(MODEL_DIR / VOICES[0]))
    log(f"Voice loaded ({model}, {threads or 'default'} threads)")
    return engine


def load_kokoro(quality: str = DEFAULT_QUALITY):
    ensure_models(quality)
    import kokoro_onnx  # noqa: F401  (imported here so the warm-up below doesn't race the import)

    # Set espeak up on the synthesis thread while this one loads the model.
    voice = os.environ.get("CLAUDE_SPEAK_VOICE") or DEFAULT_VOICE
    SYNTH.submit(_warm_phonemizer, LANGS.get(voice[:1], "en-us"))
    try:
        return _open_model(quality)
    except Exception as e:
        if quality == "high":
            raise
        # Some onnxruntime builds (Intel Macs) can't run the int8 model: use the full one.
        log(f"The fast model can't run here ({type(e).__name__}); using the full one")
        ensure_models("high")
        return _open_model("high")


def _run(cmd: list[str], alive: Alive, stdin_text: str | None = None) -> bool:
    """Run a command to the end, or until the reading is cancelled. True if it finished."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL)
    if stdin_text is not None:

        def feed() -> None:
            try:
                proc.stdin.write(stdin_text.encode())
                proc.stdin.close()
            except OSError:
                pass

        threading.Thread(target=feed, daemon=True).start()
    while True:
        try:
            proc.wait(timeout=0.1)
            return True
        except subprocess.TimeoutExpired:
            if not alive():
                proc.terminate()
                proc.wait()
                return False


class Player:
    """Plays audio as it arrives: through PortAudio (gapless) when it can, a command-line player when it can't."""

    def __init__(self, alive: Alive) -> None:
        self.alive = alive
        self.stream = None
        self.mode = ""

    def play(self, audio, sample_rate: int, pause: float) -> bool:
        import numpy as np

        audio = np.concatenate([audio.astype(np.float32), np.zeros(int(sample_rate * pause), np.float32)])
        if not self.mode:
            try:
                import sounddevice as sd

                self.stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")
                self.stream.start()
                self.mode = "stream"
            except Exception as e:
                log(f"PortAudio unavailable ({e}); using a command-line player")
                self.mode = "cli"
        if self.mode == "cli":
            return _play_wav(audio, sample_rate, self.alive)
        step = sample_rate // 10  # small writes, so a stop takes effect within a tenth of a second
        for start in range(0, len(audio), step):
            if not self.alive():
                return False
            self.stream.write(audio[start : start + step].reshape(-1, 1))
        return True

    def close(self) -> None:
        if self.stream is None:
            return
        if self.alive():
            self.stream.stop()  # returns once everything queued has been heard
        else:
            self.stream.abort()  # cut off now
        self.stream.close()
        self.stream = None


def _play_wav(audio, sample_rate: int, alive: Alive) -> bool:
    import numpy as np

    if sys.platform == "darwin":
        players = [["afplay"]]
    else:
        players = [["paplay"], ["pw-play"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]
    player = next((p for p in players if shutil.which(p[0])), None)
    if player is None:
        raise RuntimeError("no audio player found (install pulseaudio-utils, pipewire or alsa-utils)")
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f"{APP}-", suffix=".wav", dir=CACHE, delete=False) as f:
        path = Path(f.name)
    try:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        return _run([*player, str(path)], alive)
    finally:
        path.unlink(missing_ok=True)


def system_voice(text: str, alive: Alive = _always) -> None:
    """The computer's own voice: less natural, but always there."""
    if not text.strip():
        return
    if sys.platform == "darwin":
        _run(["say", "-f", "-"], alive, text)
        return
    for cmd in (["espeak-ng", "--stdin"], ["espeak", "--stdin"]):
        if shutil.which(cmd[0]):
            _run(cmd, alive, text)
            return
    if shutil.which("spd-say"):
        _run(["spd-say", "-w", text], alive)
        return
    log("No voice available: install espeak-ng, or let the Kokoro voice download finish.")


def read_aloud(text: str, voice: str, speed: float, get_engine: Callable, alive: Alive = _always) -> None:
    """Read text with Kokoro, synthesizing one piece ahead of playback. Falls back to the system voice."""
    parts = pieces(text)
    if not parts or not alive():
        return
    if DEBUG:
        log(f"Reading {len(parts)} pieces")
    if os.environ.get("CLAUDE_SPEAK_ENGINE") == "system":
        system_voice(text, alive)
        return
    try:
        engine = get_engine()
        voices = engine.get_voices()
        if voice not in voices:
            log(f"Unknown voice {voice!r}; using {DEFAULT_VOICE}. Voices: {', '.join(voices)}")
            voice = DEFAULT_VOICE
    except Exception as e:
        log(f"Kokoro voice unavailable ({type(e).__name__}: {e}); using the system voice")
        system_voice(text, alive)
        return
    if not alive():
        return

    lang = LANGS.get(voice[:1], "en-us")
    ready: queue.Queue = queue.Queue()

    def synthesize() -> None:
        try:
            for i, (piece, pause) in enumerate(parts):
                if not alive():
                    break
                started = time.monotonic()
                audio, rate = engine.create(piece, voice=voice, speed=speed, lang=lang)
                if DEBUG:
                    log(f"piece {i}: {time.monotonic() - started:.2f}s for {len(audio) / rate:.2f}s of audio ({piece[:30]!r})")
                ready.put((i, audio, rate, pause))
        except Exception as e:
            ready.put(e)
        finally:
            ready.put(None)

    SYNTH.submit(synthesize)  # queued behind the end of any reading this one replaced
    player = Player(alive)
    heard = 0
    try:
        while alive():
            try:
                item = ready.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            i, audio, rate, pause = item
            if i == 0:
                log("Speaking")
            if not player.play(audio, rate, pause):
                break
            heard = i + 1
        player.close()
        log("Done" if alive() else "Stopped")
    except Exception as e:
        log(f"Kokoro voice failed ({type(e).__name__}: {e}); the system voice reads the rest")
        try:
            player.close()
        except Exception:
            pass
        system_voice(" ".join(p for p, _ in parts[heard:]), alive)


# --------------------------------------------------------------------------------------
# The voice server: keeps the voice loaded for a while, so the next `speak` starts at once
# --------------------------------------------------------------------------------------


class Speaker:
    """Owns the loaded voice and reads one thing at a time. A new reading replaces the current one."""

    def __init__(self) -> None:
        self._engine = None
        self._quality = ""
        self._engine_lock = threading.Lock()
        self._play_lock = threading.Lock()
        self._lock = threading.Lock()
        self._generation = 0
        self._readers = 0
        self.last_used = time.monotonic()

    @property
    def busy(self) -> bool:
        return self._readers > 0

    def engine(self, quality: str):
        with self._engine_lock:
            if self._engine is None or self._quality != quality:
                self._engine = None  # let the old model go before loading another
                self._engine = load_kokoro(quality)
                self._quality = quality
            return self._engine

    def stop(self) -> bool:
        with self._lock:
            self._generation += 1
            return self._readers > 0

    def start(self, text: str, voice: str, speed: float, quality: str) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._readers += 1
        threading.Thread(target=self._read, args=(generation, text, voice, speed, quality), daemon=True).start()

    def _read(self, generation: int, text: str, voice: str, speed: float, quality: str) -> None:
        def alive() -> bool:
            return self._generation == generation

        try:
            with self._play_lock:  # the reading this one replaces stops within a tenth of a second
                if alive():
                    read_aloud(text, voice, speed, lambda: self.engine(quality), alive)
        except Exception as e:
            log(f"Reading failed ({type(e).__name__}: {e})")
        finally:
            with self._lock:
                self._readers -= 1
                self.last_used = time.monotonic()


def handle(speaker: Speaker, request: dict) -> dict:
    command = request.get("cmd")
    if command == "speak":
        try:
            speed = float(request.get("speed") or DEFAULT_SPEED)
        except (TypeError, ValueError):
            speed = DEFAULT_SPEED
        quality = request.get("quality") if request.get("quality") in MODELS else DEFAULT_QUALITY
        speaker.start(str(request.get("text") or ""), str(request.get("voice") or DEFAULT_VOICE), speed, quality)
        return {"ok": True}
    if command == "stop":
        return {"ok": True, "stopped": speaker.stop()}
    if command == "ping":
        return {"ok": True}
    return {"ok": False, "error": f"unknown command {command!r}"}


def send(request: dict) -> dict | None:
    """Hand a request to the running voice server. None if there isn't one."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(str(SOCKET))
            s.sendall(json.dumps(request).encode() + b"\n")
            reply = json.loads(s.makefile("rb").readline() or b"null")
            return reply if isinstance(reply, dict) else None
    except (OSError, ValueError):
        return None


def serve(first: dict) -> bool:
    """Become the voice server, starting with `first`. False if another process already is one."""
    import fcntl

    CACHE.mkdir(parents=True, exist_ok=True)
    lock = open(CACHE / "server.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return False
    try:
        claim_speaker()
        SOCKET.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        speaker = Speaker()
        handle(speaker, first)
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                if not speaker.busy and time.monotonic() - speaker.last_used >= IDLE_SECONDS:
                    break
                continue
            with conn:
                conn.settimeout(2)
                try:
                    request = json.loads(conn.makefile("rb").readline() or b"{}")
                    reply = handle(speaker, request if isinstance(request, dict) else {})
                except Exception as e:  # a bad request must never take the voice down
                    reply = {"ok": False, "error": str(e)}
                try:
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except OSError:
                    pass
        server.close()
        SOCKET.unlink(missing_ok=True)
        log(f"No reading for {IDLE_SECONDS:.0f}s; unloading the voice")
    finally:
        release_speaker()
        lock.close()
    return True


# --------------------------------------------------------------------------------------
# One voice at a time
# --------------------------------------------------------------------------------------


def _playing_pid() -> int | None:
    """The PID of the voice process, if the PID file still points at one (PIDs get reused)."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        out = subprocess.run(["ps", "-o", "pgid=,command=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return None
    fields = out.strip().split(None, 1)
    if len(fields) == 2 and fields[0] == str(pid) and "speak.py" in fields[1]:
        return pid
    return None


def stop_playing() -> bool:
    """Last resort when the server doesn't answer: end the voice process and everything it started."""
    pid = _playing_pid()
    if pid is None or pid == os.getpid():
        return False
    PID_FILE.unlink(missing_ok=True)
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def claim_speaker() -> None:
    """Take over as the voice: end any stray earlier one, then record this one."""
    try:
        os.setsid()  # own process group, so ending it also ends a `say` or `afplay` it started
    except OSError:
        pass
    stop_playing()
    CACHE.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def release_speaker() -> None:
    try:
        if PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def keep_stable_copy() -> None:
    """Copy this script to the cache so a hotkey has a path that survives plugin updates."""
    here = Path(__file__).resolve()
    if here.parent == CACHE.resolve():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    for src in (here, here.with_name(here.name + ".lock")):
        dst = CACHE / src.name
        try:
            if src.exists() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
                tmp = dst.with_name(dst.name + ".tmp")
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read Claude Code's last reply out loud.")
    ap.add_argument("--transcript", type=Path, help="session transcript (.jsonl); default: the most recent one")
    ap.add_argument("--session", help="session id, to find its transcript when no path is given")
    ap.add_argument("--all", action="store_true", help="read the whole last turn, not just its final message")
    ap.add_argument("--text", help="read this text instead of a reply")
    ap.add_argument("--dry-run", action="store_true", help="print what would be read")
    ap.add_argument("--stop", action="store_true", help="stop the current reading")
    ap.add_argument("--setup", action="store_true", help="download the voice now")
    ap.add_argument("--voices", action="store_true", help="list the voices")
    ap.add_argument("--voice", default=os.environ.get("CLAUDE_SPEAK_VOICE") or DEFAULT_VOICE)
    ap.add_argument("--speed", type=float, default=_env_float("CLAUDE_SPEAK_SPEED", DEFAULT_SPEED))
    ap.add_argument(
        "--quality",
        choices=sorted(MODELS),
        default=os.environ.get("CLAUDE_SPEAK_QUALITY") if os.environ.get("CLAUDE_SPEAK_QUALITY") in MODELS else DEFAULT_QUALITY,
        help="fast (default) or high: slower, and a 326 MB download instead of 114 MB",
    )
    args = ap.parse_args(argv)

    if args.stop:
        reply = send({"cmd": "stop"})
        stopped = bool(reply.get("stopped")) if reply is not None else stop_playing()
        print("Stopped." if stopped else "Nothing was playing.")
        return 0
    if args.setup or args.voices:
        engine = load_kokoro(args.quality)
        print("\n".join(engine.get_voices()) if args.voices else f"Voice ready in {MODEL_DIR}")
        keep_stable_copy()
        return 0

    if args.text is not None:
        text = to_speech(args.text)
    else:
        path = args.transcript or (args.session and session_transcript(args.session)) or latest_transcript()
        if path is None or not path.exists():
            log(f"No transcript found ({path})")
            text = NOTHING_TO_READ
        else:
            log(f"Reading from {path}")
            text = to_speech(last_reply(entries_newest_first(path), whole_turn=args.all)) or NOTHING_TO_READ

    if args.dry_run:
        print(text)
        return 0

    request = {"cmd": "speak", "text": text, "voice": args.voice, "speed": args.speed, "quality": args.quality}
    if send(request) is not None:
        log("Handed to the voice that is already loaded")
        return 0
    keep_stable_copy()
    if serve(request):
        return 0
    # Another process is starting up as the voice right now: hand over once it's listening.
    for _ in range(50):
        time.sleep(0.1)
        if send(request) is not None:
            return 0
    claim_speaker()
    read_aloud(text, args.voice, args.speed, lambda: load_kokoro(args.quality))
    return 0


if __name__ == "__main__":
    sys.exit(main())
