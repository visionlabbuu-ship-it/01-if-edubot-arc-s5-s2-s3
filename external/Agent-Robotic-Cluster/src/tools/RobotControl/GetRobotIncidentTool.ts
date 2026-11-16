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
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const GetRobotIncidentTool = buildTool({
  name: 'get_robot_incident',
  userFacingName: () => 'Get Robot Incident',
  searchHint: 'inspect the latest stored incident for a robot arm',
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
    return input.arm_id
  },
  async description(input) {
    return `Get latest stored incident for ${input.arm_id}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `get_robot_incident ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `get_robot_incident ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'get_robot_incident',
        ['arc.py', 'arm', '--json', 'incident', input.arm_id],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
