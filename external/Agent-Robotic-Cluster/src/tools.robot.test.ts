import { afterEach, expect, test } from 'bun:test'
import { getEmptyToolPermissionContext } from './Tool.js'
import { getTools } from './tools.js'
import { ListRobotsTool } from './tools/RobotControl/ListRobotsTool.js'
import { MoveTool } from './tools/RobotControl/MoveTool.js'
import { ScanTool } from './tools/RobotControl/ScanTool.js'
import { ROBOT_CONTROL_SHARED_GUIDANCE } from './tools/RobotControl/prompt.js'

const originalSimpleEnv = process.env.CLAUDE_CODE_SIMPLE

afterEach(() => {
  process.env.CLAUDE_CODE_SIMPLE = originalSimpleEnv
})

test('simple mode returns lean robot tools instead of coding tools', () => {
  process.env.CLAUDE_CODE_SIMPLE = '1'

  const toolNames = getTools(getEmptyToolPermissionContext()).map(tool => tool.name)

  expect(toolNames).toContain('list_robots')
  expect(toolNames).toContain('get_state')
  expect(toolNames).toContain('scan')
  expect(toolNames).toContain('move')
  expect(toolNames).toContain('stop')
  expect(toolNames).toContain('recover')
  expect(toolNames).toContain('AskUserQuestion')
  expect(toolNames).toContain('TaskStop')
  expect(toolNames).not.toContain('Bash')
  expect(toolNames).not.toContain('Read')
  expect(toolNames).not.toContain('Edit')
})

test('default RobotControl tool prompts are short and not duplicated shared guidance', async () => {
  const promptOptions = {
    tools: [],
    agents: [],
    getToolPermissionContext: async () => getEmptyToolPermissionContext(),
  }

  const prompts = await Promise.all([
    ListRobotsTool.prompt(promptOptions),
    ScanTool.prompt(promptOptions),
    MoveTool.prompt(promptOptions),
  ])

  expect(new Set(prompts).size).toBe(prompts.length)
  for (const prompt of prompts) {
    expect(prompt.length).toBeLessThan(180)
    expect(prompt).not.toContain('Lean workflow contract')
    expect(prompt).not.toBe(ROBOT_CONTROL_SHARED_GUIDANCE)
  }
})
