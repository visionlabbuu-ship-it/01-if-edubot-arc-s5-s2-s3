import { afterEach, beforeEach, expect, test } from 'bun:test'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { DropTool } from './tools/RobotControl/DropTool.js'
import { GetRuntimeContextTool } from './tools/RobotControl/GetRuntimeContextTool.js'
import { ListStorageSlotsTool } from './tools/RobotControl/ListStorageSlotsTool.js'
import { PlaceToStorageSlotTool } from './tools/RobotControl/PlaceToStorageSlotTool.js'
import { ResolveStorageSlotTool } from './tools/RobotControl/ResolveStorageSlotTool.js'
import { ScanStorageSlotsTool } from './tools/RobotControl/ScanStorageSlotsTool.js'

const originalSimple = process.env.CLAUDE_CODE_SIMPLE
const originalMode = process.env.ARC_RUNTIME_MODE
const originalServiceURL = process.env.ARC_SERVICE_URL
const originalWorkspace = process.env.ARC_ROBOT_WORKSPACE
const originalFetch = globalThis.fetch

beforeEach(() => {
  delete process.env.CLAUDE_CODE_SIMPLE
})

afterEach(() => {
  process.env.CLAUDE_CODE_SIMPLE = originalSimple
  process.env.ARC_RUNTIME_MODE = originalMode
  process.env.ARC_SERVICE_URL = originalServiceURL
  process.env.ARC_ROBOT_WORKSPACE = originalWorkspace
  globalThis.fetch = originalFetch
})

test('default and simple surfaces expose the intended Storage tools', () => {
  const source = readFileSync(new URL('./tools.ts', import.meta.url), 'utf8')
  const lean = source.slice(
    source.indexOf('const leanTools: Tools'),
    source.indexOf("if (!areRobotDeveloperToolsEnabled())"),
  )
  const simple = source.slice(
    source.indexOf('const simpleTools: Tool[]'),
    source.indexOf('return filterToolsByDenyRules(simpleTools'),
  )

  for (const symbol of [
    'GetRuntimeContextTool',
    'ScanStorageSlotsTool',
    'ListStorageSlotsTool',
    'ResolveStorageSlotTool',
    'PlaceToStorageSlotTool',
  ]) {
    expect(lean).toContain(symbol)
  }
  for (const symbol of [
    'GetRuntimeContextTool',
    'ScanStorageSlotsTool',
    'ListStorageSlotsTool',
    'ResolveStorageSlotTool',
  ]) {
    expect(simple).toContain(symbol)
  }
  expect(simple).not.toContain('PlaceToStorageSlotTool')

  const systemPrompt = readFileSync(
    new URL('./constants/prompts.ts', import.meta.url),
    'utf8',
  )
  for (const name of [
    'get_runtime_context',
    'scan_storage_slots',
    'list_storage_slots',
    'resolve_storage_slot',
    'place_to_storage_slot',
  ]) {
    expect(systemPrompt).toContain(name)
  }
})

test('natural-language Storage guidance forbids quadrant guessing and parallel motion', async () => {
  const prompt = await PlaceToStorageSlotTool.prompt()
  const dropPrompt = await DropTool.prompt()
  const shared = readFileSync(
    new URL('./tools/RobotControl/prompt.ts', import.meta.url),
    'utf8',
  )

  expect(shared).toContain('Bare left/ซ้าย or right/ขวา is ambiguous')
  expect(shared).toContain('ซ้ายล่าง = LB')
  expect(shared).toContain('ขวาบน = RT')
  expect(shared).toContain('only one arm moving at a time')
  expect(shared).toContain('Do not add manual move or drop steps around it')
  expect(shared).toContain('Before every real Storage placement task')
  expect(shared).toContain('Storage workflows must enter the semantic planner')
  expect(shared).toContain('Always use plan_task')
  expect(shared).toContain('including Storage')
  expect(shared).toContain('Never describe or execute Storage work as generic list/status/move/drop/stop steps')
  expect(shared).toContain('ask which Storage group to use')
  expect(shared).toContain('always means a sequential two-arm plan')
  expect(shared).toContain('color_locations[color].groups')
  expect(shared).toContain('without asking or probing LT/LB/RT/RB individually')
  expect(prompt).toContain('Exclusive Storage placement tool')
  expect(dropPrompt).toContain('Non-Storage release only')
})
test('Storage schemas stay strict and preserve the Python contract names', async () => {
  expect(ScanStorageSlotsTool.inputSchema.parse({}).expected_count).toBe(16)
  expect(ListStorageSlotsTool.inputSchema.safeParse({ group: 'LB', occupancy: 'empty' }).success).toBe(true)
  expect(ListStorageSlotsTool.inputSchema.safeParse({ group: 'left-bottom' }).success).toBe(false)
  expect(PlaceToStorageSlotTool.inputSchema.safeParse({ robot_id: 'Arm1', reference: 'LB' }).success).toBe(true)
  expect(PlaceToStorageSlotTool.inputSchema.safeParse({ arm_id: 'Arm1', reference: 'LB' }).success).toBe(false)

  const invalidResolve = await ResolveStorageSlotTool.validateInput({})
  expect(invalidResolve.result).toBe(false)
})

test('generic drop redirects every Storage reference to place_to_storage_slot', async () => {
  for (const destination of ['LT', 'LB4', 'rt3', 'RB', 'storage_zone', 'drop-zone']) {
    const result = await DropTool.validateInput(
      { arm_id: 'Arm1', target_type: 'named', destination },
    )
    expect(result.result).toBe(false)
    expect(result.result === false && result.message).toContain('place_to_storage_slot')
  }

  const normalDrop = await DropTool.validateInput(
    { arm_id: 'Arm1', target_type: 'named', destination: 'handoff_zone' },
  )
  expect(normalDrop.result).toBe(true)
})

test('Storage tools map to service endpoints with structured request data', async () => {
  process.env.ARC_RUNTIME_MODE = 'service'
  process.env.ARC_SERVICE_URL = 'http://robot.test'
  process.env.ARC_ROBOT_WORKSPACE = resolve(process.cwd(), '..', '..')

  const requests: Array<{ url: string; init?: RequestInit }> = []
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(input), init })
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  const context = { abortController: new AbortController() } as never

  await ScanStorageSlotsTool.call(
    { image_path: 'fixture image.png', expected_count: 16 },
    context,
  )
  await ListStorageSlotsTool.call(
    { group: 'LB', occupancy: 'empty' },
    context,
  )
  await ResolveStorageSlotTool.call(
    { reference: 'LB', color: 'blue', robot_id: 'Arm1' },
    context,
  )
  await PlaceToStorageSlotTool.call(
    { robot_id: 'Arm1', reference: 'LB', color: 'blue' },
    context,
  )
  await GetRuntimeContextTool.call({}, context)

  expect(requests.map(request => request.url)).toEqual([
    'http://robot.test/storage/scan?image_path=fixture+image.png&expected_count=16',
    'http://robot.test/storage/slots?group=LB&occupancy=empty',
    'http://robot.test/storage/resolve',
    'http://robot.test/storage/place',
    'http://robot.test/runtime/context',
  ])
  expect(JSON.parse(String(requests[2]?.init?.body))).toEqual({
    reference: 'LB',
    color: 'blue',
    robot_id: 'Arm1',
    require_empty: true,
  })
  expect(JSON.parse(String(requests[3]?.init?.body))).toEqual({
    robot_id: 'Arm1',
    reference: 'LB',
    color: 'blue',
  })

  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ ok: false, error_code: 'slot_unreachable' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })) as unknown as typeof fetch
  const unavailable = await ResolveStorageSlotTool.call(
    { reference: 'LB', robot_id: 'Arm1' },
    context,
  )
  expect(unavailable.data.success).toBe(false)
  expect(unavailable.data.parsed).toEqual({
    ok: false,
    error_code: 'slot_unreachable',
  })
})
