# Contributing

Bug reports and pull requests are welcome. For anything larger than a fix, open an issue first so we can agree on the approach.

## The code

| File | What it does |
| --- | --- |
| `hooks/hooks.json` | Registers the `UserPromptSubmit` hook and the mod |
| `scripts/hook.sh` | Runs on every prompt. Catches the trigger words, starts or stops a reading, and answers. Bash only |
| `hooks/speak.ts` | The mod: the `/speak` command, plain answer lines, and an empty prompt box afterwards. Optional: everything works without it |
| `scripts/speak.py` | Finds the reply, turns markdown into sentences, runs the voice, keeps it loaded, downloads, uninstall |
| `scripts/speak.py.lock` | The pinned Python packages |
| `commands/` | `/speak:all` and `/speak:shh` |
| `tests/` | pytest, with transcript fixtures |

## Rules the code follows

- **The hook stays out of the way.** For any prompt that isn't a trigger word it exits at the first check, without starting a process, in a few milliseconds. No jq, no Python, and it has to run under bash 3.2 (macOS).
- **Nothing leaves the computer**, and prompts are never logged. No telemetry.
- **Fail soft.** If the mod fails, the shell hook answers. If Kokoro can't run, the system voice reads. A reading never blocks Claude Code.
- **Pin what is downloaded**: packages in the lock file, voice files by size and SHA-256.
- **macOS and Linux, ARM and Intel/AMD.** Anything OS-specific needs a test or a CI step on both.
- **The hook's answers and the mod stay in step.** `tests/test_speak.py` checks that the mod recognises every answer `hook.sh` gives; add new answers to both.

## Checks

```sh
uv run --with pytest pytest tests
uvx --from shellcheck-py shellcheck scripts/hook.sh
claude plugin validate .
claude --plugin-dir .          # try it for real: speak, speak all, shh, stop
```

CI runs the tests and shellcheck on Linux and macOS, and the real voice on Ubuntu, an Apple Silicon Mac and an Intel Mac, on every push and once a week.

After changing the dependencies at the top of `speak.py`, run `uv lock --script scripts/speak.py` and commit the lock file.

## Releasing

1. Bump `version` in `.claude-plugin/plugin.json`. Without a new version, `claude plugin update` finds nothing to install.
2. Add the version to `CHANGELOG.md`, written for the people who use speak.
3. Push, and wait for CI to pass.
4. Tag and release:

   ```sh
   git tag -a vX.Y.Z -m "speak X.Y.Z" && git push origin vX.Y.Z
   gh release create vX.Y.Z --verify-tag --latest --title "speak X.Y.Z" --notes-file notes.md
   ```

Versions: a patch for fixes, a minor for new features, and say so at the top of the changelog entry when people have to change something themselves (as 0.3.0 did with the command names).
