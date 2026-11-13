/** 各页面共用的展示文案，不保存应用状态。 */
export const titles: Record<string, string> = {
  library: '知识库',
  search: '搜索实验室',
  keys: 'MCP 接入',
  activity: '操作记录',
}
export const statusLabels: Record<string, string> = {
  queued: '等待索引',
  parsing: '解析中',
  chunking: '分块中',
  indexing: '建立索引',
  ready: '索引已完成',
  failed: '索引失败',
  cancelled: '已取消',
}
export const scopeLabels: Record<string, string> = {
  read: '读取与搜索',
  create: '上传与创建文件',
  update: '修改与重试',
  delete: '删除资料',
  download: '下载原文件',
  'kb:create': '创建知识库',
}
export const actionLabels: Record<string, string> = {
  create_knowledge_base: '创建知识库',
  update_knowledge_base: '修改知识库',
  delete_knowledge_base: '删除知识库',
  create_document: '创建文件',
  update_document: '更新文件',
  delete_document: '删除文件',
  prepare_document_upload: '申请上传',
  retry_document_processing: '重试处理',
  create_api_key: '创建密钥',
  revoke_api_key: '撤销密钥',
}
