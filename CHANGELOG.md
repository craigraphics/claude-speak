# Changelog

## 0.4.2 (2026-11-10)

- Typed `speak`, `shh` and `stop` answer with a plain line, `speak: 🔇 Stopped.`, instead of "UserPromptSubmit operation blocked by hook", and the word no longer stays in the prompt box. Needs a Claude Code build that loads mods; only speak's own notices are changed.

## 0.4.1 (2026-11-10)

- `stop`, `shh` and `/speak:shh` now stop the voice on Linux. Linux `ps` cut the voice's command line to the terminal width, so speak didn't recognise its own process and `stop` went to Claude.

## 0.4.0 (2026-11-10)

- `--save FILE.wav` writes a reading to a sound file instead of playing it.
- The real voice is now tested on every push: on Linux, an Apple Silicon Mac and an Intel Mac, it downloads the voice, checks it, and generates speech.
- On Windows, speak says it isn't supported instead of answering "Reading" and failing out of sight. `stop` is left for Claude there.

## 0.3.2 (2026-11-10)

- An icon.

## 0.3.1 (2026-11-10)

- Type `stop` to end a reading. It is only caught while speak is reading; otherwise it goes to Claude as usual.
- `stop`, `shh` and `/speak:shh` now stop the voice at once, without starting uv, and leave it loaded for the next `speak`.

## 0.3.0 (2026-11-10)

- **Commands renamed:** `/speak`, `/speak:all` and `/speak:shh` (were `/speak:speak`, `/speak:speak-all` and `/speak:shh`). Update `keybindings.json`: `command:speak`, `command:speak:all`, `command:speak:shh`.
- `speak.py --uninstall` removes everything speak downloaded or made: the voice, its Python packages, the log and its key bindings.
- The voice model a computer doesn't use is deleted after a week without being loaded.
- A new session with no reply yet reads nothing, never another session's reply.
- A voice left loaded by another version of speak makes way for the new one.
- Security: the cache folder and log are private, the voice server's socket is never in a shared folder, downloads stop at the pinned size, and a full disk is reported before downloading.
- `CLAUDE_SPEAK_MODEL_URL` downloads the voice from a mirror, still checked against its SHA-256.
- Tests run on Linux and macOS for every push.

## 0.2.1 (2026-11-04)

- No more pauses mid-reply on Intel and AMD: there the full model is the default, which generates more than twice as fast as int8 on those chips. Apple Silicon keeps int8.
- Long early sentences are cut at commas, so each piece is ready before the last one ends.

## 0.2.0 (2026-11-04)

- Slash commands and key bindings (`ctrl+x s`, `ctrl+x a`, `ctrl+x q`).
- On Claude Code builds that load mods, the commands answer with a plain line instead of "UserPromptSubmit operation blocked by hook".

## 0.1.0 (2026-11-04)

- First release: type `speak`, `speak all` or `shh`. Kokoro reads Claude's last reply on your computer, free and offline after a one-time download, with the computer's own voice as a fallback.
