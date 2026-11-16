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
  speed_l: z.number().min(1).max(100).optional().describe('Linear speed percentage 1-100'),
  acc_l: z.number().min(1).max(100).optional().describe('Linear acceleration percentage 1-100'),
  speed_j: z.number().min(1).max(100).optional().describe('Joint speed percentage 1-100'),
  acc_j: z.number().min(1).max(100).optional().describe('Joint acceleration percentage 1-100'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const SetMotionProfileTool = buildTool({
  name: 'set_motion_profile',
  userFacingName: () => 'Set Motion Profile',
  searchHint: 'set robot motion speed and acceleration profile independently from geometric distance',
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
    return `${input.arm_id} ${input.speed_l ?? ''} ${input.acc_l ?? ''} ${input.speed_j ?? ''} ${input.acc_j ?? ''}`.trim()
  },
  async description(input) {
    return `Set motion profile on ${input.arm_id}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput(input) {
    if (
      input.speed_l === undefined &&
      input.acc_l === undefined &&
      input.speed_j === undefined &&
      input.acc_j === undefined
    ) {
      return {
        result: false,
        message: 'set_motion_profile requires at least one of speed_l, acc_l, speed_j, or acc_j',
        errorCode: 1,
      }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow set_motion_profile on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `set_motion_profile ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `set_motion_profile ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args = ['arc.py', 'arm', '--json', 'motion-profile', input.arm_id]
    if (input.speed_l !== undefined) args.push('--speed-l', String(input.speed_l))
    if (input.acc_l !== undefined) args.push('--acc-l', String(input.acc_l))
    if (input.speed_j !== undefined) args.push('--speed-j', String(input.speed_j))
    if (input.acc_j !== undefined) args.push('--acc-j', String(input.acc_j))

    return {
      data: await executeRobotCommand(
        'set_motion_profile',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
