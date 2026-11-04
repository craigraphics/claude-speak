---
description: Hear Claude's whole last turn (same as typing speak all)
disable-model-invocation: true
---

The speak plugin's hook answers /speak-all before it reaches Claude, so this text is only sent when that hook didn't run. Reply with one short sentence: /speak-all takes no arguments, and if it still reaches Claude, restart Claude Code so the plugin's hook loads. Do nothing else.
