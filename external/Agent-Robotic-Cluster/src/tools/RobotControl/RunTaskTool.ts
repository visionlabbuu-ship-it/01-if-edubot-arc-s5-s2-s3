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
  mode: z
    .enum(['inline', 'background'])
    .optional()
    .describe('Run inline and wait for completion, or start in background'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const RunTaskTool = buildTool({
  name: 'run_task',
  userFacingName: () => 'Run Task',
  searchHint: 'run multi-step and multi-arm robot tasks with sequence parallel and delay support',
  maxResultSizeChars: 180_000,
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
    return input.mode ?? 'inline'
  },
  async description(input) {
    return `Run robot task in ${input.mode ?? 'inline'} mode`
  },
  async prompt() {
    return `${ROBOT_CONTROL_DESCRIPTION}

Use run_task for multi-arm missions, delays, and coordinated sequences. Prefer run_task over issuing many single robot moves when the user asks for two arms to move together or with timing offsets.

Task schema rules:
- The top-level object must contain a non-empty "steps" array.
- Use "sequence" and "parallel" only as grouping nodes.
- Use action steps such as "motion_profile", "move_relative", "move_to_pose", "move_joint_pose", "run_path", "delay", "stop_robot", "recover_robot", and "recover_home_robot".
- Keep geometry separate from speed. Distance belongs inside move/path params. Speed and acceleration belong in "motion_profile" steps.

Example: same distance, different speeds by 50%
{
  "name": "same-distance-different-speeds",
  "steps": [
    {
      "type": "parallel",
      "steps": [
        {
          "type": "sequence",
          "steps": [
            { "type": "motion_profile", "arm_id": "Arm1", "params": { "speed_l": 80, "acc_l": 80 } },
            { "type": "move_relative", "arm_id": "Arm1", "params": { "dx": 40, "dy": 0, "dz": 0, "dr": 0 } }
          ]
        },
        {
          "type": "sequence",
          "steps": [
            { "type": "motion_profile", "arm_id": "Arm2", "params": { "speed_l": 40, "acc_l": 40 } },
            { "type": "move_relative", "arm_id": "Arm2", "params": { "dx": 40, "dy": 0, "dz": 0, "dr": 0 } }
          ]
        }
      ]
    }
  ]
}`
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
    return {
      behavior: 'ask',
      message: `Allow run_task in ${input.mode ?? 'inline'} mode?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `run_task ${input.mode ?? 'inline'}`
  },
  renderToolResultMessage(output) {
    return `run_task: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const mode = input.mode ?? 'inline'
    const args =
      mode === 'background'
        ? ['arc.py', 'task', '--json', 'start', '--task-json', input.task_json]
        : ['arc.py', 'task', '--json', 'run', '--task-json', input.task_json]

    return {
      data: await executeRobotCommand('run_task', args, context.abortController.signal),
    }
  },
} satisfies ToolDef<InputSchema, Output>)
