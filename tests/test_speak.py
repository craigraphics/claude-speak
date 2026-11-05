"""Tests for the parts that don't need the voice: finding the reply, cleaning it, and the hook.

    uv run --with pytest pytest tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "XDG_CACHE_HOME": str(tmp_path / "cache")}
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
                   "/speakers", "/speak louder", "/other:speak", "/speak:"):
        result = run_hook(env, {"prompt": prompt, "transcript_path": "/x.jsonl"})
        assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert not calls.exists()


def test_hook_is_fast_for_ordinary_prompts(hook_env):
    env, _ = hook_env
    started = time.monotonic()
    for _ in range(10):
        run_hook(env, {"prompt": "an ordinary prompt " * 50})
    assert (time.monotonic() - started) / 10 < 0.1


@pytest.mark.parametrize("prompt", ["speak", "Speak", "  speak  ", "speak\n", "SPEAK", "/speak", "/speak:speak"])
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


@pytest.mark.parametrize("prompt", ["speak all", "/speak-all", "/speak:speak-all"])
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
    if any(Path(p).exists() for p in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv")):
        pytest.skip("uv is installed system-wide here")
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
