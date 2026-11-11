"""Tests for the parts that don't need the voice: finding the reply, cleaning it, and the hook.

    uv run --with pytest pytest tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "hook.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_spec = importlib.util.spec_from_file_location("speak", ROOT / "scripts" / "speak.py")
speak = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(speak)


def reply(name: str, whole_turn: bool = False) -> str:
    return speak.last_reply(speak.entries_newest_first(FIXTURES / name), whole_turn=whole_turn)


# --- Finding the reply -------------------------------------------------------------------


def test_reads_the_final_message_after_the_last_tool_call():
    text = reply("session.jsonl")
    assert text.startswith("**Done.** The plugin is installed")
    assert "Let me check" not in text


def test_whole_turn_includes_the_narration():
    text = reply("session.jsonl", whole_turn=True)
    assert text.startswith("Let me check your setup.")
    assert "**Done.** The plugin is installed" in text
    assert "The capital of France" not in text  # the turn before


def test_skips_thinking_subagents_trigger_words_and_local_commands():
    text = reply("session.jsonl", whole_turn=True)
    assert "private reasoning" not in text
    assert "subagent" not in text
    assert "speak" not in text.lower().split()


def test_a_block_written_twice_is_read_once():
    assert reply("session.jsonl").count("The plugin is installed") == 1


def test_a_turn_with_no_text_yet_falls_back_to_the_previous_reply():
    assert reply("tool_only.jsonl") == "Here is the earlier answer."


def test_synthetic_and_error_messages_are_ignored():
    assert reply("tool_only.jsonl", whole_turn=True) == "Here is the earlier answer."


def test_an_unfinished_last_line_is_skipped(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text((FIXTURES / "session.jsonl").read_text() + '{"type": "assistant", "message": {"con')
    assert speak.last_reply(speak.entries_newest_first(path)).startswith("**Done.**")


def test_latest_transcript_honours_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    old = tmp_path / "home" / ".claude" / "projects" / "p" / "a.jsonl"
    new = tmp_path / "work" / "projects" / "q" / "b.jsonl"
    for f in (old, new):
        f.parent.mkdir(parents=True)
        f.write_text("{}\n")
    os.utime(old, (time.time() - 100, time.time() - 100))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(speak.Path, "home", lambda: tmp_path / "home")
    assert speak.latest_transcript() == new


def test_session_transcript_is_found_by_id(tmp_path, monkeypatch):
    f = tmp_path / ".claude-work" / "projects" / "p" / "abc-123.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}\n")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(speak.Path, "home", lambda: tmp_path)
    assert speak.session_transcript("abc-123") == f
    assert speak.session_transcript("missing") is None
    assert speak.session_transcript("../p/abc-123") is None


# --- Making it speakable -----------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown, spoken",
    [
        ("## Install", "Install."),
        ("**Bold** and *italic* and ~~gone~~", "Bold and italic and gone."),
        ("- first item\n- second item", "first item.\nsecond item."),
        ("1. step one", "step one."),
        ("See [the docs](https://example.com/a/b) now", "See the docs now."),
        ("Open https://www.github.com/x/y/blob/main/z.md", "Open github.com."),
        ("Edit `src/app/main.ts:42` first", "Edit main.ts first."),
        ("Saved to ~/.cache/claude-speak/", "Saved to claude-speak."),
        ("Works on Macs/Linux and/or both", "Works on Macs or Linux and or both."),
        ("Keep 24/7 and craigraphics/claude-speak", "Keep 24/7 and craigraphics/claude-speak."),
        ("A → B", "A to B."),
        ("~340 MB in 1.5s, 200ms", "about 340 megabytes in 1.5 seconds, 200 milliseconds."),
        ("The 1990s and #42", "The 1990s and number 42."),
        ("Free & offline 🔊 ✅", "Free and offline."),
        ("Set CLAUDE_SPEAK_VOICE now", "Set CLAUDE SPEAK VOICE now."),
        ("e.g. this, i.e. that", "for example, this, that is, that."),
        ("Note (an aside) here", "Note, an aside, here."),
        ("Next:", "Next."),
        ("---", ""),
        ("> quoted", "quoted."),
    ],
)
def test_to_speech(markdown, spoken):
    assert speak.to_speech(markdown) == spoken


def test_code_blocks_are_skipped_and_merged():
    md = "Before.\n```bash\necho hi\n```\n\n```python\nx = 1\n```\nAfter."
    assert speak.to_speech(md) == f"Before.\n{speak.CODE_SKIPPED}\nAfter."


def test_an_unclosed_code_block_hides_the_rest():
    assert speak.to_speech("Intro.\n```\ncode\nmore code") == f"Intro.\n{speak.CODE_SKIPPED}"


def test_tables_become_sentences():
    md = "| Option | Cost |\n|---|:---:|\n| Kokoro | free |"
    assert speak.to_speech(md) == "Option, Cost.\nKokoro, free."


def test_first_piece_is_short_so_the_voice_starts_fast():
    parts = speak.pieces("Short one. Then a much longer second sentence follows here. And a third.")
    assert parts[0][0] == "Short one."
    assert all(len(p) <= 300 for p, _ in parts)


def test_a_long_first_sentence_is_cut_at_its_first_comma():
    parts = speak.pieces("When the voice is already loaded, the next reading starts almost at once.")
    assert parts[0] == ("When the voice is already loaded,", 0.05)


REPLY = (
    "Here is what changed. On this laptop, the smaller voice model was slower than the speech it made, "
    "so the reading kept pausing between sentences. Speak now picks the full model on Intel and AMD "
    "processors, which runs more than twice as fast here. The next sentence is ready before the current "
    "one ends, so there should be no more waiting in the middle of a reply."
)


def test_early_long_sentences_are_cut_at_commas():
    assert [p for p, _ in speak.pieces(REPLY)][:4] == [
        "Here is what changed.",
        "On this laptop,",
        "the smaller voice model was slower than the speech it made,",
        "so the reading kept pausing between sentences.",
    ]


def test_no_piece_keeps_the_voice_waiting():
    # A voice that speaks 15 characters a second and generates twice as fast as that: every
    # piece must be ready by the time the pieces before it, and their pauses, have been heard.
    parts = speak.pieces(REPLY + " " + REPLY)
    ready = len(parts[0][0]) / 30
    plays_at = ready
    for (before, pause), (piece, _) in zip(parts, parts[1:]):
        ready += len(piece) / 30
        plays_at += len(before) / 15 + pause
        assert ready <= plays_at, piece
    assert " ".join(p for p, _ in parts) == REPLY + " " + REPLY


def test_paragraphs_get_a_longer_pause():
    parts = speak.pieces("One.\nTwo.")
    assert parts == [("One.", 0.45), ("Two.", 0.45)]


# --- The hook ----------------------------------------------------------------------------


@pytest.fixture
def hook_env(tmp_path):
    """A home with a stand-in `uv` that records how it was called instead of speaking."""
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    calls = tmp_path / "calls.txt"
    fake = bin_dir / "uv"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\ncase "$*" in *--stop*) echo "Stopped." ;; esac\n')
    fake.chmod(0o755)
    # The stand-in comes first on PATH, so a real uv (/usr/bin/uv on some Linux systems) is
    # never run: it would start real readings and download the voice into tmp_path.
    # COLUMNS: Claude Code sets it for hooks, and Linux ps cuts its output to that width.
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "XDG_CACHE_HOME": str(tmp_path / "cache"), "COLUMNS": "20"}
    return env, calls


def run_hook(env: dict, payload: dict | str) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(["bash", str(HOOK)], input=data, capture_output=True, text=True, env=env, timeout=10)


def wait_for(path: Path) -> str:
    for _ in range(50):
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.05)
    return ""


def test_hook_ignores_ordinary_prompts(hook_env):
    env, calls = hook_env
    for prompt in ("hello", "please speak to the API", "speak up about this", "shh is a word",
                   "/speakers", "/speak louder", "/other:speak", "/speak:", "/speak:other", "/all"):
        result = run_hook(env, {"prompt": prompt, "transcript_path": "/x.jsonl"})
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert not calls.exists()


def test_hook_is_fast_for_ordinary_prompts(hook_env):
    env, _ = hook_env
    started = time.monotonic()
    for _ in range(10):
        run_hook(env, {"prompt": "an ordinary prompt " * 50})
    assert (time.monotonic() - started) / 10 < 0.1


@pytest.mark.parametrize("prompt", ["speak", "Speak", "  speak  ", "speak\n", "SPEAK", "/speak"])
def test_hook_speaks_and_blocks_the_prompt(hook_env, prompt):
    env, calls = hook_env
    result = run_hook(env, {"session_id": "s", "transcript_path": "/tmp/a b/t.jsonl", "prompt": prompt})
    out = json.loads(result.stdout)
    assert out["decision"] == "block" and "shh" in out["reason"]
    call = wait_for(calls)
    assert "--transcript /tmp/a b/t.jsonl" in call and "--all" not in call and "speak.py" in call


def test_hook_finds_the_session_by_id_when_given_no_path(hook_env):
    # The mod (hooks/speak.ts) knows the session id but not the transcript path.
    env, calls = hook_env
    run_hook(env, {"prompt": "speak", "session_id": "4c42-b7"})
    call = wait_for(calls)
    assert "--session 4c42-b7" in call and "--transcript" not in call


@pytest.mark.parametrize("prompt", ["speak all", "/speak:all"])
def test_hook_speak_all(hook_env, prompt):
    env, calls = hook_env
    run_hook(env, {"transcript_path": "/t.jsonl", "prompt": prompt})
    assert "--all" in wait_for(calls)


@pytest.mark.parametrize("prompt", ["shh", "/shh", "/speak:shh"])
def test_hook_shh(hook_env, prompt):
    env, calls = hook_env
    out = json.loads(run_hook(env, {"prompt": prompt}).stdout)
    assert out["decision"] == "block" and out["reason"] == "🔇 Stopped."
    assert "--stop" in calls.read_text()


def test_hook_hides_the_original_prompt_line(hook_env):
    env, _ = hook_env
    out = json.loads(run_hook(env, {"prompt": "speak"}).stdout)
    assert out["hookSpecificOutput"] == {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": True}


def test_hook_says_how_to_get_uv_when_it_is_missing(hook_env):
    env, _ = hook_env
    (Path(env["HOME"]) / ".local" / "bin" / "uv").unlink()
    places = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin", "/home/linuxbrew/.linuxbrew/bin")
    if any(Path(p, "uv").exists() for p in places):
        pytest.skip("uv is installed system-wide here, where the hook looks")
    out = json.loads(run_hook(env, {"prompt": "speak"}).stdout)
    assert out["decision"] == "block" and "astral.sh/uv" in out["reason"]
    out = json.loads(run_hook(env, {"prompt": "shh"}).stdout)
    assert out["reason"] == "🔇 Nothing was playing."


@pytest.mark.parametrize(
    "machine, quality",
    [("arm64", "fast"), ("aarch64", "fast"), ("x86_64", "high"), ("AMD64", "high"), ("i686", "high")],
)
def test_the_default_model_suits_the_cpu(machine, quality):
    assert speak.default_quality(machine) == quality


def test_hook_expects_the_full_model_on_intel_and_amd(hook_env, tmp_path):
    env, _ = hook_env
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "uname").write_text("#!/bin/sh\necho x86_64\n")
    (fake / "uname").chmod(0o755)
    env = {**env, "PATH": f"{fake}:{env['PATH']}"}
    cache = Path(env["XDG_CACHE_HOME"]) / "claude-speak"
    cache.mkdir(parents=True)
    for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (cache / name).write_text("x")
    assert "350 MB" in json.loads(run_hook(env, {"prompt": "speak"}).stdout)["reason"]
    (cache / "kokoro-v1.0.onnx").write_text("x")
    assert json.loads(run_hook(env, {"prompt": "speak"}).stdout)["reason"] == "🔊 Reading. Type shh to stop."


def test_hook_mentions_the_first_download(hook_env):
    env, _ = hook_env
    env = {**env, "CLAUDE_SPEAK_QUALITY": "fast"}
    out = json.loads(run_hook(env, {"prompt": "speak"}).stdout)
    assert "First time" in out["reason"]
    cache = Path(env["XDG_CACHE_HOME"]) / "claude-speak"
    for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (cache / name).write_text("x")
    out = json.loads(run_hook(env, {"prompt": "speak"}).stdout)
    assert out["reason"] == "🔊 Reading. Type shh to stop."


# --- Reading transcripts -----------------------------------------------------------------


@pytest.mark.parametrize("chunk", [1, 7, 64, 1 << 20])
def test_reading_from_the_end_in_chunks_finds_every_entry(chunk):
    path = FIXTURES / "session.jsonl"
    whole = [json.loads(line) for line in reversed(path.read_bytes().splitlines()) if line.strip()]
    assert list(speak.entries_newest_first(path, chunk_size=chunk)) == whole


def test_a_line_longer_than_a_chunk_is_read_whole(tmp_path):
    path = tmp_path / "t.jsonl"
    long = {"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 5000}]}}
    path.write_text(json.dumps({"a": 1}) + "\n" + json.dumps(long) + "\n" + '{"half": ')
    assert list(speak.entries_newest_first(path, chunk_size=100)) == [long, {"a": 1}]


def test_the_transcript_named_or_the_session_s_never_another(tmp_path, monkeypatch):
    mine = tmp_path / "mine.jsonl"
    mine.write_text("{}\n")
    monkeypatch.setattr(speak, "session_transcript", lambda s: mine if s == "s1" else None)
    monkeypatch.setattr(speak, "latest_transcript", lambda: tmp_path / "someone-else.jsonl")
    assert speak.pick_transcript(mine, None) == mine
    assert speak.pick_transcript(tmp_path / "not-yet.jsonl", "s1") == mine
    assert speak.pick_transcript(tmp_path / "not-yet.jsonl", "new") is None  # a brand-new session
    assert speak.pick_transcript(tmp_path / "not-yet.jsonl", None) is None
    assert speak.pick_transcript(None, None) == tmp_path / "someone-else.jsonl"  # a hotkey


# --- The voice server and its requests ---------------------------------------------------


@pytest.mark.parametrize(
    "given, speed", [(1.3, 1.3), (0, 0.5), (-2, 0.5), (100, 2.0), ("fast", 1.1), (None, 1.1), (float("nan"), 1.1)]
)
def test_the_speed_stays_in_kokoro_s_range(given, speed):
    assert speak.clamp_speed(given) == speed


def test_odd_requests_get_the_defaults():
    text, voice, speed, quality = speak.reading({"text": 5, "voice": ["x"], "speed": "9e9", "quality": "best"})
    assert (text, voice, speed, quality) == ("5", speak.DEFAULT_VOICE, 2.0, speak.DEFAULT_QUALITY)


def test_a_server_of_another_version_makes_way():
    class Stub:
        stopped = started = False

        def stop(self):
            self.stopped = True

        def start(self, *a):
            self.started = True

    stub = Stub()
    assert speak.handle(stub, {"cmd": "speak", "v": "other", "text": "hi"}) == {"ok": False, "stale": True}
    assert stub.stopped and not stub.started
    assert speak.handle(Stub(), {"cmd": "speak", "v": speak.SCRIPT_ID, "text": "hi"}) == {"ok": True}
    assert speak.handle(Stub(), {"cmd": "stop"})["ok"] is True  # any version may stop it


def test_only_a_socket_of_this_user_s_is_trusted():
    import socket
    import tempfile

    path = Path(tempfile.mkdtemp(dir="/tmp")) / "s.sock"  # pytest's own folders are too long for a socket
    assert not speak.own_socket(path)
    path.write_text("not a socket")
    assert not speak.own_socket(path)
    path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.bind(str(path))
        assert speak.own_socket(path)


def test_private_folders(tmp_path):
    folder = speak._private_dir(tmp_path / "a" / "b")
    assert folder.stat().st_mode & 0o777 == 0o700
    os.chmod(folder, 0o755)
    speak._private_dir(folder)
    assert folder.stat().st_mode & 0o777 == 0o700
    link = tmp_path / "link"
    link.symlink_to(folder)
    speak._private_dir(link)  # a symlinked cache folder is fine
    with pytest.raises(PermissionError):
        speak._private_dir(link, follow_symlinks=False)  # but not in a shared temp folder


# --- Downloads and disk space ------------------------------------------------------------


def test_a_download_larger_than_pinned_is_refused(tmp_path):
    src = tmp_path / "model.bin"
    src.write_bytes(b"x" * 1000)
    part = tmp_path / "model.part"
    speak._fetch(src.as_uri(), part, 1000)
    assert part.read_bytes() == b"x" * 1000
    with pytest.raises(speak.TooLarge):
        speak._fetch(src.as_uri(), part, 999)


def test_the_model_this_computer_doesnt_use_goes_after_a_week(tmp_path, monkeypatch):
    monkeypatch.setattr(speak, "CACHE", tmp_path)
    fast, high = (tmp_path / speak.MODELS[q][0] for q in ("fast", "high"))
    for f in (fast, high):
        f.write_bytes(b"model")
    now = time.time()
    os.utime(high, (now - 3 * 86400, now - 3 * 86400))
    assert speak.drop_unused_models("fast", tmp_path, now) == []  # used 3 days ago: kept
    os.utime(high, (now - 8 * 86400, now - 8 * 86400))
    assert speak.drop_unused_models("fast", tmp_path, now) == [high]
    assert fast.exists() and not high.exists()
    assert speak.drop_unused_models("high", tmp_path / "elsewhere", now) == []  # a folder of the person's own


# --- Uninstalling ------------------------------------------------------------------------


def test_only_speak_s_shortcuts_are_removed(tmp_path):
    shared, only = tmp_path / "a", tmp_path / "b"
    for d in (shared, only, tmp_path / "c"):
        d.mkdir()
    (shared / "keybindings.json").write_text(json.dumps({"bindings": [
        {"context": "Chat", "bindings": {"ctrl+x s": "command:speak", "ctrl+e": "chat:externalEditor"}},
        {"context": "Global", "bindings": {"ctrl+x q": "command:speak:shh"}},
    ]}))
    (only / "keybindings.json").write_text(json.dumps({"$schema": "x", "bindings": [
        {"context": "Chat", "bindings": {"ctrl+x s": "command:speak", "ctrl+x a": "command:speak:all"}}]}))
    (tmp_path / "c" / "keybindings.json").write_text("{ not json")
    assert speak.remove_key_bindings([shared, only, tmp_path / "c"]) == [shared / "keybindings.json", only / "keybindings.json"]
    assert json.loads((shared / "keybindings.json").read_text()) == {
        "bindings": [{"context": "Chat", "bindings": {"ctrl+e": "chat:externalEditor"}}]}
    assert not (only / "keybindings.json").exists()
    assert (tmp_path / "c" / "keybindings.json").read_text() == "{ not json"


def test_uninstall_removes_what_speak_made_and_nothing_else(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache" / "claude-speak"
    cache.mkdir(parents=True)
    made = [speak.MODELS["fast"][0], speak.VOICES[0], "last.log", "speak.py", "speak.py.lock", "server.lock",
            ".download.lock", f"{speak.APP}-x.wav"]
    for name in made:
        (cache / name).write_text("x")
    (cache / "mine.txt").write_text("the person's own")
    env = tmp_path / "uv" / "environments-v2" / "speak-abc"
    (env / "lib" / "python3.13" / "site-packages" / "kokoro_onnx").mkdir(parents=True)
    calls = tmp_path / "uv-calls"
    fake_uv = tmp_path / "uv-bin"
    fake_uv.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho "Removed 3 files (1.0MiB)"\n')
    fake_uv.chmod(0o755)
    for name, value in {"CACHE": cache, "MODEL_DIR": cache, "SOCKET": cache / "speak.sock",
                        "PID_FILE": cache / "playing.pid"}.items():
        monkeypatch.setattr(speak, name, value)
    monkeypatch.setattr(speak, "uv_environments", lambda: [env])
    monkeypatch.setattr(speak, "account_dirs", lambda: [])
    monkeypatch.setenv("UV", str(fake_uv))
    assert speak.uninstall() == 0
    assert sorted(p.name for p in cache.iterdir()) == ["mine.txt"]
    assert not env.exists()
    assert calls.read_text().split() == ["cache", "clean", *speak.UV_PACKAGES]
    out = capsys.readouterr().out
    assert "claude plugin uninstall speak@claude-speak" in out and "files speak didn't make" in out


# --- The hook, again ---------------------------------------------------------------------


def test_the_hook_s_answer_is_valid_json_whatever_it_says(hook_env):
    env, _ = hook_env
    fake = Path(env["HOME"]) / ".local" / "bin" / "uv"
    fake.write_text('#!/bin/sh\nprintf \'Said "hi" \\\\ back\\tthen\'\n')
    out = json.loads(run_hook(env, {"prompt": "shh"}).stdout)
    assert out["reason"] == '🔇 Said "hi" \\ back then'


def test_shh_never_downloads_anything(hook_env):
    env, calls = hook_env
    run_hook(env, {"prompt": "shh"})
    assert "--offline" in calls.read_text()


def test_the_hook_passes_the_path_and_the_session(hook_env):
    env, calls = hook_env
    run_hook(env, {"session_id": "abc-1", "transcript_path": "/t.jsonl", "prompt": "speak"})
    call = wait_for(calls)
    assert "--transcript /t.jsonl" in call and "--session abc-1" in call


def test_the_hook_is_fast_even_for_a_huge_prompt(hook_env):
    env, calls = hook_env
    started = time.monotonic()
    result = run_hook(env, {"prompt": "speak " * 300_000, "transcript_path": "/t.jsonl"})
    assert time.monotonic() - started < 1 and result.stdout == "" and not calls.exists()


def test_the_hook_keeps_its_files_private(hook_env):
    env, _ = hook_env
    run_hook(env, {"prompt": "shh"})
    cache = Path(env["XDG_CACHE_HOME"]) / "claude-speak"
    assert cache.stat().st_mode & 0o777 == 0o700
    assert (cache / "last.log").stat().st_mode & 0o777 == 0o600


# --- stop ---------------------------------------------------------------------------------


@pytest.fixture
def reading(hook_env, tmp_path):
    """A stand-in speak.py, reading in its own process group, that notes the SIGUSR1 it gets."""
    env, _ = hook_env
    fake = tmp_path / "speak.py"
    got = tmp_path / "got-signal"
    fake.write_text(
        "import pathlib, signal, sys, time\n"
        f"signal.signal(signal.SIGUSR1, lambda *_: (pathlib.Path({str(got)!r}).write_text('USR1'), sys.exit(0)))\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, str(fake)], start_new_session=True, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    cache = Path(env["XDG_CACHE_HOME"]) / "claude-speak"
    cache.mkdir(parents=True)
    (cache / "reading").write_text(str(proc.pid))
    yield got
    proc.kill()
    proc.wait()


@pytest.mark.parametrize("prompt", ["stop", "Stop", " stop\n", "shh", "/speak:shh"])
def test_stop_and_shh_end_a_reading_at_once(hook_env, reading, prompt):
    env, calls = hook_env
    started = time.monotonic()
    out = json.loads(run_hook(env, {"prompt": prompt}).stdout)
    assert out["decision"] == "block" and out["reason"] == "🔇 Stopped."
    assert wait_for(reading) == "USR1"
    assert time.monotonic() - started < 1.5
    assert not calls.exists()  # no uv: that is what makes it instant


def test_the_voice_process_is_found_in_a_narrow_terminal(hook_env, reading, tmp_path, monkeypatch):
    env, _ = hook_env
    pid = (Path(env["XDG_CACHE_HOME"]) / "claude-speak" / "reading").read_text()
    monkeypatch.setattr(speak, "PID_FILE", tmp_path / "playing.pid")
    speak.PID_FILE.write_text(pid)
    monkeypatch.setenv("COLUMNS", "20")
    assert speak._playing_pid() == int(pid)


def test_stop_with_nothing_reading_goes_to_claude(hook_env):
    env, calls = hook_env
    for prompt in ("stop", "stop the build", "please stop"):
        result = run_hook(env, {"prompt": prompt})
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert not calls.exists() and not (Path(env["XDG_CACHE_HOME"]) / "claude-speak").exists()


def test_a_stale_or_foreign_reading_mark_is_ignored(hook_env, tmp_path):
    env, calls = hook_env
    cache = Path(env["XDG_CACHE_HOME"]) / "claude-speak"
    cache.mkdir(parents=True)
    other = subprocess.Popen(["sleep", "30"], start_new_session=True)  # not speak: never signalled
    try:
        for pid in ("999999", str(other.pid), "not a pid"):
            (cache / "reading").write_text(pid)
            assert run_hook(env, {"prompt": "stop"}).stdout == ""
            assert json.loads(run_hook(env, {"prompt": "shh"}).stdout)["reason"] == "🔇 Stopped."  # via uv
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_the_hook_marks_a_reading_it_starts(hook_env):
    env, _ = hook_env
    run_hook(env, {"prompt": "speak", "transcript_path": "/t.jsonl"})
    mark = (Path(env["XDG_CACHE_HOME"]) / "claude-speak" / "reading").read_text().strip()
    assert mark.isdigit()


def test_the_voice_server_marks_its_readings(tmp_path, monkeypatch):
    monkeypatch.setattr(speak, "CACHE", tmp_path)
    monkeypatch.setattr(speak, "READING", tmp_path / "reading")
    seen = []
    monkeypatch.setattr(speak, "read_aloud", lambda *a: seen.append((tmp_path / "reading").read_text()))
    speaker = speak.Speaker()
    speaker.start("Hello.", "af_heart", 1.0, "fast")
    for _ in range(50):
        if not speaker.busy:
            break
        time.sleep(0.02)
    assert seen == [str(os.getpid())] and not (tmp_path / "reading").exists()


# --- Windows ------------------------------------------------------------------------------


@pytest.mark.parametrize("ostype", ["msys", "cygwin"])
def test_on_windows_the_hook_says_so_instead_of_failing_quietly(hook_env, ostype):
    env, calls = hook_env

    def run(prompt: str) -> str:
        # OSTYPE is bash's own: set it inside the shell, then run the hook there.
        return subprocess.run(["bash", "-c", f'OSTYPE={ostype}; . "$0"', str(HOOK)], input=json.dumps({"prompt": prompt}),
                              capture_output=True, text=True, env=env, timeout=10).stdout

    assert "Windows isn't supported" in json.loads(run("speak"))["reason"]
    assert "Windows isn't supported" in json.loads(run("/speak:shh"))["reason"]
    assert run("stop") == ""  # left for Claude
    assert not calls.exists()


def test_speak_py_says_windows_isnt_supported(monkeypatch, capsys):
    monkeypatch.setattr(speak.os, "name", "nt")
    assert speak.main(["--text", "hi"]) == 1
    assert "Windows" in capsys.readouterr().err
