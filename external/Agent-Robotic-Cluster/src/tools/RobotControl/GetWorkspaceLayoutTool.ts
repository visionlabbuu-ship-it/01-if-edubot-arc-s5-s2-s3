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

export const GetWorkspaceLayoutTool = buildTool({
  name: 'get_workspace_layout',
  userFacingName: () => 'Get Workspace Layout',
  searchHint:
    'read canonical ARC workspace layout, arm identities, shared zone, park targets, and handoff contract',
  maxResultSizeChars: 100_000,
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
    return 'workspace layout shared zone handoff contract park targets'
  },
  async description() {
    return 'Read canonical ARC workspace layout, shared-zone geometry, park targets, and handoff contract.'
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'get_workspace_layout'
  },
  renderToolResultMessage(output) {
    return `get_workspace_layout: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(_input, context) {
    return {
      data: await executeRobotCommand(
        'get_workspace_layout',
        ['arc.py', 'workspace', '--json', 'layout'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
