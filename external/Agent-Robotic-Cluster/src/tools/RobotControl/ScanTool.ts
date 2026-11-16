import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { SCAN_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  camera_id: z.string().optional().describe('Camera identifier such as Cam1'),
  color: z.enum(['any', 'red', 'green', 'blue', 'unknown']).optional().describe('Optional color filter'),
  selection: z
    .enum(['first', 'largest', 'leftmost', 'rightmost', 'topmost', 'bottommost'])
    .optional()
    .describe('Selection strategy for selected_object'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const ScanTool = buildTool({
  name: 'scan',
  userFacingName: () => 'Scan',
  searchHint: 'scan workspace; returns objects with position, color, block, reachability, and per-arm coordinates; no motion',
  maxResultSizeChars: 20_000,
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
  toAutoClassifierInput(input) {
    return `${input.camera_id ?? 'Cam1'} ${input.color ?? 'any'} ${input.selection ?? 'first'}`
  },
  async description(input) {
    return `Scan ${input.camera_id ?? 'Cam1'} for ${input.color ?? 'any'} objects`
  },
  async prompt() {
    return SCAN_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `scan ${input.camera_id ?? 'Cam1'}`
  },
  renderToolResultMessage(output) {
    return `scan: ${output.success ? 'ok' : 'failed'}`
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
        'scan',
        [
          'arc.py',
          'camera',
          '--json',
          'scan',
          input.camera_id ?? 'Cam1',
          '--color',
          input.color ?? 'any',
          '--selection',
          input.selection ?? 'first',
        ],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
