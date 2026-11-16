import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { STOP_PROMPT } from './prompt.js'
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

export const StopTool = buildTool({
  name: 'stop',
  userFacingName: () => 'Stop',
  searchHint: 'recovery/safety tool: stop robot motion immediately',
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
    return `Stop motion on ${input.arm_id}`
  },
  async prompt() {
    return STOP_PROMPT
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow stop on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `stop ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `stop ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
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
        'stop',
        ['arc.py', 'arm', '--json', 'stop', input.arm_id],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
