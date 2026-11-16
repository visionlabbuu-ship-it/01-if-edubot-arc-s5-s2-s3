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
  arm_id: z.string().describe('Target arm identifier such as Arm1'),
  color: z
    .enum(['red', 'green', 'blue', 'unknown'])
    .describe('Color of the detected object to pick'),
  destination: z
    .string()
    .describe('Named destination such as home or drop_zone'),
  camera_id: z.string().optional().describe('Camera identifier such as Cam1'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PickDetectedPlaceTool = buildTool({
  name: 'pick_detected_place',
  userFacingName: () => 'Pick Detected Place',
  searchHint: 'pick a detected colored object with a robot arm and place it at a named destination; do not use for motion-only go-home requests',
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
    return `${input.arm_id} ${input.color} ${input.destination} ${input.camera_id ?? 'Cam1'}`
  },
  async description(input) {
    return `Pick detected ${input.color} object with ${input.arm_id} and place it at ${input.destination}. Use only for explicit grasp requests, not for simple go-home motion.`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow ${input.arm_id} to pick detected ${input.color} object and place it at ${input.destination}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `pick_detected_place ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `pick_detected_place ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'pick_detected_place',
        [
          'arc.py',
          'arm',
          '--json',
          'pick-detected-place',
          input.arm_id,
          '--color',
          input.color,
          '--destination',
          input.destination,
          '--camera-id',
          input.camera_id ?? 'Cam1',
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
