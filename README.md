# speak

Hear Claude Code's replies instead of reading them. Type `speak` at the prompt and Claude's last reply is read out loud in a natural voice. Type `shh` to stop.

- **Free.** No API key, no account, no subscription. The voice ([Kokoro](https://huggingface.co/hexgrad/Kokoro-82M)) runs on your computer and works offline after a one-time download: about 140 MB on Apple Silicon, 350 MB on Intel and AMD.
- **Instant.** A hook catches the word before it reaches Claude, so listening costs no tokens and doesn't wait on the model. After the first reading the voice stays loaded for a while, and the next one starts in under a second.
- **Only when you ask.** Nothing is read automatically.

## Install

You need macOS or Linux, Claude Code, and [uv](https://docs.astral.sh/uv/). If you don't have uv, run `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `brew install uv`).

```sh
claude plugin marketplace add craigraphics/claude-speak
claude plugin install speak@claude-speak
```

Then restart Claude Code. You can also do it from inside Claude Code with `/plugin marketplace add craigraphics/claude-speak` and `/plugin install speak@claude-speak`.

Install once per computer. If you keep several Claude accounts in separate config folders (`CLAUDE_CONFIG_DIR`), install once for each account too. All accounts on a computer share the same voice download.

**Linux:** for gapless audio, also install PortAudio (`sudo apt install libportaudio2`). Without it, speak plays through `paplay`, `pw-play` or `aplay`. For a backup voice, also install `espeak-ng`.

## Use

| Type at the prompt | Or press | What you hear |
| --- | --- | --- |
| `speak` | `ctrl+x s` | Claude's last reply: the message it wrote after its last tool call |
| `speak all` | `ctrl+x a` | everything Claude wrote in its last turn, including "let me check…" |
| `shh` | `ctrl+x q` | silence |

The keys work once you've added them (see [Keyboard shortcuts](#keyboard-shortcuts)). The commands `/speak`, `/speak-all` and `/shh` do the same as the words.

The keys and commands answer with a plain line, `⎿ speak: 🔊 Reading. Type shh to stop.` A typed word gets the same answer under Claude Code's own label, "UserPromptSubmit operation blocked by hook". Claude Code always adds that label when a typed prompt is held back, and nothing can turn it off. Both ways skip Claude, so neither uses tokens. Typing `speak` while it's reading starts again from the top. The first time, the voice downloads first (a few seconds on a fast connection) and the reading starts when that's done.

Replies are turned into speech, not read symbol by symbol:

- code blocks are skipped
- links are read as their text
- file paths are read as the file name
- tables are read row by row

## Settings

Set these as environment variables in your shell profile, or in the `env` section of Claude Code's `settings.json`. Then start a new Claude Code session.

```json
{ "env": { "CLAUDE_SPEAK_VOICE": "bm_george", "CLAUDE_SPEAK_SPEED": "1.2" } }
```

| Variable | Default | What it does |
| --- | --- | --- |
| `CLAUDE_SPEAK_VOICE` | `af_heart` | Which voice reads (see below) |
| `CLAUDE_SPEAK_SPEED` | `1.1` | `1.0` is Kokoro's normal pace |
| `CLAUDE_SPEAK_QUALITY` | depends on the CPU | `fast` is the int8 model (a 114 MB download), `high` the full-precision one (326 MB). By default speak uses whichever generates faster on your CPU: `fast` on Apple Silicon and other ARM chips, where it's 2–3× quicker, and `high` on Intel and AMD, where int8 is the slower one, by more than 2× on some chips. If the voice pauses mid-reading, your computer is generating slower than it speaks: try the other one |
| `CLAUDE_SPEAK_IDLE` | `600` | How many seconds the voice stays loaded after a reading. `0` unloads it right away |
| `CLAUDE_SPEAK_ENGINE` | | `system` uses the computer's own voice instead of Kokoro |
| `CLAUDE_SPEAK_THREADS` | auto | CPU threads for the voice. On Apple Silicon the default is the performance cores |
| `CLAUDE_SPEAK_DEBUG` | | `1` logs how long each sentence takes to generate |

**Voices.** The first letter of a voice name is its accent or language: `a` is American, `b` is British. The second letter is `f` for female or `m` for male.

- American: `af_heart`, `af_bella`, `af_nicole`, `af_sarah`, `am_michael`, `am_fenrir`, `am_puck`, `am_adam`
- British: `bf_emma`, `bf_isabella`, `bf_alice`, `bm_george`, `bm_fable`, `bm_daniel`
- Other languages: Spanish (`ef_dora`, `em_alex`), French (`ff_siwis`), Italian (`if_sara`, `im_nicola`), Hindi (`hf_alpha`, `hm_omega`) and Brazilian Portuguese (`pf_dora`, `pm_alex`)

To try a voice before choosing it:

```sh
CLAUDE_SPEAK_VOICE=bm_george uv run --script ~/.cache/claude-speak/speak.py --text "Hello, this is how I sound."
```

To list every voice: `uv run --script ~/.cache/claude-speak/speak.py --voices`.

## Keyboard shortcuts

**In Claude Code.** Put this in `~/.claude/keybindings.json`, or in `keybindings.json` inside your `CLAUDE_CONFIG_DIR` for another account. If the file already exists, add the `Chat` block to its `bindings` list. Then start a new Claude Code session.

```json
{
  "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
  "bindings": [
    {
      "context": "Chat",
      "bindings": {
        "ctrl+x s": "command:speak:speak",
        "ctrl+x a": "command:speak:speak-all",
        "ctrl+x q": "command:speak:shh"
      }
    }
  ]
}
```

`ctrl+x s` means press `ctrl+x`, let go, then press `s`. You have 3 seconds for the second key. Claude Code uses `ctrl+x` this way for its own two-key shortcuts, and these three are free. A shortcut runs its command right away and replaces anything you've typed but not sent, so use it on an empty prompt. You can choose other keys: see [Claude Code's keybindings docs](https://code.claude.com/docs/en/keybindings).

**Anywhere on your computer (optional).** After the first reading, `~/.cache/claude-speak/speak.py` holds a copy of the script that stays at that path when the plugin updates. You can bind it to a keyboard shortcut. It reads the newest reply from whichever Claude Code session wrote last, even while Claude is still working. In the commands below, replace `~/.local/bin/uv` with the output of `which uv`.

- **macOS:** in the Shortcuts app, create a shortcut with a *Run Shell Script* action:
  `~/.local/bin/uv run --quiet --script ~/.cache/claude-speak/speak.py`
  Then open the shortcut's details and add a keyboard shortcut. You can make a second shortcut that ends with `--stop`.
- **GNOME / KDE:** add a custom keyboard shortcut with the command
  `sh -c '~/.local/bin/uv run --quiet --script ~/.cache/claude-speak/speak.py'`

## How it works

1. **The hook.** `hooks/hooks.json` registers a `UserPromptSubmit` hook, `scripts/hook.sh`, which looks at every prompt. Anything other than `speak`, `speak all` or `shh` (or the `/speak`, `/speak-all` and `/shh` commands, which a key shortcut sends) passes straight through in a few milliseconds without starting a process. For those, the hook starts `scripts/speak.py` in the background and blocks the prompt, so it never reaches Claude. On Claude Code builds that load mods, `hooks/speak.ts` answers the `/speak` commands before that hook would, so they show a plain line instead of the "blocked by hook" label. It runs the same `hook.sh`, and builds without mods fall back to the hook.
2. **The reading.** `speak.py` finds the reply in the session transcript (`~/.claude/projects/…/<session>.jsonl`) and turns its markdown into sentences. Kokoro reads them, and the next sentence is generated while the current one plays.
3. **The warm voice.** The first reading keeps the voice loaded and listens on a private socket (`~/.cache/claude-speak/speak.sock`), so later readings start at once. It unloads after 10 idle minutes.
4. **The fallbacks.** If Kokoro can't run, the computer's own voice reads instead: `say` on macOS, `espeak-ng` on Linux.
5. **Pinning.** Dependencies are pinned in `scripts/speak.py.lock`, and the voice files are checked against their SHA-256.

## Troubleshooting

- **Typing `speak` sends the word to Claude.** Restart Claude Code after installing. Then check that `/plugin` shows speak as enabled and that `/hooks` lists its `UserPromptSubmit` hook.
- **A shortcut key does nothing.** Start a new session after editing `keybindings.json`. If it still does nothing, run `claude --debug` and look for lines starting with `[keybindings]`.
- **The voice pauses between sentences.** The computer is generating speech slower than it plays. Run once with `CLAUDE_SPEAK_DEBUG=1`: `last.log` then says which pieces were late. Try the other `CLAUDE_SPEAK_QUALITY`, and close anything busy on the CPU.
- **No sound, or the robotic voice.** Look in `~/.cache/claude-speak/last.log`.
- **Linux, no sound at all.** Install `libportaudio2`, or `pulseaudio-utils` / `alsa-utils`.

## Uninstall

```sh
claude plugin uninstall speak@claude-speak
claude plugin marketplace remove claude-speak
rm -rf ~/.cache/claude-speak
```

## Development

```sh
uv run --with pytest pytest tests     # tests: finding replies, cleaning text, the hook
claude --plugin-dir .                  # try this checkout in Claude Code
claude plugin validate .               # check the manifest, hooks.json and hooks/speak.ts
uv lock --script scripts/speak.py      # after changing the dependencies at the top of speak.py
```

## License

MIT. The voice is [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad (Apache 2.0), run through [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) by thewh1teagle (MIT). Both are downloaded from their own releases, not bundled here.
