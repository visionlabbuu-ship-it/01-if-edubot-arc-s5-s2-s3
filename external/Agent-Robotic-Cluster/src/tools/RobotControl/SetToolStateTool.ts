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
  enable: z.boolean().describe('True to turn suction/tool on, false to turn it off'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const SetToolStateTool = buildTool({
  name: 'set_tool_state',
  userFacingName: () => 'Set Tool State',
  searchHint: 'turn robot suction or tool output on or off directly',
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
    return `${input.arm_id} ${input.enable ? 'on' : 'off'}`
  },
  async description(input) {
    return `Set tool state on ${input.arm_id} to ${input.enable ? 'on' : 'off'}`
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
      message: `Allow setting ${input.arm_id} suction/tool ${input.enable ? 'on' : 'off'}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `set_tool_state ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `set_tool_state ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'set_tool_state',
        [
          'arc.py',
          'arm',
          '--json',
          'tool-state',
          input.arm_id,
          '--enable',
          input.enable ? 'on' : 'off',
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
