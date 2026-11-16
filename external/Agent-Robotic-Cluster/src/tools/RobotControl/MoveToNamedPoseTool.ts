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
    .describe('Named target such as handoff_zone, storage_zone, drop_zone, or home'),
  phase: z
    .enum(['hover', 'surface'])
    .describe('Configured Z phase at the named target. Use hover to approach/retreat and surface to pick/place.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const MoveToNamedPoseTool = buildTool({
  name: 'move_to_named_pose',
  userFacingName: () => 'Move To Named Pose',
  searchHint:
    'atomic motion to named target at hover or surface Z; no suction toggle; use handoff_zone for staged handoff',
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
    return `${input.arm_id} ${input.destination} ${input.phase}`
  },
  async description(input) {
    return `Move ${input.arm_id} to ${input.destination} at phase=${input.phase}. Atomic motion only; does not toggle suction.`
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
      message: `Allow atomic move_to_named_pose on ${input.arm_id} to ${input.destination} phase=${input.phase}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move_to_named_pose ${input.arm_id} -> ${input.destination} ${input.phase}`
  },
  renderToolResultMessage(output) {
    return `move_to_named_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'move_to_named_pose',
        [
          'arc.py',
          'arm',
          '--json',
          'move-named-pose',
          input.arm_id,
          '--destination',
          input.destination,
          '--phase',
          input.phase,
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
