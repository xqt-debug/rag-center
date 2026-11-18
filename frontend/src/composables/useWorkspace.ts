import { computed, onMounted, onUnmounted, ref } from 'vue'
import { action, api, setCsrf, errorMessage, putUpload } from '../api'
import { emptyEditorForm } from '../types'
import { indexPollingSeconds, newlyIndexed } from '../documentIndex'
import { titles, statusLabels, scopeLabels, actionLabels } from '../labels'
import type {
  User,
  Health,
  KnowledgeBase,
  DocumentRecord,
  Usage,
  ApiKeyRecord,
  AuditEvent,
  EditorForm,
  TextPreview,
  SearchHit,
  SearchResponse,
  SearchMode,
  Scope,
  DialogKind,
  Paged,
  ItemList,
  Excerpt,
  CreatedKey,
  UploadTicket,
} from '../types'

/** 统一管理工作空间生命周期，各页面通过带类型约束的依赖注入上下文使用该实例。 */
export function useWorkspace() {
  const user = ref<User | null>(null),
    health = ref<Partial<Health>>({}),
    booting = ref(true),
    busy = ref(false)
  const username = ref(''),
    password = ref(''),
    confirmPassword = ref(''),
    registering = ref(false)
  const userLabel = computed(() => user.value?.username || '用户')
  const userAvatar = computed(() => (user.value?.username || '').slice(-2))
  const page = ref('library'),
    bases = ref<KnowledgeBase[]>([]),
    selected = ref(''),
    docs = ref<DocumentRecord[]>([])
  const stats = ref<Partial<Usage>>({}),
    keys = ref<ApiKeyRecord[]>([]),
    audit = ref<AuditEvent[]>([])
  const toast = ref(''),
    error = ref(''),
    modal = ref<DialogKind>(''),
    form = ref<EditorForm>(emptyEditorForm()),
    preview = ref<TextPreview | null>(null),
    previewImageUrl = ref('')
  const query = ref(''),
    mode = ref<SearchMode>('hybrid'),
    searchBase = ref(''),
    results = ref<SearchHit[]>([]),
    warnings = ref<string[]>([]),
    searched = ref(false)
  const fileInput = ref<HTMLInputElement | null>(null),
    uploading = ref(false),
    uploadProgress = ref('')
  const docTotal = ref(0),
    docOffset = ref(0),
    expanded = ref<Record<string, string>>({})
  const keyMode = ref('read'),
    keyScopes = ref<Scope[]>(['read']),
    keyBases = ref<string[]>([]),
    rawKey = ref('')
  const config = computed(() =>
    JSON.stringify(
      {
        mcpServers: {
          'rag-center': {
            url: `${location.origin}/mcp`,
            headers: { Authorization: `Bearer ${rawKey.value || '<YOUR_API_KEY>'}` },
          },
        },
      },
      null,
      2,
    ),
  )
  const currentBase = computed(() => bases.value.find((b) => b.id === selected.value))
  let timer: ReturnType<typeof setInterval>,
    toastTimer: ReturnType<typeof setTimeout>,
    poll = 0
  let polling = false
  let documentRequest = 0

  function notify(message: string) {
    toast.value = message
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => (toast.value = ''), 4000)
  }
  async function attempt(fn: () => Promise<void>) {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try {
      await fn()
    } catch (e: unknown) {
      error.value = errorMessage(e)
    } finally {
      busy.value = false
    }
  }
  async function refresh() {
    const accountId = user.value?.id
    const [b, s] = await Promise.all([
      action<Paged<KnowledgeBase>>('list_knowledge_bases', { limit: 100 }),
      action<Usage>('usage'),
    ])
    if (user.value?.id !== accountId) return
    bases.value = b.items
    stats.value = s
    if (!bases.value.some((b) => b.id === selected.value)) selected.value = bases.value[0]?.id || ''
    if (selected.value) await loadDocs()
    else {
      docs.value = []
      docTotal.value = 0
    }
  }
  async function loadDocs() {
    if (!selected.value) return
    const request = ++documentRequest
    const knowledgeBaseId = selected.value
    const offset = docOffset.value
    const accountId = user.value?.id
    const data = await action<Paged<DocumentRecord>>('list_documents', {
      knowledge_base_id: knowledgeBaseId,
      offset,
    })
    // 较早选择对应的慢请求返回后，不能覆盖当前知识库或分页的数据。
    if (
      request !== documentRequest ||
      selected.value !== knowledgeBaseId ||
      docOffset.value !== offset ||
      user.value?.id !== accountId
    )
      return
    const completed = newlyIndexed(docs.value, data.items)
    docs.value = data.items
    docTotal.value = data.total
    if (completed.length === 1) notify(`${completed[0]!.name} 索引已完成，可开始搜索`)
    else if (completed.length > 1) notify(`${completed.length} 份文档索引已完成，可开始搜索`)
    if (modal.value === 'preview') {
      const current = data.items.find((document) => document.id === form.value.id)
      if (current) {
        // 仅同步只读预览，不覆盖用户正在编辑的表单；新版本发布后重新读取文本。
        form.value = { ...form.value, ...current }
        if (current.is_image && current.current_version !== preview.value?.version_id) {
          await loadPreviewImage(current)
        }
        if (current.current_version && preview.value?.version_id !== current.current_version) {
          const text = await action<TextPreview>('read_document', { document_id: current.id })
          if (
            modal.value === 'preview' &&
            form.value.id === current.id &&
            user.value?.id === accountId &&
            form.value.current_version === text.version_id &&
            preview.value?.version_id !== text.version_id
          ) {
            preview.value = text
          }
        }
      }
    }
  }
  async function selectBase(id: string) {
    selected.value = id
    docOffset.value = 0
    await attempt(loadDocs)
  }
  async function loadKeys() {
    const { items } = await action<ItemList<ApiKeyRecord>>('list_api_keys')
    // 后端保留撤销记录供审计，前端密钥列表仅展示未撤销的记录。
    keys.value = items.filter((key) => !key.revoked)
  }
  async function navigate(value: string) {
    page.value = value
    error.value = ''
    await attempt(async () => {
      if (value === 'keys') await loadKeys()
      if (value === 'activity')
        audit.value = (await action<ItemList<AuditEvent>>('list_audit_events')).items
      if (value === 'library') await refresh()
    })
  }
  function toggleRegistration() {
    registering.value = !registering.value
    password.value = ''
    confirmPassword.value = ''
    error.value = ''
  }
  async function login() {
    await attempt(async () => {
      if (registering.value && password.value !== confirmPassword.value)
        throw new Error('两次输入的密码不一致')
      user.value = await api<User>(registering.value ? '/api/auth/register' : '/api/auth/login', {
        username: username.value,
        password: password.value,
      })
      setCsrf(user.value.csrf)
      password.value = ''
      confirmPassword.value = ''
      registering.value = false
      await refresh()
    })
  }
  async function logout() {
    await attempt(async () => {
      await api('/api/auth/logout', {})
      user.value = null
      setCsrf('')
      // 退出后清空当前账户的数据，避免下次使用其他账号登录时残留旧状态。
      closeModal()
      bases.value = []
      selected.value = ''
      keys.value = []
      keyBases.value = []
      audit.value = []
      stats.value = {}
      docs.value = []
      docTotal.value = 0
      docOffset.value = 0
      results.value = []
      warnings.value = []
      searched.value = false
      expanded.value = {}
    })
  }
  function openModal(type: DialogKind, value: Partial<EditorForm> = {}) {
    error.value = ''
    form.value = { ...emptyEditorForm(), ...value }
    modal.value = type
  }
  function closeModal() {
    modal.value = ''
    rawKey.value = ''
    preview.value = null
    previewImageUrl.value = ''
    form.value = emptyEditorForm()
    error.value = ''
  }
  async function saveBase() {
    await attempt(async () => {
      const editing = form.value.id
      const result = await action<KnowledgeBase>(
        editing ? 'update_knowledge_base' : 'create_knowledge_base',
        {
          ...(editing
            ? { knowledge_base_id: form.value.id, expected_revision: form.value.revision }
            : {}),
          name: form.value.name,
          description: form.value.description || '',
        },
        true,
      )
      selected.value = result.id
      closeModal()
      await refresh()
      notify(editing ? '知识库已更新' : '知识库已创建')
    })
  }
  async function removeResource() {
    await attempt(async () => {
      const kb = modal.value === 'delete-base'
      await action(
        kb ? 'delete_knowledge_base' : 'delete_document',
        {
          [kb ? 'knowledge_base_id' : 'document_id']: form.value.id,
          expected_revision: form.value.revision,
        },
        true,
      )
      closeModal()
      await refresh()
      notify('资料已删除')
    })
  }
  async function uploadFiles(event: Event) {
    const files = Array.from((event.target as HTMLInputElement).files || [])
    if (!files.length || !selected.value) return
    // 将整批上传绑定到开始时选定的知识库，避免导航变化导致文件上传到其他位置。
    const knowledgeBaseId = selected.value
    const accountId = user.value?.id
    uploading.value = true
    error.value = ''
    try {
      for (let i = 0; i < files.length; i++) {
        if (user.value?.id !== accountId) throw new Error('登录状态已变化，上传已停止')
        const file = files[i]!
        uploadProgress.value = `${i + 1} / ${files.length} · ${file.name}`
        const ticket = await action<UploadTicket>(
          'prepare_document_upload',
          { knowledge_base_id: knowledgeBaseId, filename: file.name, size: file.size },
          true,
        )
        await putUpload(ticket, file)
        await action(
          'create_document',
          { knowledge_base_id: knowledgeBaseId, name: file.name, upload_id: ticket.upload_id },
          true,
        )
      }
      await refresh()
      notify('文件已上传，正在等待处理')
    } catch (e: unknown) {
      error.value = errorMessage(e)
      try {
        await refresh()
      } catch {
        /* 保留最初的上传失败提示，避免被刷新失败的错误覆盖。 */
      }
    } finally {
      uploading.value = false
      uploadProgress.value = ''
      if (fileInput.value) fileInput.value.value = ''
    }
  }
  async function showDocument(doc: DocumentRecord) {
    await attempt(async () => {
      form.value = { ...emptyEditorForm(), ...doc }
      modal.value = 'preview'
      preview.value = null
      previewImageUrl.value = ''
      if (doc.is_image) await loadPreviewImage(doc)
      preview.value = doc.current_version
        ? await action<TextPreview>('read_document', { document_id: doc.id })
        : null
    })
  }
  async function loadPreviewImage(doc: Pick<DocumentRecord, 'id'>) {
    const ticket = await action<{ url: string }>('download_document', { document_id: doc.id })
    const path = new URL(ticket.url, location.origin).pathname
    if (!path.startsWith('/api/transfers/download/')) throw new Error('图片预览地址无效')
    // 临时链接只保留在当前弹窗，服务端仍会校验撤销、版本变化和删除状态。
    if (modal.value === 'preview' && form.value.id === doc.id)
      previewImageUrl.value = `${path}?inline=true`
  }
  async function refreshPreviewImage() {
    await attempt(() => loadPreviewImage(form.value))
  }
  async function createText() {
    await attempt(async () => {
      await action(
        'create_document',
        {
          knowledge_base_id: form.value.knowledge_base_id,
          name: form.value.name,
          text: form.value.text,
        },
        true,
      )
      closeModal()
      await refresh()
      notify('文本已保存，正在等待建立索引')
    })
  }
  async function readMore() {
    await attempt(async () => {
      const current = preview.value
      if (!current || current.next_offset === null) return
      const next = await action<TextPreview>('read_document', {
        document_id: form.value.id,
        offset: current.next_offset,
      })
      if (next.version_id !== current.version_id) throw new Error('文件版本已更新，请重新打开预览')
      preview.value = { ...next, text: current.text + next.text }
    })
  }
  async function editDocument(doc: DocumentRecord) {
    await attempt(async () => {
      const fresh = await action<DocumentRecord>('get_document', { document_id: doc.id })
      openModal('edit-doc', { ...fresh, text: null, replace: false })
      if (/\.(md|txt)$/i.test(doc.name) && doc.current_version) {
        const data = await action<TextPreview>('read_document', {
          document_id: doc.id,
          limit: 20000,
        })
        if (data.next_offset === null) form.value.text = data.text
      }
    })
  }
  async function saveDocument() {
    await attempt(async () => {
      await action(
        'update_document',
        {
          document_id: form.value.id,
          expected_revision: form.value.revision,
          name: form.value.name,
          ...(form.value.replace ? { text: form.value.text } : {}),
        },
        true,
      )
      closeModal()
      await refresh()
      notify('修改已提交')
    })
  }
  async function replaceFile(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0]
    if (!file) return
    await attempt(async () => {
      const ticket = await action<UploadTicket>(
        'prepare_document_upload',
        {
          knowledge_base_id: form.value.knowledge_base_id,
          document_id: form.value.id,
          filename: file.name,
          size: file.size,
        },
        true,
      )
      await putUpload(ticket, file)
      await action(
        'update_document',
        {
          document_id: form.value.id,
          expected_revision: form.value.revision,
          upload_id: ticket.upload_id,
        },
        true,
      )
      closeModal()
      await refresh()
      notify('新版本已提交，索引完成后自动切换')
    })
  }
  async function download(doc: Pick<DocumentRecord, 'id'>) {
    await attempt(async () => {
      const result = await action<{ url: string }>('download_document', { document_id: doc.id })
      location.assign(result.url)
    })
  }
  async function retry(doc: Pick<DocumentRecord, 'id'>) {
    await attempt(async () => {
      await action('retry_document_processing', { document_id: doc.id }, true)
      await refresh()
      notify('已重新加入处理队列')
    })
  }
  async function search() {
    await attempt(async () => {
      const result = await action<SearchResponse>('search', {
        query: query.value,
        mode: mode.value,
        ...(searchBase.value ? { knowledge_base_ids: [searchBase.value] } : {}),
      })
      results.value = result.results
      warnings.value = result.warnings
      searched.value = true
      expanded.value = {}
    })
  }
  async function expand(hit: SearchHit) {
    await attempt(async () => {
      const data = await action<Excerpt>('get_document_excerpt', {
        document_id: hit.document_id,
        chunk_id: hit.chunk_id,
      })
      expanded.value[hit.chunk_id] = data.chunks.map((c) => c.text).join('\n\n')
    })
  }
  function newKey() {
    keyMode.value = 'read'
    keyScopes.value = ['read']
    keyBases.value = selected.value ? [selected.value] : []
    openModal('new-key', { name: '', days: 30 })
  }
  function changeKeyMode() {
    keyScopes.value = keyMode.value === 'full' ? (Object.keys(scopeLabels) as Scope[]) : ['read']
  }
  async function createKey() {
    await attempt(async () => {
      const result = await action<CreatedKey>('create_api_key', {
        name: form.value.name,
        knowledge_base_ids: keyBases.value,
        scopes: keyScopes.value,
        expires_in_days: Number(form.value.days) || null,
      })
      rawKey.value = result.key
      modal.value = 'config'
      await loadKeys()
    })
  }
  async function revoke(key: Pick<ApiKeyRecord, 'id'>) {
    await attempt(async () => {
      await action('revoke_api_key', { key_id: key.id }, true)
      keys.value = keys.value.filter((item) => item.id !== key.id)
      closeModal()
      notify('密钥已撤销')
    })
  }
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      notify('已复制到剪贴板')
    } catch {
      error.value = '复制失败，请手动选中复制'
    }
  }
  function downloadConfig() {
    const url = URL.createObjectURL(new Blob([config.value], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'rag-center.mcp.json'
    a.click()
    URL.revokeObjectURL(url)
  }
  onMounted(async () => {
    try {
      health.value = await api<Health>('/api/health')
      user.value = await api<User>('/api/auth/me')
      setCsrf(user.value.csrf)
      await refresh()
    } catch (e: unknown) {
      if (user.value) error.value = errorMessage(e)
    } finally {
      booting.value = false
    }
    timer = setInterval(async () => {
      if (
        ++poll % indexPollingSeconds(docs.value, stats.value.queued) === 0 &&
        user.value &&
        page.value === 'library' &&
        !busy.value &&
        !uploading.value &&
        !polling
      ) {
        polling = true
        const accountId = user.value.id
        try {
          await loadDocs()
          const usage = await action<Usage>('usage')
          if (user.value?.id === accountId) stats.value = usage
        } catch {
          /* 后台轮询失败时不反复打扰用户；手动刷新会展示具体错误。 */
        } finally {
          polling = false
        }
      }
    }, 1000)
  })
  onUnmounted(() => {
    clearInterval(timer)
    clearTimeout(toastTimer)
  })
  return {
    user,
    health,
    booting,
    busy,
    username,
    password,
    confirmPassword,
    registering,
    toggleRegistration,
    userLabel,
    userAvatar,
    page,
    bases,
    selected,
    docs,
    stats,
    keys,
    audit,
    toast,
    error,
    modal,
    form,
    preview,
    previewImageUrl,
    refreshPreviewImage,
    createText,
    query,
    mode,
    searchBase,
    results,
    warnings,
    searched,
    fileInput,
    uploading,
    uploadProgress,
    docTotal,
    docOffset,
    expanded,
    keyMode,
    keyScopes,
    keyBases,
    rawKey,
    config,
    currentBase,
    titles,
    statusLabels,
    scopeLabels,
    actionLabels,
    notify,
    attempt,
    refresh,
    loadDocs,
    selectBase,
    navigate,
    login,
    logout,
    openModal,
    closeModal,
    saveBase,
    removeResource,
    uploadFiles,
    showDocument,
    readMore,
    editDocument,
    saveDocument,
    replaceFile,
    download,
    retry,
    search,
    expand,
    newKey,
    changeKeyMode,
    createKey,
    revoke,
    copy,
    downloadConfig,
  }
}
