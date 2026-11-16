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
  destination: z
    .string()
    .describe('Named destination such as storage_zone, drop_zone, or home'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PlaceAtNamedTool = buildTool({
  name: 'place_at_named',
  userFacingName: () => 'Place At Named Target',
  searchHint:
    'primitive place: approach→descend→suction-off→depart at a named destination. Use after pick_at_pose to drop the carried object.',
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
    return `${input.arm_id} ${input.destination}`
  },
  async description(input) {
    return `Primitive place with ${input.arm_id} at ${input.destination}. Runs approach→descend→suction-off→depart using the destination's configured z profile.`
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
      message: `Allow ${input.arm_id} to place at ${input.destination}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `place_at_named ${input.arm_id} -> ${input.destination}`
  },
  renderToolResultMessage(output) {
    return `place_at_named ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'place_at_named',
        ['arc.py', 'arm', '--json', 'place-at-named', input.arm_id, '--destination', input.destination],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
