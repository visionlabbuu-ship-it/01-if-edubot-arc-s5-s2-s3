import { afterEach, expect, test } from 'bun:test'

// MACRO is replaced at build time by Bun.define but not in test mode.
// Define it globally so tests that import modules using MACRO don't crash.
;(globalThis as Record<string, unknown>).MACRO = {
  VERSION: '99.0.0',
  DISPLAY_VERSION: '0.0.0-test',
  BUILD_TIME: new Date().toISOString(),
  ISSUES_EXPLAINER: 'report the issue at https://github.com/Gitlawb/openclaude/issues',
  PACKAGE_URL: '@gitlawb/openclaude',
  NATIVE_PACKAGE_URL: undefined,
}

import { clearSystemPromptSections } from './systemPromptSections.js'
import { getSystemPrompt, DEFAULT_AGENT_PROMPT } from './prompts.js'
import { CLI_SYSPROMPT_PREFIXES, getCLISyspromptPrefix } from './system.js'
import { ListRobotsTool } from '../tools/RobotControl/ListRobotsTool.js'
import { MoveTool } from '../tools/RobotControl/MoveTool.js'
import { RobotArmTool } from '../tools/RobotArmTool/RobotArmTool.js'
import { CLAUDE_CODE_GUIDE_AGENT } from '../tools/AgentTool/built-in/claudeCodeGuideAgent.js'
import { GENERAL_PURPOSE_AGENT } from '../tools/AgentTool/built-in/generalPurposeAgent.js'
import { EXPLORE_AGENT } from '../tools/AgentTool/built-in/exploreAgent.js'
import { PLAN_AGENT } from '../tools/AgentTool/built-in/planAgent.js'
import { STATUSLINE_SETUP_AGENT } from '../tools/AgentTool/built-in/statuslineSetup.js'

const originalSimpleEnv = process.env.CLAUDE_CODE_SIMPLE

afterEach(() => {
  process.env.CLAUDE_CODE_SIMPLE = originalSimpleEnv
  clearSystemPromptSections()
})

test('CLI identity prefixes describe ARC instead of Claude Code', () => {
  expect(getCLISyspromptPrefix()).toContain('ARC')
  expect(getCLISyspromptPrefix()).toContain('robotics learning and control')
  expect(getCLISyspromptPrefix()).not.toContain('Claude Code')
  expect(getCLISyspromptPrefix()).not.toContain("Anthropic's official CLI for Claude")

  for (const prefix of CLI_SYSPROMPT_PREFIXES) {
    expect(prefix).toContain('ARC')
    expect(prefix).toContain('robot')
    expect(prefix).not.toContain('Claude Code')
    expect(prefix).not.toContain("Anthropic's official CLI for Claude")
  }
})

test('simple mode identity describes ARC instead of Claude Code', async () => {
  process.env.CLAUDE_CODE_SIMPLE = '1'

  const prompt = await getSystemPrompt([], 'gpt-4o')

  expect(prompt[0]).toContain('ARC')
  expect(prompt[0]).toContain('robotics learning and control')
  expect(prompt[0]).not.toContain('Claude Code')
  expect(prompt[0]).not.toContain("Anthropic's official CLI for Claude")
})

test('system prompt model identity updates when model changes mid-session', async () => {
  delete process.env.CLAUDE_CODE_SIMPLE
  clearSystemPromptSections()

  const firstPrompt = await getSystemPrompt([], 'old-test-model')
  const secondPrompt = await getSystemPrompt([], 'new-test-model')

  const firstText = firstPrompt.join('\n')
  const secondText = secondPrompt.join('\n')

  expect(firstText).toContain('You are powered by the model old-test-model.')
  expect(secondText).toContain('You are powered by the model new-test-model.')
  expect(secondText).not.toContain('You are powered by the model old-test-model.')
})

test('built-in agent prompts describe ARC instead of Claude Code', () => {
  expect(DEFAULT_AGENT_PROMPT).toContain('ARC')
  expect(DEFAULT_AGENT_PROMPT).toContain('robotics learning and control')
  expect(DEFAULT_AGENT_PROMPT).not.toContain('Claude Code')
  expect(DEFAULT_AGENT_PROMPT).not.toContain("Anthropic's official CLI for Claude")

  const generalPrompt = GENERAL_PURPOSE_AGENT.getSystemPrompt({
    toolUseContext: { options: {} as never },
  })
  expect(generalPrompt).toContain('ARC')
  expect(generalPrompt).toContain('robotics learning and control')
  expect(generalPrompt).not.toContain('Claude Code')
  expect(generalPrompt).not.toContain("Anthropic's official CLI for Claude")

  const explorePrompt = EXPLORE_AGENT.getSystemPrompt({
    toolUseContext: { options: {} as never },
  })
  expect(explorePrompt).toContain('ARC')
  expect(explorePrompt).not.toContain('Claude Code')
  expect(explorePrompt).not.toContain("Anthropic's official CLI for Claude")

  const planPrompt = PLAN_AGENT.getSystemPrompt({
    toolUseContext: { options: {} as never },
  })
  expect(planPrompt).toContain('ARC')
  expect(planPrompt).not.toContain('Claude Code')

  const statuslinePrompt = STATUSLINE_SETUP_AGENT.getSystemPrompt({
    toolUseContext: { options: {} as never },
  })
  expect(statuslinePrompt).toContain('ARC')
  expect(statuslinePrompt).not.toContain('Claude Code')

  const guidePrompt = CLAUDE_CODE_GUIDE_AGENT.getSystemPrompt({
    toolUseContext: {
      options: {
        commands: [],
        agentDefinitions: { activeAgents: [] },
        mcpClients: [],
      } as never,
    },
  })
  expect(guidePrompt).toContain('ARC')
  expect(guidePrompt).toContain('You are the ARC guide agent.')
  expect(guidePrompt).toContain('**ARC** (the CLI tool)')
  expect(guidePrompt).not.toContain('OpenClaude')
  expect(guidePrompt).not.toContain('You are the Claude guide agent.')
})

test('default robot tools get robot tool guidance without coding tool fallback', async () => {
  delete process.env.CLAUDE_CODE_SIMPLE
  clearSystemPromptSections()

  const prompt = await getSystemPrompt([ListRobotsTool, MoveTool], 'gpt-4o')
  const promptText = prompt.join('\n')

  expect(promptText).toContain('# Using your tools')
  expect(promptText).toContain("Prefer ARC's lean RobotControl tools")
  expect(promptText).toContain('RobotControl shared guidance')
  expect(promptText.match(/RobotControl shared guidance/g)?.length).toBe(1)
  expect(promptText).toContain('list_robots')
  expect(promptText).toContain('move')
  expect(promptText).not.toContain('To read files use')
  expect(promptText).not.toContain('To edit files use')
  expect(promptText).not.toContain(
    'Reserve using the Bash exclusively for system commands',
  )
})

test('legacy RobotArm guidance only appears when RobotArmTool is enabled', async () => {
  delete process.env.CLAUDE_CODE_SIMPLE
  clearSystemPromptSections()

  const defaultPrompt = (
    await getSystemPrompt([ListRobotsTool, MoveTool], 'gpt-4o')
  ).join('\n')
  expect(defaultPrompt).not.toContain('RobotArm is a legacy fallback')

  clearSystemPromptSections()
  const developerPrompt = (
    await getSystemPrompt([ListRobotsTool, MoveTool, RobotArmTool], 'gpt-4o')
  ).join('\n')
  expect(developerPrompt).toContain('RobotArm is a legacy fallback')
})
