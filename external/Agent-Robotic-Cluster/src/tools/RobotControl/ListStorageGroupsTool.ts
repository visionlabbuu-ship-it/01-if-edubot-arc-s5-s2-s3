import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import { modelFacingRobotResult, executeRobotCommand, robotCommandOutputSchema, type RobotCommandOutputSchema } from './shared.js'

const inputSchema = z.strictObject({})
type InputSchema = typeof inputSchema
type Output = z.infer<RobotCommandOutputSchema>

export const ListStorageGroupsTool = buildTool({
  name: 'list_storage_groups',
  userFacingName: () => 'List Storage Groups',
  searchHint: 'list calibrated LT LB RT RB Storage regions and readiness',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema { return inputSchema },
  get outputSchema(): RobotCommandOutputSchema { return robotCommandOutputSchema() },
  isConcurrencySafe() { return true },
  isReadOnly() { return true },
  toAutoClassifierInput() { return 'storage groups LT LB RT RB slots' },
  async description() { return 'List calibrated Storage groups LT/LB/RT/RB and their physical regions.' },
  async prompt() { return ROBOT_CONTROL_DESCRIPTION },
  async checkPermissions(input) { return { behavior: 'allow', updatedInput: input } },
  renderToolUseMessage() { return 'list_storage_groups' },
  renderToolResultMessage(output) { return `list_storage_groups: ${output.success ? 'ok' : 'failed'}` },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(_input, context) {
    return { data: await executeRobotCommand('list_storage_groups', ['arc.py', 'workspace', '--json', 'storage-groups'], context.abortController.signal) }
  },
} satisfies ToolDef<InputSchema, Output>)
