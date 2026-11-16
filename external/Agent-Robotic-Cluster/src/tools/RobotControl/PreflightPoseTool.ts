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
  check_collision: z
    .boolean()
    .optional()
    .describe('When true, query the other arm pose for shared-zone occupancy. Defaults to false for static reach-only preflight.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PreflightPoseTool = buildTool({
  name: 'preflight_pose',
  userFacingName: () => 'Preflight Pose',
  searchHint: 'check ARC guardrails for a target pose before requesting robot motion',
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
    return `${input.arm_id} ${input.x} ${input.y} ${input.z} ${input.r ?? 0}`
  },
  async description(input) {
    return `Preflight ${input.arm_id} pose x=${input.x}, y=${input.y}, z=${input.z}, r=${input.r ?? 0}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `preflight_pose ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `preflight_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'blocked'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args = [
      'arc.py',
      'arm',
      '--json',
      'preflight-pose',
      input.arm_id,
      '--x',
      String(input.x),
      '--y',
      String(input.y),
      '--z',
      String(input.z),
      '--r',
      String(input.r ?? 0),
    ]
    if (input.check_collision !== true) args.push('--skip-collision')

    return {
      data: await executeRobotCommand(
        'preflight_pose',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
