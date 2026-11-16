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
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const CameraStatusTool = buildTool({
  name: 'camera_status',
  userFacingName: () => 'Camera Status',
  searchHint: 'check whether the robot camera and calibration are ready',
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
  toAutoClassifierInput(input) {
    return input.camera_id ?? 'Cam1'
  },
  async description(input) {
    return `Check camera status for ${input.camera_id ?? 'Cam1'}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  renderToolUseMessage(input) {
    return `camera_status ${input.camera_id ?? 'Cam1'}`
  },
  renderToolResultMessage(output) {
    return `camera_status: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'camera_status',
        ['arc.py', 'camera', '--json', 'status', input.camera_id ?? 'Cam1'],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
