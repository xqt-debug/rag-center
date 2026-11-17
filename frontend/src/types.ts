/** HTTP 响应类型约定；修改时须与 services/serializers.py 及各领域的响应结构保持同步。 */
export type PageName = 'library' | 'search' | 'keys' | 'activity'
export type SearchMode = 'semantic' | 'keyword' | 'hybrid'
export type Scope = 'read' | 'create' | 'update' | 'delete' | 'download' | 'kb:create'
export type DocumentStatus =
  'queued' | 'parsing' | 'chunking' | 'indexing' | 'ready' | 'stored' | 'failed' | 'cancelled'
export type DialogKind =
  | ''
  | 'base'
  | 'delete-base'
  | 'delete-doc'
  | 'preview'
  | 'edit-doc'
  | 'new-text'
  | 'new-key'
  | 'config'
  | 'revoke'

export interface User {
  id: string
  phone: string | null
  username: string | null
  csrf: string
}
export interface Health {
  environment: string
  embedding_provider: string
  embedding_configured: boolean
  database: string
  max_file_mb: number
  login_mode: 'password'
}
export interface KnowledgeBase {
  id: string
  name: string
  description: string
  revision: number
  document_count: number
  created_at: number
}
export interface DocumentRecord {
  id: string
  knowledge_base_id: string
  name: string
  is_image: boolean
  media_type: string | null
  text_source: 'text' | 'ocr' | 'provided'
  revision: number
  current_version: string | null
  latest_version: string | null
  status: DocumentStatus
  error: string
  size: number
  page_count: number
  chunk_count: number
  created_at: number
  job_id: string | null
  updated_at: number | null
  indexed_at: number | null
  index_kind: 'test' | 'semantic' | 'unknown'
  vector_index_ready: boolean
  needs_reindex: boolean
}
export interface TextPreview {
  document_id: string
  version_id: string
  name: string
  text: string
  offset: number
  next_offset: number | null
  total_characters: number
}
export interface SearchHit {
  chunk_id: string
  document_id: string
  version_id: string
  knowledge_base_id: string
  title: string
  page: number | null
  text: string
  citation: string
}
export interface SearchResponse {
  results: SearchHit[]
  degraded: boolean
  warnings: string[]
}
export interface Excerpt {
  chunks: { chunk_id: string; page: number | null; text: string }[]
}
export interface ApiKeyRecord {
  id: string
  name: string
  prefix: string
  scopes: Scope[]
  knowledge_base_ids: string[]
  revoked: boolean
  expires_at: number | null
  last_used_at: number | null
  created_at: number
}
export interface CreatedKey {
  id: string
  key: string
  prefix: string
  name: string
  scopes: Scope[]
  knowledge_base_ids: string[]
}
export interface AuditEvent {
  id: string
  actor: string
  action: string
  target_id: string
  created_at: number
}
export interface Usage {
  documents: number
  max_documents: number
  storage_bytes: number
  storage_quota_bytes: number
  chunks: number
  queued: number
  failed: number
  embedding_provider: string
  database: string
}
export interface UploadTicket {
  upload_id: string
  upload_url: string
  method: 'PUT'
  size: number
  expires_at: number
}
export interface ItemList<T> {
  items: T[]
}
export interface Paged<T> extends ItemList<T> {
  total: number
}

/** 弹窗内使用独立的可编辑副本，不直接修改列表中选中的服务端记录。 */
export interface EditorForm {
  id: string
  name: string
  is_image: boolean
  text_source: 'text' | 'ocr' | 'provided'
  description: string
  revision: number
  knowledge_base_id: string
  text: string | null
  replace: boolean
  days: number
  status: DocumentStatus
  page_count: number
  chunk_count: number
  size: number
  current_version: string | null
  indexed_at: number | null
  index_kind: 'test' | 'semantic' | 'unknown'
  vector_index_ready: boolean
  needs_reindex: boolean
  error: string
}
export function emptyEditorForm(): EditorForm {
  return {
    id: '',
    name: '',
    is_image: false,
    text_source: 'text',
    description: '',
    revision: 1,
    knowledge_base_id: '',
    text: null,
    replace: false,
    days: 30,
    status: 'queued',
    page_count: 0,
    chunk_count: 0,
    size: 0,
    current_version: null,
    indexed_at: null,
    index_kind: 'unknown',
    vector_index_ready: false,
    needs_reindex: false,
    error: '',
  }
}
