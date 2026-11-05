import type { EngineInterface, Register } from 'claude-code'

// On Claude Code builds that load mods, this module answers /speak, /speak-all and /shh
// (typed, or sent by a key binding) itself, so the answer shows as the command's output
// ("⎿ speak: 🔊 Reading…") instead of "UserPromptSubmit operation blocked by hook". It
// leaves the typed words to the settings hook in hooks.json: a mod can only drop a typed
// prompt, which Claude Code shows as "Prompt dropped by a hook" and puts back in the box.
// Builds without mods skip this file, and that hook answers the commands too. Either way
// scripts/hook.sh does the work, and if anything here fails the command goes on to it.

const COMMANDS: Record<string, string> = {
  'speak:speak': 'speak',
  'speak:speak-all': 'speak all',
  'speak:shh': 'shh',
}

async function answer($: EngineInterface, word: string): Promise<string> {
  const payload = JSON.stringify({ prompt: word, session_id: await $.session.id() })
  const { stdout } = await $.process.run(['bash', `${$.plugin.root}/scripts/hook.sh`], {
    stdin: payload,
    timeoutMs: 15_000,
  })
  const reason: unknown = JSON.parse(stdout).reason
  if (typeof reason !== 'string') throw new Error('hook.sh gave no answer')
  return reason
}

export const register: Register = on => {
  on('command.run', { command: Object.keys(COMMANDS) }, async ($, e) => ({
    text: await answer($, COMMANDS[e.command] ?? 'speak'),
  })).catch(($, e, next) => next(e))
}
