import { readFileSync } from 'node:fs'
import { afterEach, mock, test } from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

// 使用现有工具测试真实的 TypeScript 请求模块，不另写一份 JavaScript 实现。
const { outputText } = ts.transpileModule(
  readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } },
)
const { api, action, setCsrf, putUpload, ApiError } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
)
afterEach(() => {
  mock.restoreAll()
  setCsrf('')
})

test('writes include session credentials, CSRF and an idempotency identifier', async () => {
  setCsrf('session-csrf')
  const fetch = mock.method(globalThis, 'fetch', async () => Response.json({ id: 'new-document' }))
  const result = await action('create_document', { text: 'private text' }, true)
  assert.equal(result.id, 'new-document')
  const [path, request] = fetch.mock.calls[0].arguments
  assert.equal(path, '/api/actions/create_document')
  assert.equal(request.credentials, 'same-origin')
  assert.equal(request.headers['X-CSRF-Token'], 'session-csrf')
  assert.ok(request.headers['Idempotency-Key'])
  assert.equal(JSON.parse(request.body).text, 'private text')
})

test('proxy HTML errors produce a readable error instead of a JSON parser failure', async () => {
  mock.method(
    globalThis,
    'fetch',
    async () => new Response('<html>Bad gateway</html>', { status: 502 }),
  )
  await assert.rejects(
    api('/api/health'),
    (error) =>
      error instanceof ApiError && error.status === 502 && !error.message.includes('Unexpected'),
  )
})

test('domain errors preserve their code and display message', async () => {
  mock.method(globalThis, 'fetch', async () =>
    Response.json({ error: { code: 'forbidden', message: '密钥权限不足' } }, { status: 403 }),
  )
  await assert.rejects(
    action('delete_document', {}),
    (error) => error.code === 'forbidden' && error.message === '密钥权限不足',
  )
})

test('unsupported upload endpoints are rejected before sending file content', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => Response.json({}))
  await assert.rejects(
    putUpload(
      { upload_url: 'https://other.example/unrelated-path' },
      new File(['private text'], 'x.txt'),
    ),
  )
  assert.equal(fetch.mock.callCount(), 0)
})

test('upload failures use the same domain error handling as JSON requests', async () => {
  mock.method(globalThis, 'fetch', async () =>
    Response.json(
      { error: { code: 'upload_expired', message: '上传会话已失效' } },
      { status: 403 },
    ),
  )
  await assert.rejects(
    putUpload(
      { upload_url: 'http://localhost:8000/api/transfers/upload/token' },
      new File(['x'], 'x.txt'),
    ),
    (error) => error.code === 'upload_expired',
  )
})
