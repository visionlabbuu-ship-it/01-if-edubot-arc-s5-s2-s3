import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const GetWorkspaceStateTool = buildTool({
  name: 'get_workspace_state',
  userFacingName: () => 'Get Workspace State',
  searchHint:
    'read current arm poses classified into workspace/global coordinates, shared-zone occupancy, and standard park status',
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
    return true
  },
  toAutoClassifierInput() {
    return 'workspace state shared zone occupancy arm park pose'
  },
  async description() {
    return 'Read arm poses and classify shared-zone occupancy and standard park status.'
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'get_workspace_state'
  },
  renderToolResultMessage(output) {
    return `get_workspace_state: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(_input, context) {
    return {
      data: await executeRobotCommand(
        'get_workspace_state',
        ['arc.py', 'workspace', '--json', 'state'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
