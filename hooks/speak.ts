import type { EngineInterface, Register } from 'claude-code'

// On Claude Code builds that load mods, this module adds the /speak command and answers it,
// /speak:all and /speak:shh (typed, or sent by a key binding) itself, so the answer shows as
// the command's output ("⎿ speak: 🔊 Reading…") instead of "UserPromptSubmit operation
// blocked by hook". /speak:all and /speak:shh are files in commands/, so builds without mods
// have them too, answered by the settings hook in hooks.json; /speak needs this module, and
// typing the word speak works everywhere. scripts/hook.sh does the work either way, and if
// anything here fails, the command goes on to that hook.
//
// Typed words (speak, shh, stop) stay with the settings hook: a mod can't take a typed prompt
// without a notice of its own. The hook's answer is stored as a notice headed "UserPromptSubmit
// operation blocked by hook:", which reads like an error, so this module rewrites speak's
// notices, and only speak's, to the plain line a command shows: "speak: 🔇 Stopped."

const COMMANDS: Record<string, string> = {
  speak: 'speak',
  'speak:all': 'speak all',
  'speak:shh': 'shh',
}

const BLOCKED = 'UserPromptSubmit operation blocked by hook:\n'
// Every answer scripts/hook.sh gives, and nothing else: another hook's notices stay as they are.
const SPEAKS = /^(🔇 (Stopped|Nothing was playing)\.|🔊 (Reading\. |First time: downloading the voice )|speak (works on|needs uv))/u

// Claude Code puts a typed word it didn't send back in the prompt box. Right after speak
// answered one, a box holding just that word is emptied, so the next prompt starts clean.
const TYPED = /^\s*(speak(\s+all)?|shh|stop)\s*$/i
let answeredAt = 0

async function clearTypedWord($: EngineInterface): Promise<void> {
  if (Date.now() - answeredAt > 3000) return
  const { text } = await $.prompt.read()
  if (TYPED.test(text)) await $.prompt.fill({ text: '', mode: 'replace' })
}

// The notice without its heading, if it is one of speak's answers; otherwise undefined.
function plainNotice(text: string): string | undefined {
  if (!text.startsWith(BLOCKED)) return undefined
  const reason = text.slice(BLOCKED.length)
  if (!SPEAKS.test(reason)) return undefined
  return reason.startsWith('speak ') ? reason : `speak: ${reason}`
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

  on('session.append', { door: 'notice' }, async ($, e, next) => {
    const [block, ...rest] = e.message.content
    const notice = block?.type === 'text' && rest.length === 0 ? block.text : undefined
    const text = typeof notice === 'string' ? plainNotice(notice) : undefined
    if (text === undefined) return next(e)
    answeredAt = Date.now()
    const stored = await next({ ...e, message: { ...e.message, content: [{ type: 'text', text }] } })
    await clearTypedWord($).catch(() => undefined)
    return stored
  }).catch(($, e, next) => next(e))

  on('prompt.change', { origin: { kind: 'engine' } }, async ($, e, next) => {
    const result = await next(e)
    await clearTypedWord($).catch(() => undefined)
    return result
  })
}
