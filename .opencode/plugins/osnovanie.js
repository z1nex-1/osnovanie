// Та же политика агента, что в Claude Code и Cursor: решение принимает tools/osn/guard.py.
import { spawnSync } from "node:child_process"
import { join } from "node:path"

export const Osnovanie = async ({ directory }) => {
  const hook = join(directory, "tools/hooks/opencode_hook.py")
  const call = (event, payload) => {
    const done = spawnSync("python3", [hook, event], { input: JSON.stringify(payload), cwd: directory, encoding: "utf8" })
    try {
      return JSON.parse(done.stdout || "{}")
    } catch {
      return { allow: false, reason: `Сбой политики агента: ${(done.stderr || "").slice(0, 200)}` }
    }
  }

  return {
    "tool.execute.before": async (input, output) => {
      const verdict = call("tool", { tool: input.tool, args: output.args || {} })
      if (verdict.allow === false) throw new Error(verdict.reason)
    },
    "chat.message": async (_input, output) => {
      const part = (output.parts || []).find((p) => p.type === "text")
      if (!part) return
      const reply = call("prompt", { prompt: part.text })
      if (reply.context) part.text = `${part.text}\n\n[osnovanie] ${reply.context}`
    },
    event: async ({ event }) => {
      if (event.type === "session.idle") call("idle", {})
    },
  }
}
