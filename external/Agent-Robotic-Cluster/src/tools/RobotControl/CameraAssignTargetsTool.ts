import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { jsonStringify } from '../../utils/slowOperations.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  camera_id: z.string().optional().describe('Camera identifier such as Cam1'),
  assignments: z
    .array(
      z.strictObject({
        arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
        color: z.enum(['any', 'red', 'green', 'blue', 'unknown']),
      }),
    )
    .describe('Arm/color assignments to satisfy from the current camera scan'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const CameraAssignTargetsTool = buildTool({
  name: 'camera_assign_targets',
  userFacingName: () => 'Camera Assign Targets',
  searchHint: 'assign detected colored objects to robot arms based on reachability',
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
  toAutoClassifierInput(input) {
    return jsonStringify(input.assignments)
  },
  async description(input) {
    return `Assign detected targets for ${input.assignments.length} arm requests`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  renderToolUseMessage(_input) {
    return 'camera_assign_targets'
  },
  renderToolResultMessage(output) {
    return `camera_assign_targets: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'camera_assign_targets',
        [
          'arc.py',
          'camera',
          '--json',
          'assign-targets',
          input.camera_id ?? 'Cam1',
          '--assignments',
          JSON.stringify(input.assignments),
        ],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
