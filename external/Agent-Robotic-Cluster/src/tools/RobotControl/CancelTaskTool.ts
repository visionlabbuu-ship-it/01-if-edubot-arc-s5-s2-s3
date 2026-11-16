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
  task_id: z.string().describe('Task id to cancel'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const CancelTaskTool = buildTool({
  name: 'cancel_task',
  userFacingName: () => 'Cancel Task',
  searchHint: 'request cancellation of a running ARC task',
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
    return input.task_id
  },
  async description(input) {
    return `Cancel task ${input.task_id}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow cancel_task for ${input.task_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `cancel_task ${input.task_id}`
  },
  renderToolResultMessage(output) {
    return `cancel_task: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'cancel_task',
        ['arc.py', 'task', '--json', 'cancel', input.task_id],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
