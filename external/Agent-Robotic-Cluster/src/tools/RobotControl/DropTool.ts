import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { jsonStringify } from '../../utils/slowOperations.js'
import { DROP_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  target_type: z.enum(['named', 'pose']).describe('Drop target kind. named means a configured zone; pose means explicit x/y.'),
  destination: z.string().optional().describe('Non-Storage named destination such as handoff_zone. Use place_to_storage_slot for LT/LB/RT/RB.'),
  x: z.number().optional().describe('Placement X in arm-local coordinates for pose targets'),
  y: z.number().optional().describe('Placement Y in arm-local coordinates for pose targets'),
  r: z.number().optional().describe('Tool rotation in degrees, default 0'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

function buildDropPlan(input: Input): string {
  const target: Record<string, unknown> =
    input.target_type === 'named'
      ? { type: 'named', name: input.destination }
      : { type: 'pose', x: input.x, y: input.y, r: input.r ?? 0 }

  return jsonStringify({
    name: 'drop',
    actions: [
      {
        action: 'drop',
        arm_id: input.arm_id,
        target,
      },
    ],
  })
}

function isStorageDestination(destination: string | undefined): boolean {
  const normalized = destination?.trim().replace(/[\s_-]+/g, '').toUpperCase()
  return (
    normalized === 'STORAGEZONE' ||
    normalized === 'DROPZONE' ||
    /^(LT|LB|RT|RB)([1-4])?$/.test(normalized ?? '')
  )
}

export const DropTool = buildTool({
  name: 'drop',
  userFacingName: () => 'Drop',
  searchHint:
    'standard drop sequence: move hover, move surface, grip off, move hover; never releases at hover; supports named zones or explicit pose',
  maxResultSizeChars: 140_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): OutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return false
  },
  isReadOnly() {
    return false
  },
  toAutoClassifierInput(input) {
    return `${input.arm_id} ${input.target_type} ${input.destination ?? ''}`
  },
  async description(input) {
    return `Drop with ${input.arm_id} at ${input.destination ?? `(${input.x}, ${input.y})`}`
  },
  async prompt() {
    return DROP_PROMPT
  },
  async validateInput(input) {
    if (input.target_type === 'named' && !input.destination) {
      return { result: false, message: 'drop target_type=named requires destination', errorCode: 1 }
    }
    if (input.target_type === 'named' && isStorageDestination(input.destination)) {
      return {
        result: false,
        message:
          'Storage destinations must use place_to_storage_slot so ARC can resolve reachability, reserve the Slot, park, rescan, and verify occupancy.',
        errorCode: 3,
      }
    }
    if (input.target_type === 'pose' && (input.x === undefined || input.y === undefined)) {
      return { result: false, message: 'drop target_type=pose requires x and y', errorCode: 2 }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow drop sequence on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `drop ${input.arm_id} ${input.destination ?? input.target_type}`
  },
  renderToolResultMessage(output) {
    return `drop ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'drop',
        [
          'arc.py',
          'plan',
          '--json',
          'execute',
          '--mode',
          'inline',
          '--plan-json',
          buildDropPlan(input),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
