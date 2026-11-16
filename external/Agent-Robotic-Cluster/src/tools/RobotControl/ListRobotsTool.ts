import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { LIST_ROBOTS_PROMPT } from './prompt.js'
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

export const ListRobotsTool = buildTool({
  name: 'list_robots',
  userFacingName: () => 'List Robots',
  searchHint: 'list configured robot arms and their connection status',
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
  async description() {
    return 'List all configured robot arms and connection status'
  },
  async prompt() {
    return LIST_ROBOTS_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'list_robots'
  },
  renderToolResultMessage(output) {
    return `list_robots: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(_input, context) {
    return {
      data: await executeRobotCommand(
        'list_robots',
        ['arc.py', 'arm', '--json', 'list'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
