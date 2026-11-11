# Security

## Reporting a problem

Please don't open a public issue for a security problem. Report it privately through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**. If that isn't available, open an issue that says only that you have a security report, and I'll arrange a private channel.

You should hear back within a week. Fixes go out as a new version, noted in [CHANGELOG.md](CHANGELOG.md).

## Supported versions

Only the latest release gets fixes. Update with:

```sh
claude plugin marketplace update claude-speak
claude plugin update speak@claude-speak
```

## What speak is trusted with

speak runs as you, on every prompt, and reads your Claude Code session transcripts. The design keeps that small:

- The hook acts only on the exact words `speak`, `speak all`, `shh` and `stop` and the `/speak` commands. It never logs, stores or sends a prompt.
- Nothing about you or your sessions leaves the computer. The only network use is the first download: the voice files (pinned by size and SHA-256, refused if larger) and Python packages through uv (pinned with hashes in `scripts/speak.py.lock`).
- Files and the voice server's socket live in a folder only you can open. speak refuses a folder or socket owned by someone else, and refuses symlinks in shared temp folders.
- Before it signals a process, speak checks that the process is its own.

The README's [Privacy and security](README.md#privacy-and-security) section says the same for users. A report is welcome for anything that breaks one of these promises.
