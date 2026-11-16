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
    .describe('Named destination such as home or drop_zone'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const MoveToNamedTargetTool = buildTool({
  name: 'move_to_named_target',
  userFacingName: () => 'Move To Named Target',
  searchHint: 'move a robot arm to a named destination like home without picking or placing',
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
    return `${input.arm_id} ${input.destination}`
  },
  async description(input) {
    return `Move ${input.arm_id} to named destination ${input.destination} without picking`
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
      message: `Allow moving ${input.arm_id} to named destination ${input.destination}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move_to_named_target ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `move_to_named_target ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'move_to_named_target',
        [
          'arc.py',
          'arm',
          '--json',
          'move-to-named-target',
          input.arm_id,
          '--destination',
          input.destination,
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
