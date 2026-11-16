import { beforeEach, expect, mock, test } from 'bun:test'
import { getEmptyToolPermissionContext } from './Tool.js'

let lspConnected = false

mock.module('./services/lsp/manager.js', () => ({
  getInitializationStatus: () => ({ status: 'success' }),
  getLspServerManager: () => undefined,
  isLspConnected: () => lspConnected,
  reinitializeLspServerManager: () => {},
  waitForInitialization: async () => {},
}))

const { getAllBaseTools, getTools } = await import('./tools.js')

beforeEach(() => {
  lspConnected = false
})

test('LSPTool is not part of the lean robot base tool pool', () => {
  expect(getAllBaseTools().map(tool => tool.name)).not.toContain('LSP')
})

test('LSPTool is not exposed from the default robot tool surface', () => {
  const permissionContext = getEmptyToolPermissionContext()

  expect(getTools(permissionContext).map(tool => tool.name)).not.toContain('LSP')

  lspConnected = true

  expect(getTools(permissionContext).map(tool => tool.name)).not.toContain('LSP')
})
