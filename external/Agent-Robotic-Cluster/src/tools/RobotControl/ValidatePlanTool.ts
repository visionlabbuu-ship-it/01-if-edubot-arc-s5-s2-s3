import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { VALIDATE_PLAN_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  plan_json: z
    .string()
    .describe('Semantic action plan JSON. Actions may include move, grip, pick, drop, handoff, scan, scan_storage_slots, place_to_storage_slot, and get_state.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const ValidatePlanTool = buildTool({
  name: 'validate_plan',
  userFacingName: () => 'Validate Plan',
  searchHint: 'validate semantic robot plan ordering, pick/drop sequence completeness, zones, handoff ordering, and reachability; does not execute',
  maxResultSizeChars: 180_000,
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
  toAutoClassifierInput() {
    return 'semantic plan validation'
  },
  async description() {
    return 'Validate a semantic robot plan without executing it.'
  },
  async prompt() {
    return VALIDATE_PLAN_PROMPT
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
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'validate_plan'
  },
  renderToolResultMessage(output) {
    return `validate_plan: ${output.success ? 'ok' : 'failed'}`
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
        'validate_plan',
        ['arc.py', 'plan', '--json', 'validate', '--plan-json', input.plan_json],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
