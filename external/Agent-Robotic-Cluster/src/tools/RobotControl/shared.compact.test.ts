import { expect, test } from 'bun:test'
import { compactScanResult, compactStorageResult, modelFacingRobotResult } from './shared.js'

test('scan bridge removes calibration bulk while retaining planning coordinates', () => {
  const compact = compactScanResult({
    ok: true,
    camera_id: 'Cam1',
    timestamp: '2026-08-10T05:36:58Z',
    calibration_ready: true,
    workspace: { zones: Array.from({ length: 100 }, () => ({ polygon_mm: [1, 2] })) },
    objects: [
      {
        id: 'obj-1',
        color: 'red',
        block: 'A4',
        bbox: { x: 1, y: 2, w: 3, h: 4 },
        workspace_xy: { x: 100, y: 200, ignored: 'no' },
        reachable_by: ['Arm2'],
        preferred_arm: 'Arm2',
        per_arm_robot_plane: {
          Arm1: { x: 10, y: 20, reachable: false, transform: { matrix: [1, 2, 3] } },
          Arm2: { x: 30, y: 40, reachable: true, transform: { matrix: [4, 5, 6] } },
        },
      },
    ],
    selected_object: { id: 'obj-1', color: 'red', bbox: { x: 1 } },
  })

  expect(compact).toEqual({
    ok: true,
    camera_id: 'Cam1',
    timestamp: '2026-08-10T05:36:58Z',
    calibration_ready: true,
    object_count: 1,
    objects: [
      {
        id: 'obj-1',
        color: 'red',
        block: 'A4',
        workspace_xy: { x: 100, y: 200 },
        reachable_by: ['Arm2'],
        preferred_arm: 'Arm2',
        per_arm_robot_plane: {
          Arm1: { x: 10, y: 20, reachable: false },
          Arm2: { x: 30, y: 40, reachable: true },
        },
      },
    ],
    selected_object: { id: 'obj-1', color: 'red' },
  })
})
test('model-facing envelope drops command stdout and workspace_root', () => {
  const raw = {
    workspace_root: 'C:\\\\ARC',
    tool_name: 'get_runtime_context',
    command: 'python arc.py workspace --json context',
    success: true,
    exit_code: 0,
    stdout: '{"ok":true,"active_arm_ids":["Arm1"]}',
    stderr: '',
    parsed: { ok: true, active_arm_ids: ['Arm1'] },
    runtime_context: { ok: true, active_arm_ids: ['Arm1'] },
  }
  const facing = JSON.parse(modelFacingRobotResult(raw))
  expect(facing.ok).toBe(true)
  expect(facing.tool).toBe('get_runtime_context')
  expect(facing.data.active_arm_ids).toEqual(['Arm1'])
  expect(facing.command).toBeUndefined()
  expect(facing.stdout).toBeUndefined()
  expect(facing.workspace_root).toBeUndefined()
})

test('storage bridge maps occupied colors to slots and groups without calibration bulk', () => {
  const compact = compactStorageResult(
    {
      ok: true,
      registry: {
        updated_at: '2026-08-11T04:07:54Z',
        slots: Array.from({ length: 200 }, () => ({ calibration_matrix: [1, 2, 3] })),
      },
      slots: [
        {
          name: 'LB1',
          group: 'LB',
          occupancy: 'occupied',
          occupancy_color: 'blue',
          reachable_by: ['Arm1'],
          per_arm_robot_plane: {
            Arm1: { x: 288, y: 236, transform: { matrix: [1, 2, 3] } },
          },
        },
        {
          name: 'LB2',
          group: 'LB',
          occupancy: 'empty',
          reachable_by: ['Arm1'],
          center_px: { x: 940, y: 1340 },
        },
        {
          name: 'RT1',
          group: 'RT',
          occupancy: 'occupied',
          held_object: { color: 'red', contour: Array.from({ length: 100 }, () => 1) },
          reachable_by: ['Arm2'],
        },
      ],
    },
    true,
  )

  expect(compact?.fresh_scan).toBe(true)
  expect(compact?.slot_count).toBe(3)
  expect(compact?.color_locations).toEqual({
    blue: { count: 1, slots: ['LB1'], groups: ['LB'] },
    red: { count: 1, slots: ['RT1'], groups: ['RT'] },
  })
  expect(compact?.empty_slots_by_group).toEqual({
    LT: [],
    LB: ['LB2'],
    RT: [],
    RB: [],
  })
  expect(JSON.stringify(compact)).not.toContain('per_arm_robot_plane')
  expect(JSON.stringify(compact)).not.toContain('calibration_matrix')
})