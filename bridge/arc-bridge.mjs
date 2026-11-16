import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import { dirname, extname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const env = process.env
const bridgeDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(bridgeDir, '..')
const defaultArcBin = resolve(repoRoot, 'external', 'Agent-Robotic-Cluster', 'bin', 'arc')

const HOST = env.ARC_BRIDGE_HOST || env.OPENCLAUDE_BRIDGE_HOST || '127.0.0.1'
const PORT = Number(env.ARC_BRIDGE_PORT || env.OPENCLAUDE_BRIDGE_PORT || 9797)
const ARC_BIN = env.ARC_BRIDGE_BIN || env.OPENCLAUDE_BRIDGE_BIN || defaultArcBin
const DEFAULT_MODEL =
  env.ARC_BRIDGE_MODEL ||
  env.OPENCLAUDE_BRIDGE_MODEL ||
  env.OPENAI_MODEL ||
  'local-llama'
// Reuse-kit default: openai → lab OpenAI-compatible gateway (not codexplan profile).
// Empty string would let ARC fall back to a saved codexplan profile and fail auth.
const DEFAULT_PROVIDER =
  env.ARC_BRIDGE_PROVIDER || env.OPENCLAUDE_BRIDGE_PROVIDER || 'openai'
// Local sim/web lab default: bypass tool approval prompts.
// The chat UI does not fully support interactive approval yet, so waiting on
// bridge.approval leaves the user stuck. Override with ARC_BRIDGE_PERMISSION_MODE=default
// when you want real approval prompts again.
const DEFAULT_PERMISSION_MODE =
  env.ARC_BRIDGE_PERMISSION_MODE ||
  env.OPENCLAUDE_BRIDGE_PERMISSION_MODE ||
  'bypassPermissions'
const DEFAULT_TOOLS =
  env.ARC_BRIDGE_TOOLS || env.OPENCLAUDE_BRIDGE_TOOLS || 'default'
const DEFAULT_ALLOWED_TOOLS =
  env.ARC_BRIDGE_ALLOWED_TOOLS || env.OPENCLAUDE_BRIDGE_ALLOWED_TOOLS || ''
const DEFAULT_SYSTEM_PROMPT =
  env.ARC_BRIDGE_SYSTEM_PROMPT || env.OPENCLAUDE_BRIDGE_SYSTEM_PROMPT || ''
const DEFAULT_WORKSPACE =
  env.ARC_BRIDGE_WORKSPACE || env.ARC_ROBOT_WORKSPACE || repoRoot
const DEFAULT_TIMEOUT_MS = Number(
  env.ARC_BRIDGE_TIMEOUT_MS || env.OPENCLAUDE_BRIDGE_TIMEOUT_MS || 180000,
)
const DEFAULT_BARE =
  env.ARC_BRIDGE_BARE === '1' || env.OPENCLAUDE_BRIDGE_BARE === '1'
const DIRECT_BIN =
  env.ARC_BRIDGE_DIRECT_BIN === '1' || env.OPENCLAUDE_BRIDGE_DIRECT_BIN === '1'

// Lab OpenAI-compatible provider (reuse kit). OPENAI_BASE_URL must end with /v1.
const DEFAULT_OPENAI_BASE_URL = (
  env.OPENAI_BASE_URL ||
  env.ARC_SIM_LLM_BASE_URL ||
  'http://127.0.0.1:4000/v1'
).replace(/\/+$/, '')
const OPENAI_BASE_URL = DEFAULT_OPENAI_BASE_URL.endsWith('/v1')
  ? DEFAULT_OPENAI_BASE_URL
  : `${DEFAULT_OPENAI_BASE_URL}/v1`
const OPENAI_API_KEY =
  env.OPENAI_API_KEY ||
  env.ARC_SIM_LLM_API_KEY ||
  'local-dev-key'
const OPENAI_MODEL = env.OPENAI_MODEL || DEFAULT_MODEL
const OPENAI_API_FORMAT = env.OPENAI_API_FORMAT || 'chat_completions'

const BYPASS_PERMISSIONS =
  DEFAULT_PERMISSION_MODE === 'bypassPermissions' ||
  DEFAULT_PERMISSION_MODE === 'bypass-permissions' ||
  env.ARC_BRIDGE_DANGEROUSLY_SKIP_PERMISSIONS === '1' ||
  env.OPENCLAUDE_BRIDGE_DANGEROUSLY_SKIP_PERMISSIONS === '1'
// Safety net: if ARC still emits can_use_tool prompts, auto-allow them so the
// unfinished web approval UX cannot stall a sim/lab turn.
const AUTO_APPROVE_TOOLS =
  env.ARC_BRIDGE_AUTO_APPROVE === '1' ||
  env.OPENCLAUDE_BRIDGE_AUTO_APPROVE === '1' ||
  (env.ARC_BRIDGE_AUTO_APPROVE !== '0' &&
    env.OPENCLAUDE_BRIDGE_AUTO_APPROVE !== '0' &&
    BYPASS_PERMISSIONS)

const jsonHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

const uiSessionToArcSession = new Map()
const pendingApprovals = new Map()

const readJsonBody = async request => {
  const chunks = []

  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }

  if (chunks.length === 0) {
    return {}
  }

  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

const sendJson = (response, statusCode, payload) => {
  response.writeHead(statusCode, {
    ...jsonHeaders,
    'Content-Type': 'application/json; charset=utf-8',
  })
  response.end(JSON.stringify(payload))
}

const asStringContent = content => {
  if (typeof content === 'string') {
    return content
  }

  if (!Array.isArray(content)) {
    return ''
  }

  return content
    .map(part => {
      if (typeof part === 'string') {
        return part
      }
      if (part?.type === 'text' && typeof part.text === 'string') {
        return part.text
      }
      if (part?.type === 'input_text' && typeof part.text === 'string') {
        return part.text
      }
      if (typeof part?.content === 'string') {
        return part.content
      }
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

const normalizeMessages = messages =>
  Array.isArray(messages)
    ? messages
        .filter(message => {
          const role = message?.role
          return role === 'system' || role === 'user' || role === 'assistant'
        })
        .map(message => ({
          role: message.role,
          content: asStringContent(message.content).trim(),
        }))
        .filter(message => message.content)
    : []

const buildPromptParts = (messages, { resumeSession = false } = {}) => {
  const normalized = normalizeMessages(messages)
  const systemMessages = normalized
    .filter(message => message.role === 'system')
    .map(message => message.content)

  const transcriptSource = resumeSession
    ? normalized
        .filter(message => message.role === 'user' || message.role === 'assistant')
        .slice(-2)
    : normalized.filter(message => message.role === 'user' || message.role === 'assistant')

  const transcript = transcriptSource
    .map(message => `${message.role === 'assistant' ? 'Assistant' : 'User'}: ${message.content}`)
    .join('\n\n')

  const systemPrompt = [
    'You are answering inside an ARC web chat bridge backed by the ARC agentic CLI.',
    'ARC can use robot control tools that call the local Python robot runtime. Prefer status, scan, plan, and preflight checks before any physical motion.',
    'Do not move hardware unless the user clearly asks for robot motion and the available guardrails allow it.',
    'Keep answers concise and surface important robot safety or execution status plainly.',
    DEFAULT_SYSTEM_PROMPT,
    systemMessages.length > 0
      ? `User-provided system instructions:\n${systemMessages.join('\n\n')}`
      : '',
  ]
    .filter(Boolean)
    .join('\n\n')

  return {
    systemPrompt,
    prompt: transcript || 'User: Hello',
  }
}

const createCompletionChunk = ({ id, model, text, finishReason }) => ({
  id,
  object: 'chat.completion.chunk',
  created: Math.floor(Date.now() / 1000),
  model,
  choices: [
    {
      index: 0,
      delta: text ? { content: text } : {},
      finish_reason: finishReason ?? null,
    },
  ],
})

const createCompletionResponse = ({
  id,
  model,
  text,
  toolCalls,
  sessionId,
  arcSessionId,
  resultMessage,
}) => ({
  id,
  object: 'chat.completion',
  created: Math.floor(Date.now() / 1000),
  model,
  choices: [
    {
      index: 0,
      message: {
        role: 'assistant',
        content: text,
      },
      finish_reason: 'stop',
    },
  ],
  usage: {
    prompt_tokens: resultMessage?.usage?.input_tokens ?? 0,
    completion_tokens: resultMessage?.usage?.output_tokens ?? 0,
    total_tokens:
      (resultMessage?.usage?.input_tokens ?? 0) +
      (resultMessage?.usage?.output_tokens ?? 0),
  },
  session_id: sessionId,
  arc_session_id: arcSessionId,
  bridge_tool_calls: toolCalls,
})

const splitToolList = value =>
  typeof value === 'string'
    ? value
        .split(/[,\s]+/)
        .map(item => item.trim())
        .filter(Boolean)
    : []

const createLauncher = arcBin => {
  if (DIRECT_BIN) {
    return { command: arcBin, prefixArgs: [] }
  }

  const extension = extname(arcBin).toLowerCase()
  const shouldUseNode =
    !extension || extension === '.js' || extension === '.mjs' || extension === '.cjs'

  return shouldUseNode
    ? { command: process.execPath, prefixArgs: [arcBin] }
    : { command: arcBin, prefixArgs: [] }
}

const createSpawnArgs = ({
  prompt,
  model,
  provider,
  sessionId,
  systemPrompt,
  resumeSession = false,
}) => {
  const args = [
    '-p',
    '--verbose',
    '--output-format',
    'stream-json',
    '--input-format',
    'stream-json',
    '--include-partial-messages',
    '--permission-prompt-tool',
    'stdio',
    '--permission-mode',
    DEFAULT_PERMISSION_MODE,
    '--tools',
    DEFAULT_TOOLS,
    '--append-system-prompt',
    systemPrompt,
  ]

  // ARC requires an explicit allow flag before bypassPermissions takes effect.
  if (BYPASS_PERMISSIONS) {
    args.push('--allow-dangerously-skip-permissions')
    args.push('--dangerously-skip-permissions')
  }

  if (DEFAULT_BARE) {
    args.push('--bare')
  }

  if (model) {
    args.push('--model', model)
  }

  if (provider) {
    args.push('--provider', provider)
  }

  const allowedTools = splitToolList(DEFAULT_ALLOWED_TOOLS)
  if (allowedTools.length > 0) {
    args.push('--allowed-tools', ...allowedTools)
  }

  if (resumeSession && sessionId) {
    args.push('--resume', sessionId)
  } else if (sessionId) {
    args.push('--session-id', sessionId)
  }

  return args
}

const createUserInputMessage = prompt => ({
  type: 'user',
  session_id: '',
  message: {
    role: 'user',
    content: prompt,
  },
  parent_tool_use_id: null,
})

const extractAssistantText = event => {
  if (!event || event.type !== 'assistant' || event.message?.role !== 'assistant') {
    return ''
  }

  const content = event.message?.content
  if (typeof content === 'string') {
    return content
  }

  if (!Array.isArray(content)) {
    return ''
  }

  return content
    .filter(entry => entry?.type === 'text' && typeof entry.text === 'string')
    .map(entry => entry.text)
    .join('')
}

const extractStreamEventTextDelta = event => {
  if (!event || event.type !== 'stream_event') {
    return ''
  }

  if (event.event?.type !== 'content_block_delta') {
    return ''
  }

  if (
    event.event.delta?.type !== 'text_delta' ||
    typeof event.event.delta?.text !== 'string'
  ) {
    return ''
  }

  return event.event.delta.text
}

const normalizeTextForSnapshotCompare = text =>
  text
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()

const createCharacterGrams = (text, size = 3) => {
  const normalized = normalizeTextForSnapshotCompare(text)
  if (normalized.length <= size) {
    return normalized ? [normalized] : []
  }

  const grams = []
  for (let index = 0; index <= normalized.length - size; index += 1) {
    grams.push(normalized.slice(index, index + size))
  }
  return grams
}

const hasHighTextOverlap = (previousText, nextText) => {
  const previous = normalizeTextForSnapshotCompare(previousText)
  const next = normalizeTextForSnapshotCompare(nextText)

  if (!previous || !next) {
    return false
  }

  if (previous === next || previous.includes(next) || next.includes(previous)) {
    return true
  }

  if (Math.min(previous.length, next.length) < 40) {
    return false
  }

  const previousGrams = createCharacterGrams(previous)
  const nextGrams = new Set(createCharacterGrams(next))
  const overlap = previousGrams.filter(gram => nextGrams.has(gram)).length

  return overlap / Math.min(previousGrams.length, nextGrams.size) >= 0.78
}

const mergeAssistantText = (previousText, event) => {
  const streamDelta = extractStreamEventTextDelta(event)
  if (streamDelta) {
    if (!previousText) {
      return streamDelta
    }
    if (streamDelta === previousText) {
      return previousText
    }
    if (streamDelta.startsWith(previousText)) {
      return streamDelta
    }
    if (previousText.startsWith(streamDelta) || previousText.endsWith(streamDelta)) {
      return previousText
    }
    if (hasHighTextOverlap(previousText, streamDelta)) {
      return streamDelta
    }
    return previousText + streamDelta
  }

  const assistantText = extractAssistantText(event)
  if (assistantText) {
    return assistantText
  }

  return previousText
}

const createAssistantTextUpdate = (previousText, event) => {
  const nextText = mergeAssistantText(previousText, event)
  if (!nextText || nextText === previousText) {
    return { text: previousText, delta: '', replaceText: '' }
  }

  if (!nextText.startsWith(previousText) && hasHighTextOverlap(previousText, nextText)) {
    return { text: nextText, delta: '', replaceText: nextText }
  }

  return {
    text: nextText,
    delta: nextText.startsWith(previousText) ? nextText.slice(previousText.length) : nextText,
    replaceText: '',
  }
}

const extractToolUse = event => {
  if (!event || event.type !== 'assistant' || event.message?.role !== 'assistant') {
    return []
  }

  const content = Array.isArray(event.message?.content) ? event.message.content : []
  return content
    .filter(entry => entry?.type === 'tool_use' && typeof entry.name === 'string')
    .map(entry => ({
      id: entry.id || randomUUID(),
      name: entry.name,
      input: entry.input || {},
    }))
}

const extractToolResult = event => {
  if (!event?.message || !Array.isArray(event.message.content)) {
    return []
  }

  return event.message.content
    .filter(entry => entry?.type === 'tool_result')
    .map(entry => ({
      id: entry.tool_use_id || randomUUID(),
      tool_use_id: entry.tool_use_id || null,
      content: entry.content ?? '',
      is_error: entry.is_error === true,
    }))
}

const extractApprovalRequest = event => {
  if (
    !event ||
    event.type !== 'control_request' ||
    typeof event.request_id !== 'string' ||
    event.request?.subtype !== 'can_use_tool'
  ) {
    return null
  }

  const request = event.request
  const input =
    request.input && typeof request.input === 'object' && !Array.isArray(request.input)
      ? request.input
      : {}
  const toolName = typeof request.tool_name === 'string' ? request.tool_name : 'tool'
  const toolUseId =
    typeof request.tool_use_id === 'string' && request.tool_use_id
      ? request.tool_use_id
      : event.request_id
  const approvalMessage =
    (typeof request.decision_reason === 'string' && request.decision_reason.trim()) ||
    (typeof request.description === 'string' && request.description.trim()) ||
    (typeof request.title === 'string' && request.title.trim()) ||
    `Allow ${toolName}?`

  return {
    id: event.request_id,
    request_id: event.request_id,
    tool_use_id: toolUseId,
    tool_name: toolName,
    name: toolName,
    input,
    message: approvalMessage,
    title: typeof request.title === 'string' ? request.title : undefined,
    display_name:
      typeof request.display_name === 'string' ? request.display_name : undefined,
  }
}

const createApprovalControlResponse = ({ approval, approved, updatedInput }) => ({
  type: 'control_response',
  response: {
    subtype: 'success',
    request_id: approval.request_id || approval.id,
    response: approved
      ? {
          behavior: 'allow',
          updatedInput:
            updatedInput && typeof updatedInput === 'object' && !Array.isArray(updatedInput)
              ? updatedInput
              : approval.input &&
                  typeof approval.input === 'object' &&
                  !Array.isArray(approval.input)
                ? approval.input
              : {},
          toolUseID: approval.tool_use_id,
          decisionClassification: 'user_temporary',
        }
      : {
          behavior: 'deny',
          message: 'User rejected tool execution.',
          toolUseID: approval.tool_use_id,
          decisionClassification: 'user_reject',
        },
  },
})

const bindLines = (stream, handler) => {
  let buffer = ''

  stream.on('data', chunk => {
    buffer += chunk.toString('utf8')
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed) {
        handler(trimmed)
      }
    }
  })

  stream.on('end', () => {
    const trimmed = buffer.trim()
    if (trimmed) {
      handler(trimmed)
    }
  })
}

const resolvePythonForBridge = (cwd) => {
  // ARC robot tools historically spawn('python') which ENOENTs on Linux/WSL
  // when only python3 / project .venv exist. Prefer explicit override, then venv.
  if (env.ARC_PYTHON && env.ARC_PYTHON.trim()) {
    return env.ARC_PYTHON.trim()
  }
  const roots = [cwd || DEFAULT_WORKSPACE, repoRoot].filter(Boolean)
  for (const root of roots) {
    for (const rel of [
      '.venv/bin/python',
      '.venv/bin/python3',
      '.venv/Scripts/python.exe',
      'venv/bin/python',
    ]) {
      const candidate = resolve(root, rel)
      if (existsSync(candidate)) {
        return candidate
      }
    }
  }
  return env.PYTHON || 'python3'
}

const buildArcChildEnv = (cwd) => {
  // Force OpenAI-compatible lab gateway so ARC does not stick on saved
  // codexplan / MaxPlus anthropic profile when provider=openai.
  const workspace = cwd || DEFAULT_WORKSPACE
  const pythonBin = resolvePythonForBridge(workspace)
  const pythonShimDir = resolve(repoRoot, '.tools', 'python-shim')
  const pathParts = []
  if (existsSync(pythonShimDir)) {
    pathParts.push(pythonShimDir)
  }
  // If pythonBin is an absolute path, put its directory early so `python` name works.
  if (pythonBin.includes('/') || pythonBin.includes('\\')) {
    pathParts.push(dirname(pythonBin))
  }
  const existingPath = env.PATH || env.Path || ''
  const pathPrefix = pathParts.join(':')

  const childEnv = {
    ...env,
    ARC_ROBOT_WORKSPACE: workspace,
    ARC_SIM_MODE: env.ARC_SIM_MODE || '1',
    // Default both sim arms when caller did not pin a subset.
    ARC_ACTIVE_ROBOT_IDS: env.ARC_ACTIVE_ROBOT_IDS || 'all',
    ARC_PYTHON: pythonBin,
    PYTHON: pythonBin,
    // Put shim first so bare `python` resolves even if ARC dist still hardcodes it.
    PATH: pathPrefix ? `${pathPrefix}:${existingPath}` : existingPath,
    CLAUDE_CODE_USE_OPENAI: env.CLAUDE_CODE_USE_OPENAI || '1',
    OPENAI_BASE_URL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_API_FORMAT,
  }

  // Avoid mixed-provider confusion: when using openai path, do not leave a
  // MaxPlus anthropic base URL as the primary auth target for this child.
  if ((DEFAULT_PROVIDER || 'openai') === 'openai') {
    childEnv.CLAUDE_CODE_USE_OPENAI = '1'
    // Keep ANTHROPIC_* only if caller explicitly wants dual config; otherwise
    // clear base URL so saved anthropic settings do not win over OPENAI_*.
    if (!env.ARC_BRIDGE_KEEP_ANTHROPIC) {
      delete childEnv.ANTHROPIC_BASE_URL
      delete childEnv.ANTHROPIC_API_KEY
    }
  }

  return childEnv
}

const runArc = ({
  prompt,
  model,
  provider,
  sessionId,
  cwd,
  systemPrompt,
  resumeSession,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  onJsonLine,
  onErrorLine,
  onClose,
}) => {
  const childEnv = buildArcChildEnv(cwd)

  const launcher = createLauncher(ARC_BIN)
  const child = spawn(
    launcher.command,
    [
      ...launcher.prefixArgs,
      ...createSpawnArgs({
        prompt,
        model,
        provider: provider || DEFAULT_PROVIDER,
        sessionId,
        systemPrompt,
        resumeSession,
      }),
    ],
    {
      cwd: cwd || DEFAULT_WORKSPACE,
      env: childEnv,
      stdio: ['pipe', 'pipe', 'pipe'],
    },
  )
  const runId = randomUUID()
  const approvalIds = new Set()

  const cleanupApprovals = () => {
    for (const approvalId of approvalIds) {
      const pending = pendingApprovals.get(approvalId)
      if (pending?.runId === runId) {
        pendingApprovals.delete(approvalId)
      }
    }
    approvalIds.clear()
  }

  const writeControlMessage = message => {
    if (!child.stdin || child.stdin.destroyed || child.stdin.writableEnded) {
      return false
    }

    try {
      child.stdin.write(`${JSON.stringify(message)}\n`)
      return true
    } catch {
      return false
    }
  }

  writeControlMessage(createUserInputMessage(prompt))

  const timeout = setTimeout(() => {
    onErrorLine(`arc bridge timed out after ${timeoutMs}ms`)
    cleanupApprovals()
    child.kill('SIGTERM')
  }, timeoutMs)

  bindLines(child.stdout, line => {
    try {
      const event = JSON.parse(line)
      if (event?.type === 'result' && child.stdin && !child.stdin.writableEnded) {
        child.stdin.end()
      }
      const approval = extractApprovalRequest(event)
      if (approval) {
        approvalIds.add(approval.id)
        const pending = {
          runId,
          approval,
          respond(approved, options = {}) {
            const sent = writeControlMessage(
              createApprovalControlResponse({
                approval,
                approved,
                updatedInput: options.updatedInput,
              }),
            )
            if (sent) {
              pendingApprovals.delete(approval.id)
              approvalIds.delete(approval.id)
            }
            return sent
          },
        }
        pendingApprovals.set(approval.id, pending)

        // Auto-allow tool use when the lab/sim bridge is configured to bypass
        // interactive approval. This keeps turns moving even if ARC still emits
        // a can_use_tool control_request.
        if (AUTO_APPROVE_TOOLS) {
          pending.respond(true)
        }
      }
      onJsonLine(event)
    } catch {
      onErrorLine(line)
    }
  })

  bindLines(child.stderr, line => {
    onErrorLine(line)
  })

  child.on('error', error => {
    onErrorLine(error.message)
  })

  child.on('close', code => {
    clearTimeout(timeout)
    cleanupApprovals()
    onClose(code ?? 1)
  })

  return {
    cancel() {
      clearTimeout(timeout)
      cleanupApprovals()
      child.kill('SIGTERM')
    },
  }
}

const sendSseChunk = (response, payload) => {
  response.write(`data: ${JSON.stringify(payload)}\n\n`)
}

const sendSseEvent = (response, eventName, payload) => {
  response.write(`event: ${eventName}\n`)
  response.write(`data: ${JSON.stringify(payload)}\n\n`)
}

const sendSseDone = response => {
  response.write('data: [DONE]\n\n')
  response.end()
}

const createRuntimeError = (stderrLines, resultMessage) => {
  const resultText =
    typeof resultMessage?.result === 'string' && resultMessage.result.trim()
      ? resultMessage.result.trim()
      : ''
  const stderrText = stderrLines.join('\n').trim()
  return resultText || stderrText || 'ARC exited before returning a response.'
}

// Resume failed → retry as brand-new session (reuse kit).
const shouldRetryAsCreate = runtimeError =>
  /--resume requires a valid session ID|Failed to resume session|No conversation found|not found/i.test(
    runtimeError,
  )

// Session UUID already exists on disk → mint a new id (reuse kit "already in use").
const shouldRemintSession = runtimeError =>
  /already in use|session id .* in use|Session ID .* is already in use/i.test(
    runtimeError,
  )

const resolveSession = requestedSessionId => {
  const uiSessionId =
    typeof requestedSessionId === 'string' && requestedSessionId.trim()
      ? requestedSessionId.trim()
      : randomUUID()

  if (UUID_PATTERN.test(uiSessionId)) {
    uiSessionToArcSession.set(uiSessionId, uiSessionId)
    return { sessionId: uiSessionId, arcSessionId: uiSessionId }
  }

  const existingArcSessionId = uiSessionToArcSession.get(uiSessionId)
  if (existingArcSessionId) {
    return { sessionId: uiSessionId, arcSessionId: existingArcSessionId }
  }

  const arcSessionId = randomUUID()
  uiSessionToArcSession.set(uiSessionId, arcSessionId)
  return { sessionId: uiSessionId, arcSessionId }
}

/** Force a new ARC UUID for this UI session (session conflict / sticky bad id). */
const remintArcSession = uiSessionId => {
  const arcSessionId = randomUUID()
  if (uiSessionId) {
    uiSessionToArcSession.set(uiSessionId, arcSessionId)
  }
  return arcSessionId
}

const isResultError = resultMessage =>
  resultMessage?.type === 'result' &&
  (resultMessage.is_error === true ||
    (typeof resultMessage.subtype === 'string' &&
      resultMessage.subtype.startsWith('error')))

const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`)

  if (request.method === 'OPTIONS') {
    response.writeHead(204, jsonHeaders)
    response.end()
    return
  }

  if (request.method === 'GET' && url.pathname === '/health') {
    const launcher = createLauncher(ARC_BIN)
    sendJson(response, 200, {
      ok: true,
      runtime: 'arc-cli',
      binary: ARC_BIN,
      launcher: launcher.command,
      model: DEFAULT_MODEL,
      provider: DEFAULT_PROVIDER || null,
      openai_base_url: OPENAI_BASE_URL,
      workspace: DEFAULT_WORKSPACE,
      permission_mode: DEFAULT_PERMISSION_MODE,
      auto_approve_tools: AUTO_APPROVE_TOOLS,
      bypass_permissions: BYPASS_PERMISSIONS,
      tools: DEFAULT_TOOLS,
    })
    return
  }

  // Client can force a fresh ARC UUID mapping (reuse kit session reset).
  if (request.method === 'POST' && url.pathname === '/v1/session/reset') {
    let body = {}
    try {
      body = await readJsonBody(request)
    } catch {
      body = {}
    }
    const uiSessionId =
      typeof body?.session_id === 'string' && body.session_id.trim()
        ? body.session_id.trim()
        : randomUUID()
    const arcSessionId = remintArcSession(uiSessionId)
    sendJson(response, 200, {
      ok: true,
      session_id: uiSessionId,
      arc_session_id: arcSessionId,
      initialized: false,
    })
    return
  }

  if (request.method === 'GET' && url.pathname === '/v1/models') {
    sendJson(response, 200, {
      object: 'list',
      data: [
        {
          id: DEFAULT_MODEL,
          object: 'model',
          created: 0,
          owned_by: 'arc',
        },
      ],
    })
    return
  }

  const approvalMatch = url.pathname.match(/^\/v1\/bridge\/approvals\/([^/]+)$/)
  if (
    request.method === 'POST' &&
    (approvalMatch || url.pathname === '/v1/bridge/approvals')
  ) {
    let body

    try {
      body = await readJsonBody(request)
    } catch (error) {
      sendJson(response, 400, {
        error: {
          message: error instanceof Error ? error.message : 'Invalid JSON body',
        },
      })
      return
    }

    const approvalId =
      approvalMatch?.[1] && decodeURIComponent(approvalMatch[1])
        ? decodeURIComponent(approvalMatch[1])
        : typeof body?.id === 'string'
          ? body.id
          : ''

    if (!approvalId || typeof body?.approved !== 'boolean') {
      sendJson(response, 400, {
        error: {
          message: 'approval id and boolean approved are required',
        },
      })
      return
    }

    const pending = pendingApprovals.get(approvalId)
    if (!pending) {
      sendJson(response, 404, {
        error: {
          message: 'Approval request is no longer pending',
        },
      })
      return
    }

    const updatedInput =
      body?.updatedInput &&
      typeof body.updatedInput === 'object' &&
      !Array.isArray(body.updatedInput)
        ? body.updatedInput
        : undefined

    if (!pending.respond(body.approved, { updatedInput })) {
      pendingApprovals.delete(approvalId)
      sendJson(response, 410, {
        error: {
          message: 'ARC process is no longer accepting approval responses',
        },
      })
      return
    }

    sendJson(response, 200, {
      ok: true,
      id: approvalId,
      approved: body.approved,
    })
    return
  }

  if (request.method === 'POST' && url.pathname === '/v1/chat/completions') {
    let body

    try {
      body = await readJsonBody(request)
    } catch (error) {
      sendJson(response, 400, {
        error: {
          message: error instanceof Error ? error.message : 'Invalid JSON body',
        },
      })
      return
    }

    const messages = Array.isArray(body?.messages) ? body.messages : []
    if (messages.length === 0) {
      sendJson(response, 400, {
        error: {
          message: 'messages is required',
        },
      })
      return
    }

    const model = typeof body?.model === 'string' && body.model.trim()
      ? body.model.trim()
      : DEFAULT_MODEL
    const provider = typeof body?.provider === 'string' && body.provider.trim()
      ? body.provider.trim()
      : DEFAULT_PROVIDER
    const cwd = typeof body?.cwd === 'string' && body.cwd.trim()
      ? body.cwd.trim()
      : typeof body?.metadata?.cwd === 'string' && body.metadata.cwd.trim()
        ? body.metadata.cwd.trim()
        : DEFAULT_WORKSPACE
    const timeoutMs = Number(body?.timeout_ms || DEFAULT_TIMEOUT_MS)
    const stream = body?.stream === true
    const requestedSessionId =
      typeof body?.session_id === 'string'
        ? body.session_id
        : typeof body?.metadata?.session_id === 'string'
          ? body.metadata.session_id
          : ''
    const resolved = resolveSession(requestedSessionId)
    let sessionId = resolved.sessionId
    let arcSessionId = resolved.arcSessionId
    const resumeSession = body?.resume_session === true
    const { prompt, systemPrompt } = buildPromptParts(messages, { resumeSession })
    const completionId = `chatcmpl-${randomUUID()}`

    let assistantText = ''
    const toolCalls = []
    const stderrLines = []
    let resultMessage = null
    let streamEnded = false
    let activeRun = null
    let remintAttempts = 0
    const MAX_REMINT_ATTEMPTS = 1

    const cancelRun = () => {
      if (streamEnded) {
        return
      }
      activeRun?.cancel()
    }

    request.on('aborted', cancelRun)
    response.on('close', cancelRun)

    if (stream) {
      response.writeHead(200, {
        ...jsonHeaders,
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
      })
      sendSseEvent(response, 'bridge.session', {
        session_id: sessionId,
        arc_session_id: arcSessionId,
        resume_session: resumeSession,
        provider: provider || DEFAULT_PROVIDER,
        model,
        openai_base_url: OPENAI_BASE_URL,
      })
    }

    const executeAttempt = nextResumeSession => {
      let attemptAssistantText = ''
      const attemptToolCalls = []
      const attemptStderrLines = []
      const seenToolResultIds = new Set()
      const seenToolUseIds = new Set()
      let attemptResultMessage = null
      const attemptArcSessionId = arcSessionId

      activeRun = runArc({
        prompt,
        model,
        provider: provider || DEFAULT_PROVIDER,
        sessionId: attemptArcSessionId,
        cwd,
        systemPrompt,
        resumeSession: nextResumeSession,
        timeoutMs,
        onJsonLine: event => {
          if (event?.type === 'result') {
            attemptResultMessage = event
          }

          const approvalRequest = extractApprovalRequest(event)

          const nextToolUses = extractToolUse(event).filter(toolUse => {
            const key = toolUse.id || `${toolUse.name}:${JSON.stringify(toolUse.input ?? {})}`
            if (seenToolUseIds.has(key)) {
              return false
            }
            seenToolUseIds.add(key)
            return true
          })
          const nextToolResults = extractToolResult(event).filter(toolResult => {
            const key = toolResult.tool_use_id || toolResult.id
            if (seenToolResultIds.has(key)) {
              return false
            }
            seenToolResultIds.add(key)
            return true
          })
          attemptToolCalls.push(...nextToolUses)

          if (stream) {
            for (const toolUse of nextToolUses) {
              sendSseEvent(response, 'bridge.tool', toolUse)
            }

            // When auto-approve is on, the control_response is already written
            // inside runArc. Do not surface an interactive approval card to the UI.
            if (approvalRequest && !AUTO_APPROVE_TOOLS) {
              sendSseEvent(response, 'bridge.approval', {
                id: approvalRequest.tool_use_id,
                name: approvalRequest.tool_name,
                input: approvalRequest.input,
                approval: {
                  id: approvalRequest.id,
                },
                approvalMessage: approvalRequest.message,
                request_id: approvalRequest.request_id,
                tool_use_id: approvalRequest.tool_use_id,
              })
            }

            for (const toolResult of nextToolResults) {
              sendSseEvent(response, 'bridge.tool_result', toolResult)
            }
          }

          const textUpdate = createAssistantTextUpdate(attemptAssistantText, event)
          if (!textUpdate.text || textUpdate.text === attemptAssistantText) {
            return
          }

          attemptAssistantText = textUpdate.text

          if (stream && textUpdate.replaceText) {
            sendSseEvent(response, 'bridge.message', {
              content: textUpdate.replaceText,
            })
          } else if (stream && textUpdate.delta) {
            sendSseChunk(
              response,
              createCompletionChunk({
                id: completionId,
                model,
                text: textUpdate.delta,
              }),
            )
          }
        },
        onErrorLine: line => {
          attemptStderrLines.push(line)
        },
        onClose: code => {
          const runtimeError =
            code === 0 && !isResultError(attemptResultMessage)
              ? ''
              : createRuntimeError(attemptStderrLines, attemptResultMessage)

          // Resume path broken → retry as create (same UUID).
          if (
            code !== 0 &&
            !attemptAssistantText &&
            nextResumeSession &&
            !streamEnded &&
            shouldRetryAsCreate(runtimeError)
          ) {
            executeAttempt(false)
            return
          }

          // UUID already on disk / in use → mint new ARC session once, create.
          if (
            code !== 0 &&
            !attemptAssistantText &&
            !streamEnded &&
            remintAttempts < MAX_REMINT_ATTEMPTS &&
            shouldRemintSession(runtimeError)
          ) {
            remintAttempts += 1
            arcSessionId = remintArcSession(sessionId)
            if (stream) {
              sendSseEvent(response, 'bridge.session', {
                session_id: sessionId,
                arc_session_id: arcSessionId,
                resume_session: false,
                reminted: true,
                reason: 'session_already_in_use',
              })
            }
            executeAttempt(false)
            return
          }

          if (streamEnded) {
            return
          }

          streamEnded = true
          assistantText = attemptAssistantText
          toolCalls.push(...attemptToolCalls)
          stderrLines.push(...attemptStderrLines)
          resultMessage = attemptResultMessage

          const failed = code !== 0 || isResultError(resultMessage)
          const finalError = failed ? createRuntimeError(stderrLines, resultMessage) : ''

          if (stream) {
            if (!assistantText && finalError) {
              sendSseChunk(
                response,
                createCompletionChunk({
                  id: completionId,
                  model,
                  text: `ARC bridge error: ${finalError}`,
                }),
              )
            }

            sendSseChunk(
              response,
              createCompletionChunk({
                id: completionId,
                model,
                finishReason: failed ? 'error' : 'stop',
              }),
            )
            sendSseDone(response)
            return
          }

          if (failed) {
            sendJson(response, 502, {
              error: {
                message: finalError,
                type: 'arc_runtime_error',
                session_id: sessionId,
                arc_session_id: arcSessionId,
                tool_calls: toolCalls,
              },
            })
            return
          }

          sendJson(
            response,
            200,
            createCompletionResponse({
              id: completionId,
              model,
              text: assistantText || 'ARC returned an empty response.',
              toolCalls,
              sessionId,
              arcSessionId,
              resultMessage,
            }),
          )
        },
      })
    }

    executeAttempt(resumeSession)
    return
  }

  sendJson(response, 404, {
    error: {
      message: 'Not found',
    },
  })
})

export const startBridgeServer = ({ host = HOST, port = PORT } = {}) =>
  new Promise(resolveStart => {
    server.listen(port, host, () => {
      resolveStart(server)
    })
  })

export const __bridgeTestUtils = {
  asStringContent,
  buildPromptParts,
  createApprovalControlResponse,
  createSpawnArgs,
  createUserInputMessage,
  extractAssistantText,
  extractApprovalRequest,
  shouldRetryAsCreate,
  shouldRemintSession,
  remintArcSession,
  extractStreamEventTextDelta,
  extractToolResult,
  extractToolUse,
  createAssistantTextUpdate,
  hasHighTextOverlap,
  mergeAssistantText,
  resolveSession,
  splitToolList,
  BYPASS_PERMISSIONS,
  AUTO_APPROVE_TOOLS,
  DEFAULT_PERMISSION_MODE,
}

const isDirectExecution =
  process.argv[1] &&
  pathToFileURL(resolve(process.argv[1])).href === import.meta.url

if (isDirectExecution) {
  server.listen(PORT, HOST, () => {
    console.log(`ARC bridge listening on http://${HOST}:${PORT}`)
  })
}
