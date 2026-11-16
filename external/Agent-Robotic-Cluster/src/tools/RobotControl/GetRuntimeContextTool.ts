import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { GET_RUNTIME_CONTEXT_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({})
type InputSchema = typeof inputSchema
type Output = z.infer<RobotCommandOutputSchema>

export const GetRuntimeContextTool = buildTool({
  name: 'get_runtime_context',
  userFacingName: () => 'Get Runtime Context',
  searchHint:
    'compact read-only readiness context for active arms transforms workspace Storage summary and scan contracts',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): RobotCommandOutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput() {
    return 'runtime readiness active arms transforms workspace storage summary'
  },
  async description() {
    return 'Read compact calibrated runtime readiness and Storage summary without scanning or moving hardware.'
  },
  async prompt() {
    return GET_RUNTIME_CONTEXT_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'get_runtime_context'
  },
  renderToolResultMessage(output) {
    return `get_runtime_context: ${output.success ? 'ok' : 'failed'}`
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
        'get_runtime_context',
        ['arc.py', 'workspace', '--json', 'context'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
