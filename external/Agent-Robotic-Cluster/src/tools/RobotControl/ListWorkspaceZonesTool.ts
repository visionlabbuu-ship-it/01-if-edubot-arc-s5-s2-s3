import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import { modelFacingRobotResult, executeRobotCommand, robotCommandOutputSchema, type RobotCommandOutputSchema } from './shared.js'

const inputSchema = z.strictObject({})
type InputSchema = typeof inputSchema
type Output = z.infer<RobotCommandOutputSchema>

export const ListWorkspaceZonesTool = buildTool({
  name: 'list_workspace_zones',
  userFacingName: () => 'List Workspace Zones',
  searchHint: 'list calibrated outer box and named work zones such as A1-A8',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema { return inputSchema },
  get outputSchema(): RobotCommandOutputSchema { return robotCommandOutputSchema() },
  isConcurrencySafe() { return true },
  isReadOnly() { return true },
  toAutoClassifierInput() { return 'calibrated workspace zones A1 A2 outer box' },
  async description() { return 'List the calibrated Outer Box and named work-zone polygons.' },
  async prompt() { return ROBOT_CONTROL_DESCRIPTION },
  async checkPermissions(input) { return { behavior: 'allow', updatedInput: input } },
  renderToolUseMessage() { return 'list_workspace_zones' },
  renderToolResultMessage(output) { return `list_workspace_zones: ${output.success ? 'ok' : 'failed'}` },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(_input, context) {
    return { data: await executeRobotCommand('list_workspace_zones', ['arc.py', 'workspace', '--json', 'zones'], context.abortController.signal) }
  },
} satisfies ToolDef<InputSchema, Output>)
