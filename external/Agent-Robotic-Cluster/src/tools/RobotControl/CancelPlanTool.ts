import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { CANCEL_PLAN_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  plan_id: z.string().describe('Plan id returned by execute_plan background mode'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const CancelPlanTool = buildTool({
  name: 'cancel_plan',
  userFacingName: () => 'Cancel Plan',
  searchHint: 'recovery/safety tool: request cancellation of a running semantic plan',
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
    return input.plan_id
  },
  async description(input) {
    return `Cancel semantic plan ${input.plan_id}`
  },
  async prompt() {
    return CANCEL_PLAN_PROMPT
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow cancel_plan for ${input.plan_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `cancel_plan ${input.plan_id}`
  },
  renderToolResultMessage(output) {
    return `cancel_plan: ${output.success ? 'ok' : 'failed'}`
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
        'cancel_plan',
        ['arc.py', 'plan', '--json', 'cancel', input.plan_id],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
