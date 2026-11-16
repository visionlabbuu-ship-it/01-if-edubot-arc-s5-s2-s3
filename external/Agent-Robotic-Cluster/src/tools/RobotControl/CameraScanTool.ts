import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  camera_id: z.string().optional().describe('Camera identifier such as Cam1'),
  color: z
    .enum(['any', 'red', 'green', 'blue', 'unknown'])
    .optional()
    .describe('Optional color filter'),
  selection: z
    .enum(['first', 'largest', 'leftmost', 'rightmost', 'topmost', 'bottommost'])
    .optional()
    .describe('Selection strategy for selected_object'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const CameraScanTool = buildTool({
  name: 'camera_scan',
  userFacingName: () => 'Camera Scan',
  searchHint: 'scan the robot workspace for objects and reachability',
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
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  renderToolUseMessage(input) {
    return `camera_scan ${input.camera_id ?? 'Cam1'}`
  },
  renderToolResultMessage(output) {
    return `camera_scan: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'camera_scan',
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
