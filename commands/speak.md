---
description: Hear Claude's last reply (same as typing speak)
disable-model-invocation: true
---

The speak plugin's hook answers /speak before it reaches Claude, so this text is only sent when that hook didn't run. Reply with one short sentence: /speak takes no arguments, and if it still reaches Claude, restart Claude Code so the plugin's hook loads. Do nothing else.
