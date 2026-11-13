import { inject, type InjectionKey } from 'vue'
import type { useWorkspace } from './composables/useWorkspace'

export type Workspace = ReturnType<typeof useWorkspace>
export const workspaceKey: InjectionKey<Workspace> = Symbol('rag-center-workspace')

/** 所有页面共用 App 创建的唯一工作空间，不要在子页面中另建状态实例。 */
export function useWorkspaceContext(): Workspace {
  const workspace = inject(workspaceKey)
  if (!workspace) throw new Error('Workspace views must be mounted under App')
  return workspace
}
