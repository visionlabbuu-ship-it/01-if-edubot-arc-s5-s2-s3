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
  x: z.number().describe('Target X in arm-local frame (mm)'),
  y: z.number().describe('Target Y in arm-local frame (mm)'),
  phase: z
    .enum(['hover', 'surface'])
    .describe('Configured Z phase: hover above workspace, or surface at pick/place height'),
  r: z.number().optional().describe('Tool rotation in degrees (default 0)'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const MoveToWorkPoseTool = buildTool({
  name: 'move_to_work_pose',
  userFacingName: () => 'Move To Work Pose',
  searchHint:
    'atomic motion to explicit x/y at configured hover or surface Z; no suction toggle; use for visible pick/place/handoff steps',
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
    return `${input.arm_id} ${input.x} ${input.y} ${input.phase}`
  },
  async description(input) {
    return `Move ${input.arm_id} to work pose x=${input.x}, y=${input.y}, phase=${input.phase}. Atomic motion only; does not toggle suction.`
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
      message: `Allow atomic move_to_work_pose on ${input.arm_id} to x=${input.x} y=${input.y} phase=${input.phase}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move_to_work_pose ${input.arm_id} ${input.phase}`
  },
  renderToolResultMessage(output) {
    return `move_to_work_pose ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'move_to_work_pose',
        [
          'arc.py',
          'arm',
          '--json',
          'move-work-pose',
          input.arm_id,
          '--x',
          String(input.x),
          '--y',
          String(input.y),
          '--phase',
          input.phase,
          '--r',
          String(input.r ?? 0),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
