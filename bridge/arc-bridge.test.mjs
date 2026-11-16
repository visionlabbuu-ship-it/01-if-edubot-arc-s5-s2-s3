import test from 'node:test'
import assert from 'node:assert/strict'
import { __bridgeTestUtils } from './arc-bridge.mjs'

test('buildPromptParts separates system instructions from transcript', () => {
  const { systemPrompt, prompt } = __bridgeTestUtils.buildPromptParts([
    { role: 'system', content: 'Use short answers.' },
    { role: 'user', content: 'Check robot state.' },
  ])

  assert.match(systemPrompt, /Use short answers\./)
  assert.equal(prompt, 'User: Check robot state.')
})

test('resume prompt keeps only the latest conversational turns', () => {
  const { prompt } = __bridgeTestUtils.buildPromptParts(
    [
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'two' },
      { role: 'user', content: 'three' },
    ],
    { resumeSession: true },
  )

  assert.equal(prompt, 'Assistant: two\n\nUser: three')
})

test('extracts stream text deltas and merges final assistant text', () => {
  let text = ''
  text = __bridgeTestUtils.mergeAssistantText(text, {
    type: 'stream_event',
    event: {
      type: 'content_block_delta',
      delta: { type: 'text_delta', text: 'hello' },
    },
  })
  text = __bridgeTestUtils.mergeAssistantText(text, {
    type: 'assistant',
    message: {
      role: 'assistant',
      content: [{ type: 'text', text: 'hello world' }],
    },
  })

  assert.equal(text, 'hello world')
})

test('merges cumulative stream text snapshots without duplicating visible text', () => {
  let text = ''
  text = __bridgeTestUtils.mergeAssistantText(text, {
    type: 'stream_event',
    event: {
      type: 'content_block_delta',
      delta: { type: 'text_delta', text: 'hello' },
    },
  })
  text = __bridgeTestUtils.mergeAssistantText(text, {
    type: 'stream_event',
    event: {
      type: 'content_block_delta',
      delta: { type: 'text_delta', text: 'hello world' },
    },
  })
  text = __bridgeTestUtils.mergeAssistantText(text, {
    type: 'stream_event',
    event: {
      type: 'content_block_delta',
      delta: { type: 'text_delta', text: 'hello world' },
    },
  })

  assert.equal(text, 'hello world')
})

test('replaces highly overlapping final assistant snapshots instead of appending them', () => {
  const previousText =
    'ผมเป็น ARC (Agentic Robot Controller) ครับ เป็น AI agent ที่ออกแบบมาเพื่อช่วยควบคุมและจัดการระบบหุ่นยนต์ โดยเฉพาะกับ MG40 robot arms ผ่าน ARC runtimeพูดง่ายๆ คือผมเป็น assistant สำหรับทำงานกับหุ่นยนต์ครับ มีเครื่องมือสำหรับสแกน ตรวจสอบสถานะ และสั่งการหุ่นยนต์ครับมีอะไรให้ช่วยเรื่องหุ่นยนต์ไหมครับ?'
  const finalText =
    'ผมเป็น ARC (Agentic Robot Controller) ครับ เป็น AI agent ที่ออกแบบมาเพื่อช่วยควบคุมและจัดการระบบหุ่นยนต์ โดยเฉพาะกับ MG400 robot arms ผ่าน ARC runtime\n\nพูดง่ายๆ คือผมเป็น assistant สำหรับทำงานกับหุ่นยนต์ครับ มีเครื่องมือสำหรับสแกน ตรวจสอบสถานะ และสั่งการหุ่นยนต์ครับ\n\nมีอะไรให้ช่วยเรื่องหุ่นยนต์ไหมครับ?'

  const update = __bridgeTestUtils.createAssistantTextUpdate(previousText, {
    type: 'assistant',
    message: {
      role: 'assistant',
      content: [{ type: 'text', text: finalText }],
    },
  })

  assert.equal(update.text, finalText)
  assert.equal(update.delta, '')
  assert.equal(update.replaceText, finalText)
})

test('extracts ARC tool use blocks for bridge.tool events', () => {
  const toolUses = __bridgeTestUtils.extractToolUse({
    type: 'assistant',
    message: {
      role: 'assistant',
      content: [
        { type: 'text', text: 'I will scan first.' },
        { type: 'tool_use', id: 'toolu_1', name: 'camera_scan', input: { camera_id: 'Cam1' } },
      ],
    },
  })

  assert.deepEqual(toolUses, [
    {
      id: 'toolu_1',
      name: 'camera_scan',
      input: { camera_id: 'Cam1' },
    },
  ])
})

test('spawn args enable stream-json stdin permission prompts', () => {
  const args = __bridgeTestUtils.createSpawnArgs({
    prompt: 'Move the blue objects.',
    model: 'Qwen3.6',
    provider: 'openai',
    sessionId: '71f8d30c-c2db-44d2-9548-53ebc304ddae',
    systemPrompt: 'Use robot tools safely.',
  })

  assert.deepEqual(args.slice(0, 8), [
    '-p',
    '--verbose',
    '--output-format',
    'stream-json',
    '--input-format',
    'stream-json',
    '--include-partial-messages',
    '--permission-prompt-tool',
  ])
  assert.equal(args.includes('stdio'), true)
  assert.equal(args.includes('Move the blue objects.'), false)
  assert.equal(args.includes('--permission-mode'), true)
  assert.equal(args.includes(__bridgeTestUtils.DEFAULT_PERMISSION_MODE), true)
  assert.equal(args.includes('--provider'), true)
  assert.equal(args.includes('openai'), true)
  if (__bridgeTestUtils.BYPASS_PERMISSIONS) {
    assert.equal(args.includes('--allow-dangerously-skip-permissions'), true)
    assert.equal(args.includes('--dangerously-skip-permissions'), true)
  }
})

test('session remint helpers detect already-in-use errors', () => {
  assert.equal(
    __bridgeTestUtils.shouldRemintSession(
      'Error: Session ID 56603bd7-ba8e-433b-aad5-221ee6472ed7 is already in use.',
    ),
    true,
  )
  assert.equal(__bridgeTestUtils.shouldRemintSession('ok'), false)
  assert.equal(
    __bridgeTestUtils.shouldRetryAsCreate('Failed to resume session'),
    true,
  )
})

test('extracts approval request and builds ARC control responses', () => {
  const approval = __bridgeTestUtils.extractApprovalRequest({
    type: 'control_request',
    request_id: 'req-1',
    request: {
      subtype: 'can_use_tool',
      tool_name: 'execute_plan',
      input: { mode: 'inline' },
      decision_reason: 'Allow execute_plan in inline mode?',
      tool_use_id: 'toolu-1',
    },
  })

  assert.deepEqual(approval, {
    id: 'req-1',
    request_id: 'req-1',
    tool_use_id: 'toolu-1',
    tool_name: 'execute_plan',
    name: 'execute_plan',
    input: { mode: 'inline' },
    message: 'Allow execute_plan in inline mode?',
    title: undefined,
    display_name: undefined,
  })

  assert.deepEqual(
    __bridgeTestUtils.createApprovalControlResponse({ approval, approved: true }),
    {
      type: 'control_response',
      response: {
        subtype: 'success',
        request_id: 'req-1',
        response: {
          behavior: 'allow',
          updatedInput: { mode: 'inline' },
          toolUseID: 'toolu-1',
          decisionClassification: 'user_temporary',
        },
      },
    },
  )

  assert.deepEqual(
    __bridgeTestUtils.createApprovalControlResponse({ approval, approved: false }),
    {
      type: 'control_response',
      response: {
        subtype: 'success',
        request_id: 'req-1',
        response: {
          behavior: 'deny',
          message: 'User rejected tool execution.',
          toolUseID: 'toolu-1',
          decisionClassification: 'user_reject',
        },
      },
    },
  )
})

test('creates stream-json user input messages for ARC stdin', () => {
  assert.deepEqual(__bridgeTestUtils.createUserInputMessage('Check state.'), {
    type: 'user',
    session_id: '',
    message: {
      role: 'user',
      content: 'Check state.',
    },
    parent_tool_use_id: null,
  })
})

test('maps non-uuid UI session ids to stable ARC UUIDs', () => {
  const first = __bridgeTestUtils.resolveSession('chat-component-session')
  const second = __bridgeTestUtils.resolveSession('chat-component-session')

  assert.equal(first.sessionId, 'chat-component-session')
  assert.equal(first.arcSessionId, second.arcSessionId)
  assert.match(first.arcSessionId, /^[0-9a-f-]{36}$/i)
})
