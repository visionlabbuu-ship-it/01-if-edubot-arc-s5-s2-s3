import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  destination: z
    .string()
    .describe('Named destination such as storage_zone (Arm1 side) or drop_zone (Arm2 side)'),
  color: z
    .enum(['any', 'red', 'green', 'blue', 'unknown'])
    .optional()
    .describe('Color filter; "any" picks any color (default any)'),
  camera_id: z.string().optional().describe('Camera identifier such as Cam1'),
  max_picks: z
    .number()
    .int()
    .optional()
    .describe('Maximum loop iterations (default 10)'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PickAllDetectedPlaceTool = buildTool({
  name: 'pick_all_detected_place',
  userFacingName: () => 'Pick All Detected Place',
  searchHint:
    'pick all reachable detected objects (optionally filtered by color) with one arm and place them at a single named destination; use for bulk pick requests like "หยิบทุกก้อน"',
  maxResultSizeChars: 100_000,
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
    return `${input.arm_id} ${input.color ?? 'any'} ${input.destination} ${input.camera_id ?? 'Cam1'}`
  },
  async description(input) {
    const colorPart = input.color && input.color !== 'any' ? `${input.color} ` : ''
    return `Pick all reachable detected ${colorPart}objects with ${input.arm_id} and place them at ${input.destination}. Loops scan→pick→place until none remain. Use only for bulk grasp requests.`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  async checkPermissions(input) {
    const colorPart = input.color && input.color !== 'any' ? `${input.color} ` : 'all reachable '
    return {
      behavior: 'ask',
      message: `Allow ${input.arm_id} to pick ${colorPart}objects and place them at ${input.destination}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `pick_all_detected_place ${input.arm_id} -> ${input.destination}`
  },
  renderToolResultMessage(output) {
    return `pick_all_detected_place ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args: string[] = [
      'arc.py',
      'arm',
      '--json',
      'pick-all-detected-place',
      input.arm_id,
      '--destination',
      input.destination,
    ]
    if (input.color) {
      args.push('--color', input.color)
    }
    if (input.camera_id) {
      args.push('--camera-id', input.camera_id)
    }
    if (input.max_picks !== undefined) {
      args.push('--max-picks', String(input.max_picks))
    }
    return {
      data: await executeRobotCommand(
        'pick_all_detected_place',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
