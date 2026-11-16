import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { EXECUTE_PLAN_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  plan_json: z
    .string()
    .describe('Semantic action plan JSON. It is validated before execution.'),
  mode: z.enum(['inline', 'background']).optional().describe('Run inline or start in background'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const ExecutePlanTool = buildTool({
  name: 'execute_plan',
  userFacingName: () => 'Execute Plan',
  searchHint: 'execute a semantic robot plan step-by-step with logging, cancellation, and status support',
  maxResultSizeChars: 220_000,
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
    return input.mode ?? 'inline'
  },
  async description(input) {
    return `Execute semantic robot plan in ${input.mode ?? 'inline'} mode`
  },
  async prompt() {
    return EXECUTE_PLAN_PROMPT
  },
  async validateInput(input) {
    try {
      JSON.parse(input.plan_json)
    } catch {
      return { result: false, message: 'plan_json must be valid JSON', errorCode: 1 }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow execute_plan in ${input.mode ?? 'inline'} mode?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `execute_plan ${input.mode ?? 'inline'}`
  },
  renderToolResultMessage(output) {
    return `execute_plan: ${output.success ? 'ok' : 'failed'}`
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
        'execute_plan',
        [
          'arc.py',
          'plan',
          '--json',
          'execute',
          '--mode',
          input.mode ?? 'inline',
          '--plan-json',
          input.plan_json,
        ],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
