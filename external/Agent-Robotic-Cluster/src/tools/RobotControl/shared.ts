import { spawn, type SpawnOptionsWithoutStdio } from 'child_process'
import { existsSync } from 'fs'
import { dirname, join, resolve } from 'path'
import { fileURLToPath } from 'url'
import { z } from 'zod/v4'
import { lazySchema } from '../../utils/lazySchema.js'

/**
 * Resolve a Python executable for ARC robot tools.
 * Linux/WSL often only ship `python3`; project venv is preferred when present.
 * Override with ARC_PYTHON / PYTHON / PYTHON_PATH.
 */
export function resolvePythonExecutable(workspaceRoot?: string): string {
  const explicit = [
    process.env.ARC_PYTHON,
    process.env.PYTHON,
    process.env.PYTHON_PATH,
  ].find(value => typeof value === 'string' && value.trim().length > 0)

  if (explicit) {
    return explicit.trim()
  }

  const roots = [
    workspaceRoot,
    process.env.ARC_ROBOT_WORKSPACE,
    process.cwd(),
  ].filter(Boolean) as string[]

  for (const root of roots) {
    for (const rel of [
      join('.venv', 'bin', 'python'),
      join('.venv', 'bin', 'python3'),
      join('.venv', 'Scripts', 'python.exe'),
      join('venv', 'bin', 'python'),
      join('venv', 'Scripts', 'python.exe'),
    ]) {
      const candidate = join(root, rel)
      if (existsSync(candidate)) {
        return candidate
      }
    }
  }

  // Prefer python3 on Unix โ€” bare `python` is often missing (spawn ENOENT).
  return process.platform === 'win32' ? 'python' : 'python3'
}

function spawnPython(
  args: string[],
  options: SpawnOptionsWithoutStdio & { cwd?: string },
) {
  const python = resolvePythonExecutable(options.cwd)
  return { python, child: spawn(python, args, options) }
}

export const incidentActionSchema = z.enum([
  'recover',
  'recover_home',
  'abort',
  'retry_last',
])

export const pathTypeSchema = z.enum(['rectangle', 'circle', 'arc', 'waypoints'])

export const robotCommandOutputSchema = lazySchema(() =>
  z.object({
    workspace_root: z
      .string()
      .describe('Workspace directory containing arc.py and IMPLEMENTATION_STATUS.md'),
    tool_name: z.string().describe('ARC robot tool that initiated the command'),
    command: z.string().describe('Executed Python command'),
    success: z.boolean().describe('Whether the command exited successfully'),
    exit_code: z.number().describe('Process exit code from the Python CLI'),
    stdout: z.string().describe('Standard output from the command'),
    stderr: z.string().describe('Standard error from the command'),
    parsed: z
      .unknown()
      .optional()
      .describe('Parsed JSON output when the command returned JSON'),
    robot_id: z.string().optional().describe('Target robot id when applicable'),
    fault_type: z.string().optional().describe('Detected fault type when present'),
    recommended_action: z
      .string()
      .optional()
      .describe('Recommended next action from the Python runtime'),
    saw_running_motion: z
      .boolean()
      .optional()
      .describe('Whether the runtime observed the robot running before faulting'),
    incident_detected: z
      .boolean()
      .optional()
      .describe('Whether the parsed payload indicates a safety incident'),
    guardrail_blocked: z
      .boolean()
      .optional()
      .describe('Whether ARC safety middleware blocked the command before motion'),
    scan_result: z
      .unknown()
      .optional()
      .describe('Structured scan result from camera scan command'),
    robot_state: z
      .unknown()
      .optional()
      .describe('Structured robot state from workspace state command'),
    storage_result: z
      .unknown()
      .optional()
      .describe('Structured Storage Slot scan, list, resolve, or placement result'),
    runtime_context: z
      .unknown()
      .optional()
      .describe('Compact calibrated runtime readiness and Storage summary'),
  }),
)

export type RobotCommandOutputSchema = ReturnType<
  typeof robotCommandOutputSchema
>
export type SafetyHints = {
  fault_type?: string
  recommended_action?: string
  saw_running_motion?: boolean
  incident_detected?: boolean
  guardrail_blocked?: boolean
}

type JsonRecord = Record<string, unknown>

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function compactPoint(value: unknown): JsonRecord | undefined {
  if (!isRecord(value)) return undefined

  const point: JsonRecord = {}
  for (const key of ['x', 'y', 'reachable']) {
    const item = value[key]
    if (typeof item === 'number' || typeof item === 'boolean') {
      point[key] = item
    }
  }
  return Object.keys(point).length > 0 ? point : undefined
}

function compactScannedObject(value: unknown): JsonRecord | undefined {
  if (!isRecord(value)) return undefined

  const object: JsonRecord = {}
  for (const key of ['id', 'color', 'shape', 'block', 'preferred_arm']) {
    if (typeof value[key] === 'string') object[key] = value[key]
  }

  const workspaceXY = compactPoint(value.workspace_xy)
  if (workspaceXY) object.workspace_xy = workspaceXY

  if (Array.isArray(value.reachable_by)) {
    object.reachable_by = value.reachable_by.filter(
      armID => typeof armID === 'string',
    )
  }

  if (isRecord(value.per_arm_robot_plane)) {
    const armPlanes: JsonRecord = {}
    for (const [armID, plane] of Object.entries(value.per_arm_robot_plane)) {
      const compactPlane = compactPoint(plane)
      if (compactPlane) armPlanes[armID] = compactPlane
    }
    if (Object.keys(armPlanes).length > 0) {
      object.per_arm_robot_plane = armPlanes
    }
  }

  return object
}

/**
 * Camera responses contain calibration matrices, zone geometry, contours, and
 * overlay paths. Those details are useful to the Python runtime but are far
 * too large for a conversational tool response. Keep the motion-planning
 * essentials and deliberately discard the rest at the ARC bridge.
 */
export function compactScanResult(parsed: unknown): JsonRecord | undefined {
  if (!isRecord(parsed)) return undefined

  const result: JsonRecord = {}
  for (const key of [
    'ok',
    'camera_id',
    'timestamp',
    'object_count',
    'calibration_ready',
  ]) {
    const value = parsed[key]
    if (
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      result[key] = value
    }
  }

  const objects = Array.isArray(parsed.objects)
    ? parsed.objects
        .map(compactScannedObject)
        .filter((object): object is JsonRecord => object !== undefined)
    : []
  result.objects = objects
  result.object_count =
    typeof parsed.object_count === 'number' ? parsed.object_count : objects.length

  const selectedObject = compactScannedObject(parsed.selected_object)
  result.selected_object = selectedObject ?? null
  return result
}

function compactStorageSlot(value: unknown): JsonRecord | undefined {
  if (!isRecord(value)) return undefined

  const slot: JsonRecord = {}
  for (const key of ['name', 'label', 'group', 'occupancy', 'preferred_arm']) {
    if (typeof value[key] === 'string') slot[key] = value[key]
  }
  if (typeof slot.name !== 'string' && typeof slot.label === 'string') {
    slot.name = slot.label
  }
  delete slot.label

  let color: string | undefined
  for (const key of ['color', 'occupancy_color', 'object_color']) {
    if (typeof value[key] === 'string' && value[key].trim()) {
      color = value[key].trim().toLowerCase()
      break
    }
  }
  if (!color && isRecord(value.held_object) && typeof value.held_object.color === 'string') {
    color = value.held_object.color.trim().toLowerCase()
  }
  slot.color = color ?? null

  for (const key of ['reachable_by', 'allowed_arms']) {
    if (Array.isArray(value[key])) {
      slot[key] = value[key].filter(item => typeof item === 'string')
    }
  }
  return Object.keys(slot).length > 0 ? slot : undefined
}

function storageSummary(slots: JsonRecord[]): JsonRecord {
  const summary: JsonRecord = {
    empty: 0,
    occupied: 0,
    unknown: 0,
    reserved: 0,
    verification_pending: 0,
    occupied_by_color: {},
    by_group: {},
  }
  const occupiedByColor = summary.occupied_by_color as JsonRecord
  const byGroup = summary.by_group as JsonRecord

  for (const slot of slots) {
    const status =
      typeof slot.occupancy === 'string' ? slot.occupancy : 'unknown'
    summary[status] =
      (typeof summary[status] === 'number' ? summary[status] : 0) + 1
    const group = typeof slot.group === 'string' ? slot.group : 'unassigned'
    const currentGroup = isRecord(byGroup[group])
      ? byGroup[group]
      : {
          total: 0,
          empty: 0,
          occupied: 0,
          unknown: 0,
          reserved: 0,
          verification_pending: 0,
          occupied_by_color: {},
        }
    currentGroup.total =
      (typeof currentGroup.total === 'number' ? currentGroup.total : 0) + 1
    currentGroup[status] =
      (typeof currentGroup[status] === 'number' ? currentGroup[status] : 0) + 1
    byGroup[group] = currentGroup

    if (status === 'occupied' && typeof slot.color === 'string') {
      occupiedByColor[slot.color] =
        (typeof occupiedByColor[slot.color] === 'number'
          ? occupiedByColor[slot.color]
          : 0) + 1
      const groupColors = currentGroup.occupied_by_color as JsonRecord
      groupColors[slot.color] =
        (typeof groupColors[slot.color] === 'number'
          ? groupColors[slot.color]
          : 0) + 1
    }
  }
  return summary
}

/**
 * Storage scans contain full calibration geometry for every Slot. Keep only
 * identity, occupancy, color, routing, and reachability so the model can map
 * an existing color directly to its Slot group without probing four groups.
 */
export function compactStorageResult(
  parsed: unknown,
  freshScan = false,
): JsonRecord | undefined {
  if (!isRecord(parsed)) return undefined

  const embedded = isRecord(parsed.compact) ? parsed.compact : undefined
  const registry = isRecord(parsed.registry) ? parsed.registry : undefined
  const rawSlots = Array.isArray(embedded?.slots)
    ? embedded.slots
    : Array.isArray(parsed.slots)
      ? parsed.slots
      : Array.isArray(registry?.slots)
        ? registry.slots
        : []
  const slots = rawSlots
    .map(compactStorageSlot)
    .filter((slot): slot is JsonRecord => slot !== undefined)

  const colorLocations: JsonRecord = {}
  const emptySlotsByGroup: JsonRecord = { LT: [], LB: [], RT: [], RB: [] }
  for (const slot of slots) {
    const name = typeof slot.name === 'string' ? slot.name : undefined
    const group = typeof slot.group === 'string' ? slot.group : undefined
    const status =
      typeof slot.occupancy === 'string' ? slot.occupancy : 'unknown'
    if (status === 'empty' && name && group) {
      const names = Array.isArray(emptySlotsByGroup[group])
        ? (emptySlotsByGroup[group] as unknown[])
        : []
      names.push(name)
      emptySlotsByGroup[group] = names
    }
    if (
      status === 'occupied' &&
      typeof slot.color === 'string' &&
      name &&
      group
    ) {
      const existing = isRecord(colorLocations[slot.color])
        ? colorLocations[slot.color]
        : { count: 0, slots: [], groups: [] }
      existing.count =
        (typeof existing.count === 'number' ? existing.count : 0) + 1
      const names = Array.isArray(existing.slots) ? existing.slots : []
      names.push(name)
      existing.slots = names
      const groups = Array.isArray(existing.groups) ? existing.groups : []
      if (!groups.includes(group)) groups.push(group)
      existing.groups = groups
      colorLocations[slot.color] = existing
    }
  }

  const result: JsonRecord = {
    ok: typeof parsed.ok === 'boolean' ? parsed.ok : true,
    fresh_scan:
      typeof parsed.fresh_scan === 'boolean' ? parsed.fresh_scan : freshScan,
    slot_count:
      typeof embedded?.slot_count === 'number'
        ? embedded.slot_count
        : typeof parsed.slot_count === 'number'
          ? parsed.slot_count
          : slots.length,
    summary:
      slots.length > 0
        ? storageSummary(slots)
        : isRecord(embedded?.summary)
          ? embedded.summary
          : isRecord(parsed.summary)
            ? parsed.summary
            : isRecord(registry?.summary)
              ? registry.summary
              : {},
    slots,
    color_locations: colorLocations,
    empty_slots_by_group: emptySlotsByGroup,
  }
  for (const key of ['error', 'error_code', 'updated_at']) {
    if (typeof parsed[key] === 'string') result[key] = parsed[key]
  }
  const policy = isRecord(parsed.policy)
    ? parsed.policy
    : isRecord(registry?.slot_policy)
      ? registry.slot_policy
      : undefined
  if (policy) result.policy = policy
  return result
}
export function compactRuntimeContext(parsed: unknown): JsonRecord | undefined {
  if (!isRecord(parsed)) return undefined
  const result: JsonRecord = {}
  for (const key of [
    'ok',
    'active_arm_ids',
    'arms',
    'policy',
    'scan_contract',
    'source_of_truth',
    'storage',
  ]) {
    if (key in parsed) result[key] = parsed[key]
  }
  return Object.keys(result).length > 0 ? result : undefined
}

export function compactRobotState(parsed: unknown): JsonRecord | undefined {
  if (!isRecord(parsed)) return undefined
  const result: JsonRecord = {}
  for (const key of [
    'ok',
    'arms',
    'holding',
    'shared_zone',
    'park',
    'faults',
    'incidents',
    'active_arm_ids',
    'ready',
    'online',
  ]) {
    if (key in parsed) result[key] = parsed[key]
  }
  return Object.keys(result).length > 0 ? result : parsed
}

export function modelFacingRobotResult(output: unknown): string {
  if (!isRecord(output)) {
    return JSON.stringify(output ?? {})
  }
  const data =
    output.scan_result ??
    output.storage_result ??
    output.runtime_context ??
    output.robot_state ??
    output.parsed
  const payload: JsonRecord = {
    ok: output.success === true || output.ok === true,
  }
  if (typeof output.tool_name === 'string') payload.tool = output.tool_name
  if (output.guardrail_blocked === true) payload.guardrail_blocked = true
  if (typeof output.fault_type === 'string' && output.fault_type) {
    payload.fault_type = output.fault_type
  }
  if (data !== undefined) payload.data = data
  else {
    const slim: JsonRecord = {}
    for (const key of ['ok', 'success', 'error', 'message', 'status']) {
      if (key in output) slim[key] = output[key]
    }
    payload.data = Object.keys(slim).length > 0 ? slim : output
  }
  return JSON.stringify(payload)
}

export function tryParseJson(text: string): unknown | undefined {
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

export function resolveRobotWorkspaceRoot(): string {
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

async function runPythonCommand(
  args: string[],
  cwd: string,
  signal: AbortSignal,
): Promise<{ code: number; stdout: string; stderr: string; command: string }> {
  const runtimeMode = process.env.ARC_RUNTIME_MODE || 'subprocess'
  const serviceUrl = process.env.ARC_SERVICE_URL || 'http://127.0.0.1:8765'

  // Service mode โ€” call HTTP endpoint instead of spawning
  if (runtimeMode === 'service') {
    return await callRobotService(args, serviceUrl, signal)
  }

  // Subprocess mode (default) โ€” spawn resolved python (venv / python3 / ARC_PYTHON)
  const { python, child } = spawnPython(args, {
    cwd,
    signal,
    windowsHide: true,
  })
  const command = [python, ...args].join(' ')

  return await new Promise((resolvePromise, rejectPromise) => {
    let stdout = ''
    let stderr = ''

    child.stdout?.on('data', chunk => {
      stdout += String(chunk)
    })
    child.stderr?.on('data', chunk => {
      stderr += String(chunk)
    })

    child.on('error', error => {
      // Enrich ENOENT so operators know which binary was missing.
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        rejectPromise(
          new Error(
            `spawn ${python} ENOENT โ€” Python runtime not found. ` +
              `Install Python 3.10+, activate the project .venv, or set ARC_PYTHON ` +
              `to the interpreter that has ARC deps (cwd=${cwd}).`,
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

async function callRobotService(
  args: string[],
  serviceUrl: string,
  signal: AbortSignal,
): Promise<{ code: number; stdout: string; stderr: string; command: string }> {
  // Parse args to determine endpoint and body
  // args format: ['arc.py', 'camera', '--json', 'scan', 'Cam1', '--color', 'any']
  //           or: ['arc.py', 'workspace', '--json', 'state']
  //           or: ['arc.py', 'arm', '--json', 'move', 'Arm1', 'HOME']

  const subcommand = args[1] // 'camera' | 'workspace' | 'arm'
  const jsonFlagIndex = args.indexOf('--json')
  const actionIndex = jsonFlagIndex >= 0 ? jsonFlagIndex + 1 : 2
  const action = args[actionIndex] // 'scan' | 'state' | 'move' | 'stop' | etc.

  let endpoint = ''
  let body: Record<string, unknown> = {}
  let method = 'POST'

  try {
    if (subcommand === 'camera' && action === 'scan') {
      endpoint = '/scan'
      const cameraId = args[actionIndex + 1] || 'Cam1'
      const colorIdx = args.indexOf('--color')
      const color = colorIdx >= 0 ? args[colorIdx + 1] : 'any'
      const selectionIdx = args.indexOf('--selection')
      const selection = selectionIdx >= 0 ? args[selectionIdx + 1] : 'first'
      body = { camera_id: cameraId, color, selection }
    } else if (subcommand === 'workspace' && action === 'layout') {
      endpoint = '/workspace/layout'
      method = 'GET'
    } else if (subcommand === 'workspace' && action === 'zones') {
      endpoint = '/workspace/zones'
      method = 'GET'
    } else if (subcommand === 'workspace' && action === 'storage-groups') {
      endpoint = '/storage/groups'
      method = 'GET'
    } else if (subcommand === 'workspace' && action === 'context') {
      endpoint = '/runtime/context'
      method = 'GET'
    } else if (subcommand === 'storage' && action === 'scan') {
      const imagePathIdx = args.indexOf('--image-path')
      const expectedCountIdx = args.indexOf('--expected-count')
      const query = new URLSearchParams()
      if (imagePathIdx >= 0) query.set('image_path', args[imagePathIdx + 1])
      if (expectedCountIdx >= 0) query.set('expected_count', args[expectedCountIdx + 1])
      endpoint = `/storage/scan${query.size > 0 ? `?${query.toString()}` : ''}`
    } else if (subcommand === 'storage' && action === 'list') {
      const groupIdx = args.indexOf('--group')
      const occupancyIdx = args.indexOf('--occupancy')
      const query = new URLSearchParams()
      if (groupIdx >= 0) query.set('group', args[groupIdx + 1])
      if (occupancyIdx >= 0) query.set('occupancy', args[occupancyIdx + 1])
      endpoint = `/storage/slots${query.size > 0 ? `?${query.toString()}` : ''}`
      method = 'GET'
    } else if (subcommand === 'storage' && action === 'resolve') {
      endpoint = '/storage/resolve'
      const referenceIdx = args.indexOf('--reference')
      const colorIdx = args.indexOf('--color')
      const robotIdx = args.indexOf('--robot-id')
      body = {
        reference: referenceIdx >= 0 ? args[referenceIdx + 1] : undefined,
        color: colorIdx >= 0 ? args[colorIdx + 1] : undefined,
        robot_id: robotIdx >= 0 ? args[robotIdx + 1] : undefined,
        require_empty: true,
      }
    } else if (subcommand === 'storage' && action === 'place') {
      endpoint = '/storage/place'
      const robotIdx = args.indexOf('--robot-id')
      const referenceIdx = args.indexOf('--reference')
      const colorIdx = args.indexOf('--color')
      body = {
        robot_id: robotIdx >= 0 ? args[robotIdx + 1] : undefined,
        reference: referenceIdx >= 0 ? args[referenceIdx + 1] : undefined,
        color: colorIdx >= 0 ? args[colorIdx + 1] : undefined,
      }
    } else if (subcommand === 'workspace' && action === 'plan-intent') {
      endpoint = '/planning/intent'
      const destinationIdx = args.indexOf('--destination')
      const sourceIdx = args.indexOf('--source-zone')
      const colorIdx = args.indexOf('--color')
      const robotIdx = args.indexOf('--robot-id')
      body = {
        objects: [],
        destination_reference: destinationIdx >= 0 ? args[destinationIdx + 1] : '',
        source_zone: sourceIdx >= 0 ? args[sourceIdx + 1] : undefined,
        color: colorIdx >= 0 ? args[colorIdx + 1] : 'any',
        robot_id: robotIdx >= 0 ? args[robotIdx + 1] : undefined,
      }
    } else if (subcommand === 'workspace' && action === 'state') {
      endpoint = '/state'
      method = 'GET'
    } else if (subcommand === 'arm' && action === 'list') {
      endpoint = '/list_robots'
      method = 'GET'
    } else if (subcommand === 'arm' && action === 'move') {
      endpoint = '/move'
      const armId = args[actionIndex + 1]
      const moveAction = args[actionIndex + 2]
      const stepIdx = args.indexOf('--step')
      const step = stepIdx >= 0 ? parseFloat(args[stepIdx + 1]) : undefined
      body = { arm_id: armId, action: moveAction, step }
    } else if (subcommand === 'arm' && action === 'move-to-pose') {
      endpoint = '/move'
      const armId = args[actionIndex + 1]
      const xIdx = args.indexOf('--x')
      const yIdx = args.indexOf('--y')
      const zIdx = args.indexOf('--z')
      const rIdx = args.indexOf('--r')
      body = {
        arm_id: armId,
        x: xIdx >= 0 ? parseFloat(args[xIdx + 1]) : undefined,
        y: yIdx >= 0 ? parseFloat(args[yIdx + 1]) : undefined,
        z: zIdx >= 0 ? parseFloat(args[zIdx + 1]) : undefined,
        r: rIdx >= 0 ? parseFloat(args[rIdx + 1]) : 0.0,
      }
    } else if (subcommand === 'arm' && action === 'stop') {
      endpoint = '/stop'
      const armId = args[actionIndex + 1]
      body = { arm_id: armId }
    } else if (subcommand === 'arm' && action === 'recover') {
      endpoint = '/recover'
      const armId = args[actionIndex + 1]
      body = { arm_id: armId }
    } else if (subcommand === 'arm' && action === 'tool-state') {
      endpoint = '/grip'
      const armId = args[actionIndex + 1]
      const enableIdx = args.indexOf('--enable')
      const enable = enableIdx >= 0 ? args[enableIdx + 1] === 'on' : false
      body = { arm_id: armId, enable }
    } else {
      // Unknown command โ€” fallback to subprocess
      console.warn(
        `[ARC Service] Unknown command, falling back to subprocess: ${args.join(' ')}`,
      )
      const cwd = process.cwd()
      const { python, child } = spawnPython(args, {
        cwd,
        signal,
        windowsHide: true,
      })
      const command = [python, ...args].join(' ')
      return await new Promise((resolvePromise, rejectPromise) => {
        let stdout = ''
        let stderr = ''
        child.stdout?.on('data', chunk => (stdout += String(chunk)))
        child.stderr?.on('data', chunk => (stderr += String(chunk)))
        child.on('error', error => {
          if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
            rejectPromise(
              new Error(
                `spawn ${python} ENOENT โ€” Python runtime not found. ` +
                  `Set ARC_PYTHON or install project .venv (cwd=${cwd}).`,
              ),
            )
            return
          }
          rejectPromise(error)
        })
        child.on('close', code =>
          resolvePromise({ code: code ?? 1, stdout, stderr, command }),
        )
      })
    }

    // Call service
    const url = `${serviceUrl}${endpoint}`
    const command = `${method} ${url}`

    const fetchOptions: RequestInit = {
      method,
      signal,
      headers: { 'Content-Type': 'application/json' },
    }

    if (method === 'POST' && Object.keys(body).length > 0) {
      fetchOptions.body = JSON.stringify(body)
    }

    const response = await fetch(url, fetchOptions)

    if (!response.ok) {
      const errorText = await response.text()
      return {
        code: response.status >= 500 ? 1 : 2,
        stdout: '',
        stderr: errorText || `HTTP ${response.status}`,
        command,
      }
    }

    const result = await response.json()
    const semanticFailure = isRecord(result) && result.ok === false
    return {
      code: semanticFailure ? 2 : 0,
      stdout: JSON.stringify(result),
      stderr: '',
      command,
    }
  } catch (error) {
    const command = `${method} ${serviceUrl}${endpoint}`
    return {
      code: 1,
      stdout: '',
      stderr: error instanceof Error ? error.message : String(error),
      command,
    }
  }
}

function collectObjects(value: unknown, depth = 0): JsonRecord[] {
  if (!isRecord(value) || depth > 5) return []
  const direct: JsonRecord[] = [value]
  const nestedKeys = ['result', 'incident', 'details', 'guardrail', 'final_state', 'state']
  for (const key of nestedKeys) {
    direct.push(...collectObjects(value[key], depth + 1))
  }
  for (const key of ['results', 'steps', 'events']) {
    const nested = value[key]
    if (Array.isArray(nested)) {
      for (const item of nested) {
        direct.push(...collectObjects(item, depth + 1))
      }
    }
  }
  return direct
}

export function extractSafetyHints(parsed: unknown): SafetyHints {
  const candidates = collectObjects(parsed)

  for (const candidate of candidates) {
    const faultType =
      typeof candidate.fault_type === 'string' ? candidate.fault_type : undefined
    const recommendedAction =
      typeof candidate.recommended_action === 'string'
        ? candidate.recommended_action
        : undefined
    const sawRunningMotion =
      typeof candidate.saw_running_motion === 'boolean'
        ? candidate.saw_running_motion
        : undefined
    const guardrailBlocked =
      candidate.guardrail_blocked === true || faultType === 'guardrail_blocked'

    const incidentDetected =
      faultType === 'runtime_fault' ||
      faultType === 'guardrail_blocked' ||
      sawRunningMotion === true ||
      recommendedAction === 'await_operator_decision' ||
      guardrailBlocked

    if (
      faultType !== undefined ||
      recommendedAction !== undefined ||
      sawRunningMotion !== undefined ||
      guardrailBlocked ||
      incidentDetected
    ) {
      return {
        fault_type: faultType,
        recommended_action: recommendedAction,
        saw_running_motion: sawRunningMotion,
        incident_detected: incidentDetected,
        guardrail_blocked: guardrailBlocked,
      }
    }
  }

  return {}
}

export async function executeRobotCommand(
  toolName: string,
  args: string[],
  signal: AbortSignal,
  robotID?: string,
) {
  const workspaceRoot = resolveRobotWorkspaceRoot()
  const result = await runPythonCommand(args, workspaceRoot, signal)
  const parsed = tryParseJson(result.stdout)
  const compactScan =
    toolName === 'scan' || toolName === 'camera_scan'
      ? compactScanResult(parsed)
      : undefined
  const compactStorage =
    toolName === 'scan_storage_slots' || toolName === 'list_storage_slots'
      ? compactStorageResult(parsed, toolName === 'scan_storage_slots')
      : undefined
  const compactRuntime =
    toolName === 'get_runtime_context'
      ? compactRuntimeContext(parsed)
      : undefined
  const compactState =
    toolName === 'get_state' || toolName === 'workspace_state'
      ? compactRobotState(parsed)
      : undefined
  const presentedParsed =
    compactScan ?? compactStorage ?? compactRuntime ?? compactState ?? parsed
  const safety = extractSafetyHints(parsed)

  const baseResult = {
    workspace_root: workspaceRoot,
    tool_name: toolName,
    command: result.command,
    success: result.code === 0,
    exit_code: result.code,
    // Do not include the original camera JSON alongside its compact form.
    // It can exceed the tool-result limit several times over.
    stdout: compactScan || compactStorage || compactRuntime || compactState
      ? JSON.stringify(presentedParsed)
      : result.stdout,
    stderr: result.stderr,
    parsed: presentedParsed,
    robot_id: robotID,
    ...safety,
  }

  // Extract structured fields for visualization
  if (isRecord(parsed)) {
    if (toolName === 'scan' || toolName === 'camera_scan') {
      return { ...baseResult, scan_result: compactScan ?? parsed }
    }
    if (toolName === 'get_state' || toolName === 'workspace_state') {
      return { ...baseResult, robot_state: compactState ?? parsed }
    }
    if (
      toolName === 'scan_storage_slots' ||
      toolName === 'list_storage_slots' ||
      toolName === 'resolve_storage_slot' ||
      toolName === 'place_to_storage_slot'
    ) {
      return { ...baseResult, storage_result: compactStorage ?? parsed }
    }
    if (toolName === 'get_runtime_context') {
      return { ...baseResult, runtime_context: compactRuntime ?? parsed }
    }
  }

  return baseResult
}

