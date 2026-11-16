import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { dirname, join, resolve } from 'path'
import { fileURLToPath } from 'url'
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { lazySchema } from '../../utils/lazySchema.js'
import { jsonStringify } from '../../utils/slowOperations.js'
import { resolvePythonExecutable } from '../RobotControl/shared.js'
import { DESCRIPTION, ROBOT_ARM_TOOL_NAME } from './prompt.js'

const READ_ONLY_ACTIONS = ['list', 'status', 'state', 'pose'] as const
const MUTATING_ACTIONS = [
  'home',
  'move',
  'square',
  'stop',
  'recover',
  'recover_home',
] as const
const ALL_ACTIONS = [...READ_ONLY_ACTIONS, ...MUTATING_ACTIONS] as const
const MOVE_DIRECTIONS = [
  'UP',
  'DOWN',
  'LEFT',
  'RIGHT',
  'FORWARD',
  'BACK',
  'HOME',
] as const

type RobotAction = (typeof ALL_ACTIONS)[number]

const inputSchema = lazySchema(() =>
  z.strictObject({
    action: z
      .enum(ALL_ACTIONS)
      .describe('Robot action to perform or inspect'),
    arm_id: z
      .string()
      .optional()
      .describe('Target arm identifier such as Arm1 or Arm2'),
    direction: z
      .enum(MOVE_DIRECTIONS)
      .optional()
      .describe('Direction for move action'),
    step: z
      .number()
      .positive()
      .optional()
      .describe('Step size in mm for move or square actions'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>
type Input = z.infer<InputSchema>

const outputSchema = lazySchema(() =>
  z.object({
    workspace_root: z
      .string()
      .describe('Workspace directory containing arc.py and IMPLEMENTATION_STATUS.md'),
    command: z.string().describe('Executed Python command'),
    action: z.enum(ALL_ACTIONS).describe('Robot action that was executed'),
    arm_id: z.string().optional().describe('Target arm when applicable'),
    success: z.boolean().describe('Whether the command exited successfully'),
    exit_code: z.number().describe('Process exit code from the Python CLI'),
    stdout: z.string().describe('Standard output from the command'),
    stderr: z.string().describe('Standard error from the command'),
    parsed: z
      .unknown()
      .optional()
      .describe('Parsed JSON output when the command returned JSON'),
  }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

function isReadOnlyAction(action: RobotAction): boolean {
  return (READ_ONLY_ACTIONS as readonly string[]).includes(action)
}

function tryParseJson(text: string): unknown | undefined {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  const objectIndex = trimmed.indexOf('{')
  const arrayIndex = trimmed.indexOf('[')
  const startIndexCandidates = [objectIndex, arrayIndex].filter(
    index => index >= 0,
  )
  if (startIndexCandidates.length === 0) return undefined
  const startIndex = Math.min(...startIndexCandidates)
  try {
    return JSON.parse(trimmed.slice(startIndex))
  } catch {
    return undefined
  }
}

function buildAncestorCandidates(start: string): string[] {
  const candidates: string[] = []
  let current = resolve(start)
  for (let i = 0; i < 8; i += 1) {
    candidates.push(current)
    const parent = dirname(current)
    if (parent === current) break
    current = parent
  }
  return candidates
}

function getWorkspaceCandidates(): string[] {
  const moduleDir = dirname(fileURLToPath(import.meta.url))
  const explicitRoots = [
    process.env.ARC_ROBOT_WORKSPACE,
    process.cwd(),
    process.env.INIT_CWD,
    moduleDir,
    join(moduleDir, '..'),
    join(moduleDir, '..', '..'),
    join(moduleDir, '..', '..', '..'),
  ].filter(Boolean) as string[]

  return [
    ...new Set(explicitRoots.flatMap(root => buildAncestorCandidates(root))),
  ]
}

function resolveRobotWorkspaceRoot(): string {
  for (const candidate of getWorkspaceCandidates()) {
    const hasArc = existsSync(join(candidate, 'arc.py'))
    const hasStatus = existsSync(join(candidate, 'IMPLEMENTATION_STATUS.md'))
    if (hasArc && hasStatus) {
      return candidate
    }
  }

  throw new Error(
    'Could not locate the robot workspace root. Set ARC_ROBOT_WORKSPACE to the directory containing arc.py and IMPLEMENTATION_STATUS.md.',
  )
}

function buildRobotArgs(input: Input): string[] {
  switch (input.action) {
    case 'list':
      return ['arc.py', 'arm', '--json', 'list']
    case 'status':
      return ['arc.py', 'arm', '--json', 'status', input.arm_id as string]
    case 'state':
      return ['arc.py', 'arm', '--json', 'state', input.arm_id as string]
    case 'pose':
      return ['arc.py', 'arm', '--json', 'pose', input.arm_id as string]
    case 'home':
      return ['arc.py', 'arm', '--json', 'home', input.arm_id as string]
    case 'move':
      return [
        'arc.py',
        'arm',
        '--json',
        'move',
        input.arm_id as string,
        input.direction as string,
        '--step',
        String(input.step as number),
      ]
    case 'square':
      return [
        'arc.py',
        'arm',
        '--json',
        'square',
        input.arm_id as string,
        '--step',
        String(input.step ?? 25),
      ]
    case 'stop':
      return ['arc.py', 'arm', '--json', 'stop', input.arm_id as string]
    case 'recover':
      return ['arc.py', 'arm', '--json', 'recover', input.arm_id as string]
    case 'recover_home':
      return ['arc.py', 'arm', '--json', 'recover-home', input.arm_id as string]
  }
}

async function runPythonCommand(
  args: string[],
  cwd: string,
  signal: AbortSignal,
): Promise<{ code: number; stdout: string; stderr: string; command: string }> {
  const python = resolvePythonExecutable(cwd)
  const command = [python, ...args].join(' ')

  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(python, args, {
      cwd,
      signal,
      windowsHide: true,
    })

    let stdout = ''
    let stderr = ''

    child.stdout.on('data', chunk => {
      stdout += String(chunk)
    })
    child.stderr.on('data', chunk => {
      stderr += String(chunk)
    })

    child.on('error', error => {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        rejectPromise(
          new Error(
            `spawn ${python} ENOENT — Python runtime not found. ` +
              `Install Python 3.10+, use project .venv, or set ARC_PYTHON (cwd=${cwd}).`,
          ),
        )
        return
      }
      rejectPromise(error)
    })

    child.on('close', code => {
      resolvePromise({
        code: code ?? 1,
        stdout,
        stderr,
        command,
      })
    })
  })
}

export const RobotArmTool = buildTool({
  name: ROBOT_ARM_TOOL_NAME,
  searchHint: 'inspect or control MG400 robot arms through the ARC Python runtime',
  maxResultSizeChars: 100_000,
  userFacingName: () => 'Robot Arm',
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isConcurrencySafe() {
    return false
  },
  isReadOnly(input) {
    return isReadOnlyAction(input.action)
  },
  toAutoClassifierInput(input) {
    return [
      input.action,
      input.arm_id ?? '',
      input.direction ?? '',
      input.step?.toString() ?? '',
    ]
      .filter(Boolean)
      .join(' ')
  },
  async description(input) {
    const arm = input.arm_id ? ` on ${input.arm_id}` : ''
    if (input.action === 'home') {
      return `Run motion-only home action${arm}. This moves the arm to its configured named home destination without picking or placing.`
    }
    return `Run robot action ${input.action}${arm}`
  },
  async prompt() {
    return DESCRIPTION
  },
  async validateInput(input) {
    if (input.action !== 'list' && !input.arm_id) {
      return {
        result: false,
        message: `arm_id is required for action "${input.action}"`,
        errorCode: 1,
      }
    }

    if (input.action === 'move' && !input.direction) {
      return {
        result: false,
        message: 'direction is required for action "move"',
        errorCode: 2,
      }
    }

    if (input.action === 'move' && input.step === undefined) {
      return {
        result: false,
        message: 'step is required for action "move"',
        errorCode: 3,
      }
    }

    return { result: true }
  },
  async checkPermissions(input) {
    if (isReadOnlyAction(input.action)) {
      return {
        behavior: 'allow',
        updatedInput: input,
      }
    }

    const target = input.arm_id ? ` ${input.arm_id}` : ''
    const extra =
      input.action === 'move'
        ? ` ${input.direction} step=${input.step}`
        : input.action === 'square'
          ? ` step=${input.step ?? 25}`
          : ''

    return {
      behavior: 'ask',
      message: `Allow robot action ${input.action}${target}${extra}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    const parts = [input.action, input.arm_id, input.direction]
      .filter(Boolean)
      .join(' ')
    return `RobotArm: ${parts || 'pending'}`
  },
  renderToolResultMessage(output) {
    const summary = output.success ? 'ok' : 'failed'
    const target = output.arm_id ? ` ${output.arm_id}` : ''
    return `RobotArm ${output.action}${target}: ${summary}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: jsonStringify(output),
    }
  },
  async call(input, context) {
    const workspaceRoot = resolveRobotWorkspaceRoot()
    const args = buildRobotArgs(input)
    const result = await runPythonCommand(
      args,
      workspaceRoot,
      context.abortController.signal,
    )

    return {
      data: {
        workspace_root: workspaceRoot,
        command: result.command,
        action: input.action,
        arm_id: input.arm_id,
        success: result.code === 0,
        exit_code: result.code,
        stdout: result.stdout,
        stderr: result.stderr,
        parsed: tryParseJson(result.stdout),
      },
    }
  },
} satisfies ToolDef<InputSchema, Output>)
