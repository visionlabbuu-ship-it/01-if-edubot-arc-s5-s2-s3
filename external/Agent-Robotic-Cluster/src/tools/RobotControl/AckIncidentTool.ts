import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ACK_INCIDENT_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  incidentActionSchema,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  action: incidentActionSchema.describe('Operator decision for the stored incident'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const AckIncidentTool = buildTool({
  name: 'ack_incident',
  userFacingName: () => 'Acknowledge Incident',
  searchHint: 'apply an operator decision to the latest stored robot incident',
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
    return `${input.arm_id} ${input.action}`
  },
  async description(input) {
    return `Acknowledge incident on ${input.arm_id} with action ${input.action}`
  },
  async prompt() {
    return ACK_INCIDENT_PROMPT
  },
  async validateInput(input) {
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow ack_incident ${input.action} on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `ack_incident ${input.action} ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `ack_incident ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const action = input.action.replaceAll('_', '-')
    return {
      data: await executeRobotCommand(
        'ack_incident',
        ['arc.py', 'arm', '--json', 'ack-incident', input.arm_id, action],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
