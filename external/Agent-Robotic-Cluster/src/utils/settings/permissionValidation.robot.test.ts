import { describe, expect, test } from 'bun:test'
import { validatePermissionRule } from './permissionValidation.js'

describe('RobotControl permission rules', () => {
  test('accepts exact lowercase RobotControl tool names', () => {
    expect(validatePermissionRule('execute_plan')).toEqual({ valid: true })
    expect(validatePermissionRule('get_runtime_context')).toEqual({ valid: true })
  })

  test('still rejects unknown lowercase tool names', () => {
    const result = validatePermissionRule('unknown_tool')
    expect(result.valid).toBe(false)
    expect(result.error).toBe('Tool names must start with uppercase')
  })
})