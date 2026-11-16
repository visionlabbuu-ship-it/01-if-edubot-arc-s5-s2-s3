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
  dx: z.number().describe('Relative X offset in mm'),
  dy: z.number().describe('Relative Y offset in mm'),
  dz: z.number().describe('Relative Z offset in mm'),
  dr: z.number().optional().describe('Relative R rotation'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const MoveRelativeTool = buildTool({
  name: 'move_relative',
  userFacingName: () => 'Move Relative',
  searchHint: 'move robot by explicit relative offsets with monitored completion',
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
    return `${input.arm_id} ${input.dx} ${input.dy} ${input.dz} ${input.dr ?? 0}`
  },
  async description(input) {
    return `Move ${input.arm_id} relative by dx=${input.dx}, dy=${input.dy}, dz=${input.dz}, dr=${input.dr ?? 0}`
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
      message: `Allow move_relative on ${input.arm_id} with dx=${input.dx} dy=${input.dy} dz=${input.dz} dr=${input.dr ?? 0}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move_relative ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `move_relative ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'move_relative',
        [
          'arc.py',
          'arm',
          '--json',
          'move-relative',
          input.arm_id,
          '--dx',
          String(input.dx),
          '--dy',
          String(input.dy),
          '--dz',
          String(input.dz),
          '--dr',
          String(input.dr ?? 0),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
