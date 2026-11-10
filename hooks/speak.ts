import type { EngineInterface, Register } from 'claude-code'

// On Claude Code builds that load mods, this module adds the /speak command and answers it,
// /speak:all and /speak:shh (typed, or sent by a key binding) itself, so the answer shows as
// the command's output ("⎿ speak: 🔊 Reading…") instead of "UserPromptSubmit operation
// blocked by hook". /speak:all and /speak:shh are files in commands/, so builds without mods
// have them too, answered by the settings hook in hooks.json; /speak needs this module, and
// typing the word speak works everywhere. It leaves typed words to that hook: a mod can only
// drop a typed prompt, which Claude Code shows as "Prompt dropped by a hook" and puts back in
// the box. scripts/hook.sh does the work either way, and if anything here fails, the command
// goes on to that hook.

const COMMANDS: Record<string, string> = {
  speak: 'speak',
  'speak:all': 'speak all',
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
  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({ name: 'speak', description: "Hear Claude's last reply (same as typing speak)" })
    } catch (error) {
      $.ui.log(`speak: /speak not added (${String(error)}); typing speak still works`, { to: 'debug' })
    }
    return next(e)
  })

  on('command.run', { command: Object.keys(COMMANDS) }, async ($, e) => ({
    text: await answer($, COMMANDS[e.command] ?? 'speak'),
  })).catch(($, e, next) => next(e))
}
