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
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  x: z.number().describe('Target x in arm-local frame (mm). For cross-arm scenarios use per_arm_robot_plane[arm].x from camera_scan.'),
  y: z.number().describe('Target y in arm-local XY plane (mm), not vertical Z. For cross-arm scenarios use per_arm_robot_plane[arm].y from camera_scan.'),
  r: z.number().optional().describe('Tool rotation in degrees (default 0)'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PickAtPoseTool = buildTool({
  name: 'pick_at_pose',
  userFacingName: () => 'Pick At Pose',
  searchHint:
    'primitive grasp: approach→descend→suction-on→lift at an explicit (x,y) pose. No reach/color filtering; the AI chooses the arm and pose. Use when pick_detected_place is too restrictive, or for cross-arm pickups.',
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
    return false
  },
  toAutoClassifierInput(input) {
    return `${input.arm_id} ${input.x} ${input.y}`
  },
  async description(input) {
    return `Primitive pick with ${input.arm_id} at (x=${input.x}, y=${input.y}). Runs approach→descend→suction-on→lift using the arm's configured pick z profile. No reach or color filtering.`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow ${input.arm_id} to run primitive pick-at-pose at (${input.x}, ${input.y})?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `pick_at_pose ${input.arm_id} (${input.x}, ${input.y})`
  },
  renderToolResultMessage(output) {
    return `pick_at_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args: string[] = [
      'arc.py',
      'arm',
      '--json',
      'pick-at',
      input.arm_id,
      '--x',
      String(input.x),
      '--y',
      String(input.y),
    ]
    if (input.r !== undefined) {
      args.push('--r', String(input.r))
    }
    return {
      data: await executeRobotCommand(
        'pick_at_pose',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
