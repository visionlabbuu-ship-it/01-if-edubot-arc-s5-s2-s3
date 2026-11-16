import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { RECOVER_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const RecoverTool = buildTool({
  name: 'recover',
  userFacingName: () => 'Recover',
  searchHint: 'recovery/safety tool: clear robot errors and re-enable the arm after operator decision',
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
    return input.arm_id
  },
  async description(input) {
    return `Recover ${input.arm_id}`
  },
  async prompt() {
    return RECOVER_PROMPT
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow recover on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `recover ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `recover ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
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
        'recover',
        ['arc.py', 'arm', '--json', 'recover', input.arm_id],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
