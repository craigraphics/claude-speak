---
description: Stop the reading (same as typing shh)
disable-model-invocation: true
---

The speak plugin's hook answers /shh before it reaches Claude, so this text is only sent when that hook didn't run. Reply with one short sentence: /shh takes no arguments, and if it still reaches Claude, restart Claude Code so the plugin's hook loads. Do nothing else.
