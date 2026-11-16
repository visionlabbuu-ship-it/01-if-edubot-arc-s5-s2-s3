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
  x: z.number().describe('Target X in mm'),
  y: z.number().describe('Target Y in mm'),
  z: z.number().describe('Target Z in mm'),
  r: z.number().optional().describe('Target R rotation'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const MoveToPoseTool = buildTool({
  name: 'move_to_pose',
  userFacingName: () => 'Move To Pose',
  searchHint: 'move robot to an explicit pose with monitored completion',
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
    return `${input.arm_id} ${input.x} ${input.y} ${input.z} ${input.r ?? 0}`
  },
  async description(input) {
    return `Move ${input.arm_id} to pose x=${input.x}, y=${input.y}, z=${input.z}, r=${input.r ?? 0}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput(input) {
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow move_to_pose on ${input.arm_id} to x=${input.x} y=${input.y} z=${input.z} r=${input.r ?? 0}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move_to_pose ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `move_to_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'move_to_pose',
        [
          'arc.py',
          'arm',
          '--json',
          'move-to-pose',
          input.arm_id,
          '--x',
          String(input.x),
          '--y',
          String(input.y),
          '--z',
          String(input.z),
          '--r',
          String(input.r ?? 0),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
