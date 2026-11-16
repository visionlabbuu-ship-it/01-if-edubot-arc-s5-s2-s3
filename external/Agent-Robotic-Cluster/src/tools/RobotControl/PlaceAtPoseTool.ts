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
  x: z.number().describe('Place x in arm-local frame (mm)'),
  y: z.number().describe('Place y in arm-local frame (mm)'),
  r: z.number().optional().describe('Tool rotation in degrees (default 0)'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PlaceAtPoseTool = buildTool({
  name: 'place_at_pose',
  userFacingName: () => 'Place At Pose',
  searchHint:
    'primitive place at an explicit (x,y) using the arm\'s pick z as the drop height (same height objects sat at during pick). Use when the user wants to drop at a free pose rather than a named destination.',
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
    return `Primitive place with ${input.arm_id} at (x=${input.x}, y=${input.y}). Runs approach→descend→suction-off→lift using the arm's pick z as the place height.`
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
      message: `Allow ${input.arm_id} to place at (${input.x}, ${input.y})?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `place_at_pose ${input.arm_id} (${input.x}, ${input.y})`
  },
  renderToolResultMessage(output) {
    return `place_at_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args: string[] = [
      'arc.py',
      'arm',
      '--json',
      'place-at-pose',
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
        'place_at_pose',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
