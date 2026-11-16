import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { GRIP_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  state: z.enum(['on', 'off']).describe('Suction/tool state. on grips, off releases. No motion is hidden inside this tool.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const GripTool = buildTool({
  name: 'grip',
  userFacingName: () => 'Grip',
  searchHint: 'turn suction on or off; no motion; use after move surface and before move hover in pick/drop sequences',
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
    return `${input.arm_id} ${input.state}`
  },
  async description(input) {
    return `Set ${input.arm_id} suction/tool ${input.state}`
  },
  async prompt() {
    return GRIP_PROMPT
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow grip ${input.state} on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `grip ${input.arm_id} ${input.state}`
  },
  renderToolResultMessage(output) {
    return `grip ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
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
        'grip',
        [
          'arc.py',
          'arm',
          '--json',
          'tool-state',
          input.arm_id,
          '--enable',
          input.state,
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
