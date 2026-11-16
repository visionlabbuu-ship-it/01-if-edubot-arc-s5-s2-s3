import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { GET_STATE_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({})
type InputSchema = typeof inputSchema
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const GetStateTool = buildTool({
  name: 'get_state',
  userFacingName: () => 'Get State',
  searchHint:
    'read combined robot/workspace state: arm poses, shared-zone occupancy, parking classification, and holding estimate',
  maxResultSizeChars: 120_000,
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
    return 'robot state workspace state shared zone occupancy holding'
  },
  async description() {
    return 'Read combined robot and workspace state.'
  },
  async prompt() {
    return GET_STATE_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'get_state'
  },
  renderToolResultMessage(output) {
    return `get_state: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(_input, context) {
    return {
      data: await executeRobotCommand(
        'get_state',
        ['arc.py', 'workspace', '--json', 'state'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
