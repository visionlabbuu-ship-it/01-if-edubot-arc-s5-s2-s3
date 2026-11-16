import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { GET_PLAN_STATUS_PROMPT } from './prompt.js'
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

export const GetPlanStatusTool = buildTool({
  name: 'get_plan_status',
  userFacingName: () => 'Get Plan Status',
  searchHint: 'read semantic plan status, current step, events, incidents, and execution result',
  maxResultSizeChars: 160_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): OutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput(input) {
    return input.plan_id
  },
  async description(input) {
    return `Get semantic plan status for ${input.plan_id}`
  },
  async prompt() {
    return GET_PLAN_STATUS_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `get_plan_status ${input.plan_id}`
  },
  renderToolResultMessage(output) {
    return `get_plan_status: ${output.success ? 'ok' : 'failed'}`
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
        'get_plan_status',
        ['arc.py', 'plan', '--json', 'status', input.plan_id],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
