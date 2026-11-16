import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import { modelFacingRobotResult, executeRobotCommand, robotCommandOutputSchema, type RobotCommandOutputSchema } from './shared.js'

const inputSchema = z.strictObject({
  destination: z.string().describe('Work zone, Storage group, or Storage slot, for example A4, LT, or RT3'),
  source_zone: z.string().optional().describe('Optional calibrated source work zone such as A1'),
  color: z.enum(['any', 'red', 'green', 'blue', 'unknown']).default('any'),
  robot_id: z.enum(['Arm1', 'Arm2']).optional(),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type Output = z.infer<RobotCommandOutputSchema>

export const PlanCalibratedIntentTool = buildTool({
  name: 'plan_calibrated_intent',
  userFacingName: () => 'Plan Calibrated Intent',
  searchHint: 'dry run zone object storage slot arm selection reachability handoff',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema { return inputSchema },
  get outputSchema(): RobotCommandOutputSchema { return robotCommandOutputSchema() },
  isConcurrencySafe() { return false },
  isReadOnly() { return true },
  toAutoClassifierInput() { return 'plan calibrated source zone destination storage handoff' },
  async description() { return 'Scan and dry-run a calibrated source-to-destination plan without moving hardware.' },
  async prompt() { return ROBOT_CONTROL_DESCRIPTION },
  async checkPermissions(input) { return { behavior: 'allow', updatedInput: input } },
  renderToolUseMessage(input) { return `plan_calibrated_intent ${input.source_zone ?? '*'} -> ${input.destination}` },
  renderToolResultMessage(output) { return `plan_calibrated_intent: ${output.success ? 'ok' : 'blocked'}` },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input: Input, context) {
    const args = ['arc.py', 'workspace', '--json', 'plan-intent', '--destination', input.destination, '--color', input.color]
    if (input.source_zone) args.push('--source-zone', input.source_zone)
    if (input.robot_id) args.push('--robot-id', input.robot_id)
    return { data: await executeRobotCommand('plan_calibrated_intent', args, context.abortController.signal, input.robot_id) }
  },
} satisfies ToolDef<InputSchema, Output>)
