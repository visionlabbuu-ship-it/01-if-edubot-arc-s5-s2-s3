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
  task_json: z
    .string()
    .describe('Task JSON string using ARC task schema with sequence/parallel/delay steps'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PreflightTaskTool = buildTool({
  name: 'preflight_task',
  userFacingName: () => 'Preflight Task',
  searchHint: 'statically check ARC guardrails for a robot task before run_task',
  maxResultSizeChars: 120_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): OutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput(input) {
    return input.task_json
  },
  async description() {
    return 'Preflight robot task without moving hardware'
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput(input) {
    let parsed: unknown
    try {
      parsed = JSON.parse(input.task_json)
    } catch {
      return {
        result: false,
        message: 'task_json must be valid JSON',
        errorCode: 1,
      }
    }

    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      !('steps' in parsed) ||
      !Array.isArray((parsed as { steps?: unknown }).steps) ||
      (parsed as { steps: unknown[] }).steps.length === 0
    ) {
      return {
        result: false,
        message: 'task_json must contain a non-empty top-level steps array',
        errorCode: 2,
      }
    }

    return { result: true }
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'preflight_task'
  },
  renderToolResultMessage(output) {
    return `preflight_task: ${output.success ? 'ok' : 'blocked'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'preflight_task',
        ['arc.py', 'task', '--json', 'preflight', '--task-json', input.task_json],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
