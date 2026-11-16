import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { jsonStringify } from '../../utils/slowOperations.js'
import { PICK_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  x: z.number().describe('Object X in arm-local coordinates, usually camera scan per_arm_robot_plane[arm].x'),
  y: z.number().describe('Object Y in arm-local XY plane coordinates, not vertical Z; usually camera scan per_arm_robot_plane[arm].y'),
  r: z.number().optional().describe('Tool rotation in degrees, default 0'),
  object_id: z.string().optional().describe('Optional detected object id for logging'),
  color: z.enum(['red', 'green', 'blue', 'unknown']).optional().describe('Optional detected color for logging'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

function buildPickPlan(input: Input): string {
  return jsonStringify({
    name: 'pick',
    actions: [
      {
        action: 'pick',
        arm_id: input.arm_id,
        target: {
          type: 'pose',
          x: input.x,
          y: input.y,
          r: input.r ?? 0,
        },
        object_id: input.object_id,
        color: input.color,
      },
    ],
  })
}

export const PickTool = buildTool({
  name: 'pick',
  userFacingName: () => 'Pick',
  searchHint: 'standard pick sequence: move hover, move surface, grip on, move hover; works with explicit object pose',
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
    return `${input.arm_id} ${input.x} ${input.y}`
  },
  async description(input) {
    return `Pick with ${input.arm_id} at x=${input.x}, y=${input.y}`
  },
  async prompt() {
    return PICK_PROMPT
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow pick sequence on ${input.arm_id} at (${input.x}, ${input.y})?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `pick ${input.arm_id} (${input.x}, ${input.y})`
  },
  renderToolResultMessage(output) {
    return `pick ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
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
        'pick',
        [
          'arc.py',
          'plan',
          '--json',
          'execute',
          '--mode',
          'inline',
          '--plan-json',
          buildPickPlan(input),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
