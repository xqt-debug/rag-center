import type { DocumentRecord, DocumentStatus } from './types'

const processingStatuses = new Set<DocumentStatus>(['queued', 'parsing', 'chunking', 'indexing'])
export const indexSteps = ['解析文档', '文本分块', '建立索引', '完成'] as const
const stepByStatus: Partial<Record<DocumentStatus, number>> = {
  queued: -1,
  parsing: 0,
  chunking: 1,
  indexing: 2,
  ready: 3,
}

export function isIndexing(status: DocumentStatus): boolean {
  return processingStatuses.has(status)
}

export function activeIndexStep(status: DocumentStatus): number {
  return stepByStatus[status] ?? -1
}

type IndexState = Pick<
  DocumentRecord,
  'status' | 'index_kind' | 'vector_index_ready' | 'needs_reindex'
>

export function isRealIndexReady(document: IndexState): boolean {
  return (
    document.status === 'ready' &&
    document.index_kind === 'semantic' &&
    document.vector_index_ready === true
  )
}

export function readyIndexLabel(document: IndexState): string {
  if (isRealIndexReady(document)) return '索引已完成'
  return document.index_kind === 'test' ? '测试索引（非模型）' : '需重建向量索引'
}

export function summarizeIndexing(documents: IndexState[]) {
  return {
    completed: documents.filter(isRealIndexReady).length,
    testing: documents.filter(
      (document) => document.status === 'ready' && document.index_kind === 'test',
    ).length,
    needsReindex: documents.filter(
      (document) =>
        document.status === 'ready' &&
        !isRealIndexReady(document) &&
        document.index_kind !== 'test',
    ).length,
    processing: documents.filter((document) => isIndexing(document.status)).length,
    failed: documents.filter((document) => document.status === 'failed').length,
  }
}

export function indexSubtitle(document: DocumentRecord): string {
  const hasPrevious =
    document.current_version && document.current_version !== document.latest_version
  if (hasPrevious) {
    if (document.status === 'failed') return '更新失败，上一版本仍可检索'
    if (document.status === 'cancelled') return '更新已取消，上一版本仍可检索'
    return '上一版本仍可检索，新版本处理中'
  }
  if (document.status === 'ready') {
    if (isRealIndexReady(document)) return `已生成 ${document.chunk_count} 个文本块，可开始搜索`
    if (document.index_kind === 'test') return '未调用真实模型；关键词可检索，配置模型后请重建'
    return '原模型配置已变化；关键词可检索，向量索引需重建'
  }
  if (document.status === 'failed') return '索引未完成，可查看原因并重试'
  if (document.status === 'cancelled') return '索引任务已取消'
  if (document.status === 'queued') return '等待后台处理'
  return '处理完成后将自动更新状态'
}

export function newlyIndexed(previous: DocumentRecord[], next: DocumentRecord[]): DocumentRecord[] {
  const known = new Map(previous.map((document) => [document.id, document]))
  // 首次打开页面不提醒历史完成项；新版本快速发布时，即使没看到中间状态也应提醒。
  return next.filter((document) => {
    const before = known.get(document.id)
    return (
      before &&
      isRealIndexReady(document) &&
      (!isRealIndexReady(before) || before.current_version !== document.current_version)
    )
  })
}

export function indexPollingSeconds(
  documents: Pick<DocumentRecord, 'status'>[],
  queued = 0,
): number {
  // 活跃任务加快更新；空闲时仍定期查询，能发现 MCP 或其他页面提交的文件。
  return queued > 0 || documents.some((document) => isIndexing(document.status)) ? 3 : 15
}
