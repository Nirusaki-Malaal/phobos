#!/usr/bin/env node
/**
 * Terrain service shim — HTTP + MCP front end for the `terrain` CLI.
 *
 * Terrain ships as a CLI only (no serve/daemon subcommand). pai-stack agents
 * need a network endpoint, so this process owns the binary and exposes it two
 * ways on one port:
 *
 *   HTTP  POST /call   { action, params }   → Hermes (Python tool)
 *   MCP   POST /mcp    JSON-RPC 2.0        → DSH (@deepseek-ai/dsh-mcp-client)
 *   GET   /healthz                         → compose healthcheck
 *
 * MCP is implemented as plain JSON-RPC rather than via the SDK so the image
 * needs no npm install stage and there is no SDK version to drift. See
 * .planning/2026-10-04-terrain-service/findings.md §4.
 *
 * SAFETY — two invariants this file exists to enforce:
 *   1. Subcommand allowlist. `terrain env apply` rewrites AGENTS.md and installs
 *      preset skills from `.agents/skills`, overwriting files pai-stack owns, so
 *      the whole `env` tree is unreachable. Same for `sdd`, `settings`, `usage`
 *      and the raw `tools` agent surface.
 *   2. Path confinement. Index targets must resolve inside TERRAIN_WORKSPACE.
 *      Without this, any agent holding this tool could ask terrain to walk
 *      /var/lib/terrain or the container image itself.
 */

import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { realpathSync, existsSync } from 'node:fs'
import { readFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { resolve, join, dirname, isAbsolute } from 'node:path'

const PORT = Number(process.env.TERRAIN_PORT ?? 7878)
const WORKSPACE = resolve(process.env.TERRAIN_WORKSPACE ?? '/opt/data/workspace')
const BIN = process.env.TERRAIN_BIN ?? 'terrain'
const TOKEN = process.env.TERRAIN_TOKEN || null

/**
 * Seed the ACP agent's config from the pristine image copy onto the volume.
 * XDG_CONFIG_HOME has to be writable — opencode mkdirs `$XDG_CONFIG_HOME/opencode/`
 * on startup, which fails under this container's `read_only: true` if it points
 * into the image layer. So the real copy lives on the terrain_data volume and is
 * restored from the image on every boot. Never overwrites an existing file, so a
 * hand-edited config survives restarts. Failure is non-fatal: terrain still
 * indexes and searches; only init/ask lose their agent.
 */
function seedAcpConfig() {
  const src = '/opt/terrain/opencode.json'
  const xdg = process.env.XDG_CONFIG_HOME
  if (!xdg || !existsSync(src)) return
  try {
    const dst = join(xdg, 'opencode', 'opencode.json')
    if (existsSync(dst)) return
    mkdirSync(dirname(dst), { recursive: true })
    copyFileSync(src, dst)
    console.error(`[terrain] seeded ACP agent config -> ${dst}`)
  } catch (e) {
    console.error(`[terrain] could not seed ACP config: ${e.message} (init/ask may be unavailable)`)
  }
}

/** Actions exposed to agents. Anything not listed here is unreachable. */
const FORBIDDEN = new Set(['env', 'sdd', 'settings', 'usage', 'tools'])

/** action → { bin: [argv prefix], llm: boolean } */
const ACTIONS = {
  index: { bin: ['scan'], llm: false },
  init: { bin: ['init'], llm: true },
  refresh: { bin: ['refresh'], llm: false },
  search: { bin: ['search'], llm: false },
  read: { bin: ['read'], llm: false },
  overview: { bin: ['project', 'overview'], llm: false },
  projects: { bin: ['project', 'list'], llm: false },
  unregister: { bin: ['project', 'remove'], llm: false },
  source: { bin: ['source', 'read'], llm: false, repoFlag: true },
  ask: { bin: ['ask', 'query'], llm: true },
}

const SCHEMAS = {
  index: { path: 'string?', slug: 'string?' },
  init: { path: 'string?', slug: 'string?' },
  refresh: { path: 'string?', slug: 'string?' },
  search: { query: 'string', project: 'string?', limit: 'int?' },
  read: { path: 'string' },
  overview: { project: 'string' },
  projects: {},
  unregister: { project: 'string' },
  source: { file: 'string?', project: 'string?', start_line: 'int?', end_line: 'int?' },
  ask: { query: 'string', project: 'string?' },
}

const LLM_ACTIONS = Object.entries(ACTIONS).filter(([, v]) => v.llm).map(([k]) => k)

// ── per-repo serialisation ───────────────────────────────────────────────────
// Two concurrent mutating runs against one repo interleave writes into .terrain/
// and corrupt it. Terrain tracks git HEAD itself, so we cannot lean on
// that. See run(): the chain key is the repo, not the action.
const chains = new Map()
function serialise(key, fn) {
  const prev = chains.get(key) ?? Promise.resolve()
  const next = prev.then(fn, fn)
  chains.set(key, next.catch(() => {}))
  return next
}

// ── path confinement ─────────────────────────────────────────────────────────
// `what` only labels the error — the check is identical for files and dirs.
function confine(p, what = 'directory') {
  if (!p) return WORKSPACE
  const abs = isAbsolute(p) ? resolve(p) : resolve(WORKSPACE, p)
  if (abs !== WORKSPACE && !abs.startsWith(WORKSPACE + '/')) {
    throw Object.assign(new Error(`path escapes workspace: ${p}`), { status: 403 })
  }
  if (!existsSync(abs)) {
    throw Object.assign(new Error(`no such ${what}: ${abs}`), { status: 400 })
  }
  try {
    const real = realpathSync(abs)
    if (real !== WORKSPACE && !real.startsWith(WORKSPACE + '/')) {
      throw Object.assign(new Error(`path resolves outside workspace: ${p}`), { status: 403 })
    }
  } catch (e) {
    if (e.status) throw e
  }
  return abs
}

// ── argv construction ────────────────────────────────────────────────────────
// spawn() with an argv array — never a shell string, so params cannot inject.
function buildArgv(action, params = {}) {
  const spec = ACTIONS[action]
  if (!spec) throw Object.assign(new Error(`unknown action: ${action}`), { status: 400 })
  if (spec.bin.some((a) => FORBIDDEN.has(a))) {
    throw Object.assign(new Error(`action ${action} is blocked`), { status: 403 })
  }

  const argv = [...spec.bin]

  // Two different repo-targeting conventions. Registry-based commands
  // (search/overview/ask) take `--project <slug>`; `source read` takes
  // `--repo-path <abs>` and rejects --project outright. Emitting the wrong one
  // fails the whole call, so branch on the spec rather than guessing.
  if (spec.repoFlag) {
    const rp = params.repo_path ?? params.path
    if (rp) argv.push('--repo-path', confine(rp))
  } else {
    const pathKeys = ['path', 'repo_path']
    for (const k of pathKeys) {
      if (params[k]) argv.push(confine(params[k]))
    }
  }

  // `terrain source read` is a SUBCOMMAND taking --file/--start-line/--end-line.
  // This used to push a bare positional, so every call died with
  // "unrecognized subcommand '<file>'" and the action was unusable.
  // `--file` is a caller-supplied path, so it is confined like every
  // other path — otherwise `source` could read outside the workspace.
  if (params.file) argv.push('--file', confine(params.file, 'file'))
  if (params.start_line != null) argv.push('--start-line', String(Math.max(1, Number(params.start_line) || 1)))
  if (params.end_line != null) argv.push('--end-line', String(Math.max(1, Number(params.end_line) || 1)))
  if (params.query) argv.push(String(params.query))
  if (params.project && !spec.repoFlag) argv.push('--project', String(params.project))
  if (params.slug) argv.push('--slug', String(params.slug))
  if (params.limit != null) argv.push('--limit', String(Math.max(1, Number(params.limit) || 20)))

  return argv
}

function run(action, params) {
  const argv = buildArgv(action, params)
  // Key on the TARGET for mutating actions, not on the action.
  // `index` and `refresh` both write the same .terrain/ directory, so
  // they must not run concurrently even though they are different actions.
  // Read-only actions carry an empty key, which never collides with a
  // mutating one, so they stay concurrent.
  const mutating = ['index', 'refresh', 'init'].includes(action)
  const key = (mutating ? 'write|' : 'read|') + (params.path ?? '')

  return serialise(key, () => new Promise((res) => {
    const child = spawn(BIN, argv, {
      cwd: WORKSPACE,
      env: {
        ...process.env,
        // Terrain's registry must stay off the shared workspace mount.
        HOME: process.env.HOME ?? '/var/lib/terrain',
      },
      timeout: 10 * 60 * 1000,
    })
    let out = '', err = ''
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    child.on('error', (e) => res({ ok: false, action, error: String(e.message) }))
    child.on('close', (code) => {
      if (code === 0) res({ ok: true, action, output: out.trim() })
      else res({ ok: false, action, exit: code, error: (err || out).trim().slice(0, 4000) })
    })
  }))
}

// ── MCP tool listing ─────────────────────────────────────────────────────────
// Tool names keep the `pai_` namespace even though MCP fans one tool out
// per action — see docs/naming.md rule 1. The prefix identifies the
// capability owner, never the transport.
function mcpTools() {
  return Object.entries(ACTIONS).map(([name, v]) => ({
    name: `pai_terrain_${name}`,
    description:
      `Terrain ${name} — ${v.llm ? 'uses an LLM (keyless Zen models)' : 'offline, no LLM call'}. ` +
      `Params: ${Object.keys(SCHEMAS[name]).join(', ') || 'none'}. ` +
      `Writes .terrain/ into the indexed repo.`,
    inputSchema: {
      type: 'object',
      properties: Object.fromEntries(
        Object.entries(SCHEMAS[name]).map(([k, t]) => [k, { type: t.replace('?', '') }]),
      ),
    },
    annotations: { readOnlyHint: !['index', 'init', 'refresh', 'unregister'].includes(name) },
  }))
}

const MCP_TOOLS = new Map(mcpTools().map((t) => [t.name.replace('pai_terrain_', ''), t]))

// ── MCP JSON-RPC ─────────────────────────────────────────────────────────────
const PROTOCOL_VERSION = '2025-06-18'

async function handleRpc(msg, sessions) {
  const { id, method, params } = msg
  const reply = (result) => ({ jsonrpc: '2.0', id, result })
  const fail = (code, message) => ({ jsonrpc: '2.0', id, error: { code, message } })

  switch (method) {
    case 'initialize':
      return reply({
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: 'pai-terrain', version: '0.1.0' },
      })
    case 'notifications/initialized':
      return null
    case 'ping':
      return reply({})
    case 'tools/list':
      return reply({ tools: mcpTools() })
    case 'tools/call': {
      // Accept both the legacy and the namespaced spelling so an
      // existing client does not break on upgrade; the registry is
      // keyed by the bare action either way.
      const raw = String(params?.name ?? '')
      const action = raw.replace(/^pai_terrain_/, '').replace(/^terrain_/, '')
      if (!MCP_TOOLS.has(action)) return fail(-32602, `unknown tool: ${params?.name}`)
      const gated = guardLlm(action)
      if (gated) return fail(-32603, gated)
      const r = await run(action, params?.arguments ?? {})
      return reply({
        content: [{ type: 'text', text: r.ok ? r.output : `ERROR: ${r.error}` }],
        isError: !r.ok,
      })
    }
    default:
      return id == null ? null : fail(-32601, `method not found: ${method}`)
  }
}

// ── HTTP ─────────────────────────────────────────────────────────────────────
const sessions = new Set()

function json(res, status, body, extra = {}) {
  const s = JSON.stringify(body)
  res.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(s),
    ...extra,
  })
  res.end(s)
}

async function readBody(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const raw = Buffer.concat(chunks).toString('utf8')
  if (!raw.trim()) return null
  try {
    return JSON.parse(raw)
  } catch {
    throw Object.assign(new Error('invalid JSON body'), { status: 400 })
  }
}

function commitRef() {
  try { return readFileSync('/etc/terrain-commit', 'utf8').trim().slice(0, 12) } catch { return 'unknown' }
}

// ── LLM gate ─────────────────────────────────────────────────────────
// `ask` and `init` invoke an LLM. They are refused unless the operator
// has explicitly opted in. Applies to BOTH protocols — this is a
// permission, so it must hold no matter how the request arrives.
function guardLlm(action) {
  if (!LLM_ACTIONS.includes(action)) return null
  if (process.env.TERRAIN_ALLOW_LLM === '1') return null
  return `action "${action}" needs an LLM; set TERRAIN_ALLOW_LLM=1 to enable`
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`)

  try {
    if (url.pathname === '/healthz') {
      return json(res, 200, { ok: true, terrain: commitRef(), workspace: WORKSPACE, llmActions: LLM_ACTIONS })
    }
    if (TOKEN && req.headers['x-terrain-token'] !== TOKEN) {
      return json(res, 401, { error: 'bad or missing x-terrain-token' })
    }

    if (url.pathname === '/mcp' && req.method === 'POST') {
      const body = await readBody(req)
      if (!body) return res.writeHead(202).end()
      const batch = Array.isArray(body) ? body : [body]
      const out = []
      for (const msg of batch) {
        if (msg.method === 'initialize' && !req.headers['mcp-session-id']) {
          const sid = `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
          sessions.add(sid)
          var extraHeaders = { 'mcp-session-id': sid }
        }
        // Apply LLM gate check for MCP tool calls
        if (msg.method === 'tools/call') {
          const action = String(msg.params?.name ?? '').replace(/^pai_terrain_/, '').replace(/^terrain_/, '')
          const gated = guardLlm(action)
          if (gated) {
            out.push({ jsonrpc: '2.0', id: msg.id, error: { code: -32603, message: gated } })
            continue
          }
        }
        const r = await handleRpc(msg, sessions)
        if (r) out.push(r)
      }
      if (!out.length) return res.writeHead(202).end()
      return json(res, 200, out.length === 1 ? out[0] : out, extraHeaders ?? {})
    }

    if (url.pathname === '/call' && req.method === 'POST') {
      const body = await readBody(req)
      if (!body?.action) throw Object.assign(new Error('missing "action"'), { status: 400 })
      const gated = guardLlm(body.action)
      if (gated) throw Object.assign(new Error(gated), { status: 403 })
      return json(res, 200, await run(body.action, body.params ?? {}))
    }

    return json(res, 404, { error: 'not found', routes: ['/healthz', '/call', '/mcp'] })
  } catch (e) {
    return json(res, e.status ?? 500, { error: e.message })
  }
})

server.listen(PORT, '0.0.0.0', () => {
  console.error(`[terrain] listening on ${PORT} · workspace=${WORKSPACE} · ref=${commitRef()}`)
  seedAcpConfig()
  // Report the gate's ACTUAL state. This printed "gated OFF" unconditionally,
  // so a container running with TERRAIN_ALLOW_LLM=1 still announced the opposite
  // of the truth in its only startup banner. The per-request check below was
  // always correct — this was a lie in the logs, not a broken gate.
  if (process.env.TERRAIN_ALLOW_LLM === '1') {
    console.error(`[terrain] llm actions ENABLED: ${LLM_ACTIONS.join(', ')} (keyless Zen models via opencode acp — NOT llm-gateway)`)
  } else {
    console.error(`[terrain] llm actions gated OFF: ${LLM_ACTIONS.join(', ')} (TERRAIN_ALLOW_LLM=1 to enable)`)
  }
})