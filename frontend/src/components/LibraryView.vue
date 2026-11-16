<script setup lang="ts">
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  FileText,
  FolderOpen,
  Layers3,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
  Activity,
  Pencil,
} from 'lucide-vue-next'
import { size, date } from '../api'
import { useWorkspaceContext } from '../context'
import { computed } from 'vue'
import DocumentIndexStatus from './DocumentIndexStatus.vue'
import { indexSubtitle, summarizeIndexing } from '../documentIndex'
import { supportedFileTypes } from '../media'
const workspace = useWorkspaceContext()
const {
  health,
  busy,
  bases,
  selected,
  docs,
  stats,
  error,
  fileInput,
  uploading,
  uploadProgress,
  docTotal,
  docOffset,
  currentBase,
  attempt,
  refresh,
  loadDocs,
  selectBase,
  navigate,
  openModal,
  uploadFiles,
  showDocument,
  editDocument,
  download,
  retry,
} = workspace
const indexingSummary = computed(() => summarizeIndexing(docs.value))

function changePage(delta: number) {
  docOffset.value += delta
  void attempt(loadDocs)
}
</script>

<template>
  <div class="stats-grid">
    <div class="stat">
      <span class="stat-icon green"><BookOpen :size="20" /></span>
      <div>
        <span>知识库</span>
        <strong>
          {{ bases.length }}
          <small>个空间</small>
        </strong>
      </div>
    </div>
    <div class="stat">
      <span class="stat-icon sand"><FileText :size="20" /></span>
      <div>
        <span>资料文件</span>
        <strong>
          {{ stats.documents || 0 }}
          <small>份资料</small>
        </strong>
      </div>
    </div>
    <div class="stat">
      <span class="stat-icon blue"><Layers3 :size="20" /></span>
      <div>
        <span>可检索片段</span>
        <strong>
          {{ stats.chunks || 0 }}
          <small>个片段</small>
        </strong>
      </div>
    </div>
    <div class="stat">
      <span class="stat-icon lilac"><Activity :size="20" /></span>
      <div>
        <span>处理队列</span>
        <strong>
          {{ stats.queued || 0 }}
          <small>{{ stats.failed ? `${stats.failed} 个失败` : '个任务' }}</small>
        </strong>
      </div>
    </div>
  </div>
  <div class="section-title">
    <h2>
      我的知识库
      <span>{{ bases.length }}</span>
    </h2>
    <span class="muted small">按主题组织你的资料</span>
  </div>
  <div v-if="!bases.length" class="empty base-empty">
    <div class="empty-icon"><FolderOpen :size="32" /></div>
    <h3>给知识一个落脚点</h3>
    <p>创建第一个知识库，开始整理你的文档、笔记和参考资料。</p>
    <button class="secondary" @click="openModal('base', { name: '', description: '' })">
      <Plus :size="16" />
      创建知识库
    </button>
  </div>
  <div v-else class="base-grid">
    <button
      v-for="base in bases"
      :key="base.id"
      class="base-card"
      :class="{ selected: selected === base.id }"
      @click="selectBase(base.id)"
    >
      <div class="base-card-top">
        <span class="folder-icon"><FolderOpen :size="23" /></span>
        <Check v-if="selected === base.id" :size="17" />
        <ArrowUpRight v-else :size="16" class="muted" />
      </div>
      <h3>{{ base.name }}</h3>
      <p>{{ base.description || '为这个知识库收集一些值得保留的资料。' }}</p>
      <div class="base-card-bottom">
        <span>
          <FileText :size="13" />
          {{ base.document_count }} 个文件
        </span>
        <span>{{ date(base.created_at).split(' ')[0] }}</span>
      </div>
    </button>
  </div>
  <section v-if="selected" class="documents-panel">
    <div class="panel-heading">
      <div>
        <h2>
          {{ currentBase?.name }}
          <span class="subtle">/ 文件</span>
        </h2>
        <p class="muted small">
          支持文本、PDF、图片与 GIF 动图 · 单文件最多 {{ health.max_file_mb || 20 }} MB
        </p>
      </div>
      <div class="button-group">
        <button
          class="icon-btn"
          title="刷新"
          aria-label="刷新文件列表"
          :disabled="busy"
          @click="attempt(refresh)"
        >
          <RefreshCw :size="16" />
        </button>
        <button
          class="icon-btn"
          title="编辑知识库"
          aria-label="编辑知识库"
          @click="openModal('base', currentBase)"
        >
          <Settings2 :size="17" />
        </button>
        <button
          class="icon-btn danger"
          title="删除知识库"
          aria-label="删除知识库"
          @click="openModal('delete-base', currentBase)"
        >
          <Trash2 :size="16" />
        </button>
        <button
          class="secondary small-btn"
          :disabled="busy"
          @click="
            openModal('new-text', { name: '笔记.txt', text: '', knowledge_base_id: selected })
          "
        >
          <Pencil :size="15" />
          粘贴文本
        </button>
        <button class="primary small-btn" :disabled="uploading" @click="fileInput?.click()">
          <Upload :size="15" />
          {{ uploading ? '上传中…' : '上传文件' }}
        </button>
        <input
          ref="fileInput"
          type="file"
          multiple
          :accept="supportedFileTypes"
          class="hidden"
          @change="uploadFiles"
        />
      </div>
    </div>
    <div v-if="uploading" class="notice">正在上传 {{ uploadProgress }}</div>
    <div v-if="docs.length" class="index-summary" aria-live="polite" aria-atomic="true">
      <span>本页索引状态</span>
      <strong class="index-summary-ready">已完成 {{ indexingSummary.completed }} 份</strong>
      <span v-if="indexingSummary.testing">测试索引 {{ indexingSummary.testing }} 份</span>
      <span v-if="indexingSummary.needsReindex">需重建 {{ indexingSummary.needsReindex }} 份</span>
      <span>处理中 {{ indexingSummary.processing }} 份</span>
      <span v-if="indexingSummary.stored">仅保存 {{ indexingSummary.stored }} 份</span>
      <span :class="{ 'index-summary-failed': indexingSummary.failed > 0 }">
        失败 {{ indexingSummary.failed }} 份
      </span>
      <small>处理进度自动更新</small>
    </div>
    <p v-if="health.embedding_provider === 'local-test'" class="index-model-note">
      当前显式使用测试向量，不会计为真实索引完成。请配置真实向量模型后重建。
    </p>
    <p v-else-if="!health.embedding_configured" class="index-model-note">
      尚未配置向量模型 API，新文件不会生成真实向量索引。配置并重启后可重试或重建。
    </p>
    <div
      v-if="!docs.length"
      class="upload-empty"
      @dragover.prevent
      @drop.prevent="error = '请点击上传文件选择资料'"
    >
      <span class="empty-icon"><Upload :size="25" /></span>
      <h3>把资料放进来，让知识连接起来</h3>
      <p>
        支持 TXT、Markdown、文本 PDF 和 PNG/JPEG/GIF/WebP/BMP。图片通过 OCR
        识别文字，原图和动画完整保留。
      </p>
      <button class="text-button" @click="fileInput?.click()">
        选择本地文件
        <ArrowRight :size="14" />
      </button>
    </div>
    <div v-else class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>文件名称</th>
            <th>索引进度</th>
            <th>大小</th>
            <th>上传时间</th>
            <th class="align-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="doc in docs" :key="doc.id">
            <td>
              <button class="file-cell" @click="showDocument(doc)">
                <span class="file-icon" :class="{ pdf: /\.pdf$/i.test(doc.name) }">
                  <FileText :size="18" />
                </span>
                <span>
                  <strong>{{ doc.name }}</strong>
                  <small>
                    {{ indexSubtitle(doc) }}
                  </small>
                </span>
              </button>
            </td>
            <td>
              <DocumentIndexStatus :document="doc" />
            </td>
            <td class="muted">{{ size(doc.size) }}</td>
            <td class="muted">{{ date(doc.created_at) }}</td>
            <td>
              <div class="row-actions">
                <button
                  v-if="doc.status === 'ready' && doc.needs_reindex"
                  class="text-button"
                  :disabled="busy || !health.embedding_configured"
                  aria-label="重建向量索引"
                  :title="
                    health.embedding_configured
                      ? '用当前真实模型重新建立索引'
                      : '请先配置真实向量模型 API 并重启服务'
                  "
                  @click="retry(doc)"
                >
                  重建索引
                </button>
                <button
                  v-if="doc.status === 'failed'"
                  class="icon-btn"
                  title="重试处理"
                  aria-label="重试处理"
                  @click="retry(doc)"
                >
                  <RefreshCw :size="15" />
                </button>
                <button
                  class="icon-btn"
                  title="预览"
                  aria-label="预览文件"
                  @click="showDocument(doc)"
                >
                  <BookOpen :size="15" />
                </button>
                <button
                  class="icon-btn"
                  title="修改"
                  aria-label="修改文件"
                  @click="editDocument(doc)"
                >
                  <Pencil :size="15" />
                </button>
                <button
                  class="icon-btn"
                  title="下载原文件"
                  aria-label="下载原文件"
                  @click="download(doc)"
                >
                  <ArrowDownToLine :size="15" />
                </button>
                <button
                  class="icon-btn danger"
                  title="删除"
                  aria-label="删除文件"
                  @click="openModal('delete-doc', doc)"
                >
                  <Trash2 :size="15" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="table-footer">
        <span>共 {{ docTotal }} 份资料</span>
        <div class="button-group">
          <button class="text-button" :disabled="docOffset === 0" @click="changePage(-30)">
            上一页
          </button>
          <button
            class="text-button"
            :disabled="docOffset + docs.length >= docTotal"
            @click="changePage(30)"
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  </section>
  <div class="bottom-note">
    <ShieldCheck :size="16" />
    <span>资料仅在你的账户及密钥授权范围内可用。</span>
    <button @click="navigate('keys')">
      连接你的 Agent
      <ArrowUpRight :size="14" />
    </button>
  </div>
</template>
