let owner = ""
export function workspaceScope() { return owner }
export function setWorkspaceScope(value: string) { owner = value }
export function workspaceKey(name: string) { return `${name}:${owner || "signed-out"}` }
