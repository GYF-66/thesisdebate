export type ReviewTab = 'changes' | 'agents' | 'review' | 'assistance' | 'assistant' | 'literature'
export type WorkspaceLayout = { leftRailOpen: boolean; rightPanelOpen: boolean; showDiff: boolean; reviewTab: ReviewTab }
export const WORKSPACE_LAYOUT_KEY = 'thesisdebate.workspace-layout.v1'
export function readWorkspaceLayout(storage: Pick<Storage, 'getItem'>, narrow: boolean): WorkspaceLayout {
  const defaults: WorkspaceLayout = { leftRailOpen: !narrow, rightPanelOpen: false, showDiff: false, reviewTab: 'changes' }
  try {
    const saved = JSON.parse(storage.getItem(WORKSPACE_LAYOUT_KEY) ?? 'null')
    if (!saved || typeof saved !== 'object') return defaults
    return { leftRailOpen: typeof saved.leftRailOpen === 'boolean' ? saved.leftRailOpen : defaults.leftRailOpen, rightPanelOpen: typeof saved.rightPanelOpen === 'boolean' ? saved.rightPanelOpen : false, showDiff: typeof saved.showDiff === 'boolean' ? saved.showDiff : false, reviewTab: ['changes', 'agents', 'review', 'assistance', 'assistant', 'literature'].includes(saved.reviewTab) ? saved.reviewTab : 'changes' }
  } catch { return defaults }
}
export function saveWorkspaceLayout(storage: Pick<Storage, 'setItem'>, layout: WorkspaceLayout) {
  try { storage.setItem(WORKSPACE_LAYOUT_KEY, JSON.stringify(layout)) } catch { /* Layout preferences never block editing when storage is unavailable. */ }
}
