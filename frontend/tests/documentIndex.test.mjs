import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

// 验证实际状态逻辑，不启动网站、浏览器或监听服务。
const { outputText } = ts.transpileModule(
  readFileSync(new URL('../src/documentIndex.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } },
)
const { activeIndexStep, summarizeIndexing, indexSubtitle, newlyIndexed, indexPollingSeconds } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const document = (changes = {}) => ({
  id: 'doc',
  name: '资料.md',
  status: 'queued',
  current_version: null,
  latest_version: 'v1',
  chunk_count: 0,
  indexed_at: null,
  index_kind: 'semantic',
  vector_index_ready: true,
  needs_reindex: false,
  ...changes,
})

test('阶段与实际处理状态对应，不将等待或失败显示为完成', () => {
  assert.equal(activeIndexStep('queued'), -1)
  assert.equal(activeIndexStep('parsing'), 0)
  assert.equal(activeIndexStep('chunking'), 1)
  assert.equal(activeIndexStep('indexing'), 2)
  assert.equal(activeIndexStep('ready'), 3)
  assert.equal(activeIndexStep('failed'), -1)
})

test('本页完成、处理和失败数量分别统计', () => {
  assert.deepEqual(
    summarizeIndexing([
      document(),
      document({ status: 'ready' }),
      document({ status: 'failed' }),
      document({ status: 'indexing' }),
      document({ status: 'cancelled' }),
    ]),
    { completed: 1, testing: 0, needsReindex: 0, processing: 2, failed: 1 },
  )
})

test('新版本失败时清楚说明上一版本仍可检索', () => {
  assert.match(
    indexSubtitle(document({ status: 'failed', current_version: 'old' })),
    /更新失败.*上一版本仍可检索/,
  )
  assert.match(
    indexSubtitle(document({ status: 'ready', current_version: 'v1', chunk_count: 8 })),
    /8 个文本块/,
  )
})

test('只提醒新完成项，刷新历史完成项不反复提醒', () => {
  const ready = document({ status: 'ready', current_version: 'v1' })
  assert.equal(newlyIndexed([], [ready]).length, 0)
  assert.equal(newlyIndexed([document({ status: 'indexing' })], [ready]).length, 1)
  assert.equal(newlyIndexed([ready], [ready]).length, 0)
  assert.equal(
    newlyIndexed(
      [ready],
      [document({ status: 'ready', current_version: 'v2', latest_version: 'v2' })],
    ).length,
    1,
  )
})

test('有任务时频繁更新，空闲时仍发现从 MCP 提交的新任务', () => {
  assert.equal(indexPollingSeconds([document()]), 3)
  assert.equal(indexPollingSeconds([], 2), 3)
  assert.equal(indexPollingSeconds([document({ status: 'ready' })], 0), 15)
})

test('测试向量和过期模型索引不计入真实完成，也不触发完成提醒', () => {
  const testing = document({
    status: 'ready',
    index_kind: 'test',
    vector_index_ready: false,
    needs_reindex: true,
  })
  const stale = document({ status: 'ready', vector_index_ready: false, needs_reindex: true })
  assert.deepEqual(summarizeIndexing([testing, stale]), {
    completed: 0,
    testing: 1,
    needsReindex: 1,
    processing: 0,
    failed: 0,
  })
  assert.equal(newlyIndexed([document()], [testing]).length, 0)
  assert.match(indexSubtitle(testing), /未调用真实模型/)
  assert.match(indexSubtitle(stale), /需重建/)
  assert.equal(
    newlyIndexed([testing], [document({ status: 'ready', current_version: 'v2' })]).length,
    1,
  )
})
