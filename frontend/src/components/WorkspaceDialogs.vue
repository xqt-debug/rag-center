<script setup lang="ts">
import { ArrowDownToLine, ArrowRight, Check, Copy, Trash2, Upload, X } from 'lucide-vue-next'
import { size, date } from '../api'
import { useWorkspaceContext } from '../context'
import { readyIndexLabel } from '../documentIndex'
import { supportedFileTypes } from '../media'
const workspace = useWorkspaceContext()
const {
  busy,
  bases,
  error,
  modal,
  form,
  preview,
  previewImageUrl,
  refreshPreviewImage,
  createText,
  keyMode,
  keyScopes,
  keyBases,
  rawKey,
  config,
  statusLabels,
  scopeLabels,
  closeModal,
  saveBase,
  removeResource,
  readMore,
  saveDocument,
  replaceFile,
  download,
  retry,
  changeKeyMode,
  createKey,
  revoke,
  copy,
  downloadConfig,
} = workspace
</script>

<template>
  <div v-if="modal" class="modal-backdrop" @click.self="closeModal">
    <section
      class="modal"
      :class="{ wide: ['preview', 'edit-doc', 'new-text', 'config'].includes(modal) }"
      role="dialog"
      aria-modal="true"
      :aria-label="modal === 'config' ? 'MCP 配置' : '管理资料'"
    >
      <button class="modal-close icon-btn" aria-label="关闭" @click="closeModal">
        <X :size="20" />
      </button>
      <template v-if="modal === 'base'">
        <span class="mini-label">KNOWLEDGE BASE</span>
        <h2>{{ form.id ? '编辑知识库' : '创建知识库' }}</h2>
        <p class="muted">给相关资料一个共同的空间。</p>
        <form @submit.prevent="saveBase">
          <label>
            名称
            <input
              v-model="form.name"
              required
              maxlength="120"
              placeholder="例如：项目资料、学习笔记"
              autofocus
            />
          </label>
          <label>
            描述
            <textarea
              v-model="form.description"
              rows="3"
              maxlength="4000"
              placeholder="这个知识库里放些什么？"
            />
          </label>
          <button class="primary full" :disabled="busy">
            {{ busy ? '保存中…' : '保存知识库' }}
            <ArrowRight :size="16" />
          </button>
        </form>
      </template>
      <template v-else-if="modal === 'delete-base' || modal === 'delete-doc'">
        <span class="danger"><Trash2 :size="30" /></span>
        <h2>删除{{ modal === 'delete-base' ? '知识库' : '文件' }}？</h2>
        <p class="muted">
          “{{ form.name }}”{{
            modal === 'delete-base' ? '及其中全部文件' : ''
          }}将立即退出搜索、读取与下载，后台随后清理原文件。
        </p>
        <div class="modal-buttons">
          <button class="secondary" @click="closeModal">取消</button>
          <button class="primary destructive" :disabled="busy" @click="removeResource">
            确认删除
          </button>
        </div>
      </template>
      <template v-else-if="modal === 'new-text'">
        <span class="mini-label">TEXT NOTE</span>
        <h2>保存文本资料</h2>
        <form @submit.prevent="createText">
          <label>
            文件名称（.txt 或 .md）
            <input v-model="form.name" required maxlength="240" pattern=".*\.(txt|md)$" />
          </label>
          <label>
            文本内容
            <textarea
              v-model="form.text"
              rows="14"
              class="editor"
              required
              maxlength="200000"
              placeholder="粘贴学习资料或输入笔记…"
            />
          </label>
          <p class="muted small">
            保存后自动分块并建立关键词与向量索引，内容可下载为原始文本文件。
          </p>
          <button class="primary full" :disabled="busy">
            保存并建立索引
            <Check :size="16" />
          </button>
        </form>
      </template>
      <template v-else-if="modal === 'preview'">
        <span class="mini-label">DOCUMENT PREVIEW</span>
        <h2>{{ form.name }}</h2>
        <div class="preview-meta">
          <span>
            {{ form.status === 'ready' ? readyIndexLabel(form) : statusLabels[form.status] }}
          </span>
          <span>
            {{ form.page_count ? `${form.page_count} 页 · ` : '' }}{{ form.chunk_count }} 个片段
          </span>
          <span>{{ size(form.size) }}</span>
        </div>
        <p v-if="form.indexed_at" class="muted small">
          最近可检索版本索引完成于 {{ date(form.indexed_at) }}
        </p>
        <div v-if="form.error" class="error">{{ form.error }}</div>
        <div v-if="form.is_image" class="image-preview">
          <img v-if="previewImageUrl" :src="previewImageUrl" :alt="form.name" />
          <button class="text-button" :disabled="busy" @click="refreshPreviewImage">
            刷新图片预览
          </button>
          <p class="muted small">
            保留原图；GIF 等动图可直接播放。OCR 仅识别文字，动图最多抽取 12
            帧，可能遗漏短暂出现的内容。
          </p>
        </div>
        <p v-if="form.is_image && preview?.text" class="muted small">
          {{
            form.text_source === 'provided'
              ? '以下为随图片提供的文字内容。'
              : '以下为 OCR 识别文字，请与原图核对。'
          }}
        </p>
        <pre v-if="preview" class="document-preview">{{ preview.text }}</pre>
        <div v-if="!preview?.text" class="empty">
          <p>
            {{
              form.status === 'stored'
                ? '原图已保存，未识别到可检索文字。'
                : '处理完成后可预览提取文本。原文件仍可下载。'
            }}
          </p>
        </div>
        <button
          v-if="preview?.next_offset !== null && preview"
          class="text-button"
          @click="readMore"
        >
          继续读取
          <ArrowRight :size="14" />
        </button>
        <div class="modal-buttons">
          <button v-if="form.status === 'failed'" class="secondary" @click="retry(form)">
            重试处理
          </button>
          <button class="secondary" @click="download(form)">
            <ArrowDownToLine :size="15" />
            下载原文件
          </button>
        </div>
      </template>
      <template v-else-if="modal === 'edit-doc'">
        <span class="mini-label">UPDATE DOCUMENT</span>
        <h2>更新资料</h2>
        <form @submit.prevent="saveDocument">
          <label>
            文件显示名称
            <input v-model="form.name" required maxlength="240" />
          </label>
          <label v-if="form.text !== null" class="checkbox-line">
            <input type="checkbox" v-model="form.replace" />
            同时编辑文本内容
          </label>
          <label v-if="form.replace">
            完整文本
            <textarea v-model="form.text" rows="12" class="editor" required />
          </label>
          <p class="muted small">
            仅改名不会重建索引。替换内容会生成新版本，处理完成前保留旧版可用。
          </p>
          <label class="file-replace secondary">
            <Upload :size="15" />
            选择文件替换内容
            <input
              type="file"
              :accept="supportedFileTypes"
              @change="replaceFile"
              :disabled="busy"
            />
          </label>
          <button class="primary full" :disabled="busy">
            保存修改
            <Check :size="16" />
          </button>
        </form>
      </template>
      <template v-else-if="modal === 'new-key'">
        <span class="mini-label">AGENT ACCESS</span>
        <h2>创建访问密钥</h2>
        <form @submit.prevent="createKey">
          <label>
            密钥名称
            <input
              v-model="form.name"
              required
              maxlength="120"
              placeholder="例如：我的桌面 Agent"
            />
          </label>
          <label>
            权限模板
            <select v-model="keyMode" @change="changeKeyMode">
              <option value="read">只读检索</option>
              <option value="full">完整管理</option>
            </select>
          </label>
          <div class="scope-grid">
            <label v-for="(label, scope) in scopeLabels" :key="scope" class="checkbox-line">
              <input type="checkbox" :value="scope" v-model="keyScopes" />
              {{ label }}
            </label>
          </div>
          <label>授权知识库</label>
          <div class="kb-checkboxes">
            <label v-for="base in bases" :key="base.id" class="checkbox-line">
              <input type="checkbox" :value="base.id" v-model="keyBases" />
              {{ base.name }}
            </label>
            <p v-if="!bases.length" class="muted small">暂无知识库。可以先授予“创建知识库”权限。</p>
          </div>
          <p class="muted small">
            通过此密钥创建的知识库会加入其授权范围，其他新增知识库不会自动加入。
          </p>
          <label>
            有效期
            <select v-model="form.days">
              <option :value="7">7 天</option>
              <option :value="30">30 天</option>
              <option :value="90">90 天</option>
              <option :value="0">长期有效</option>
            </select>
          </label>
          <button
            class="primary full"
            :disabled="
              busy || !keyScopes.length || (!keyBases.length && !keyScopes.includes('kb:create'))
            "
          >
            生成密钥与配置
            <ArrowRight :size="16" />
          </button>
        </form>
      </template>
      <template v-else-if="modal === 'config'">
        <span class="mini-label">CONNECT VIA MCP</span>
        <h2>{{ rawKey ? '密钥已创建，连接你的 Agent' : 'MCP 配置模板' }}</h2>
        <p class="muted">
          适用于支持远程 HTTP 和自定义请求头的客户端。不同客户端的字段可能需要调整。
        </p>
        <template v-if="rawKey">
          <div class="notice">完整密钥仅显示本次。关闭后无法再次查看，请先妥善保存。</div>
          <div class="secret-value">
            <code>{{ rawKey }}</code>
            <button class="icon-btn" aria-label="复制密钥" @click="copy(rawKey)">
              <Copy :size="16" />
            </button>
          </div>
        </template>
        <div class="config-header">
          <span>mcp.json</span>
          <button class="text-button" @click="copy(config)">
            <Copy :size="14" />
            复制配置
          </button>
        </div>
        <pre class="config-code">{{ config }}</pre>
        <p class="muted small">
          {{
            rawKey
              ? '配置包含完整密钥，请仅保存到你信任的客户端。'
              : '将密钥占位符替换为你保存的密钥。'
          }}
          PDF、图片和动图上传下载需要 Agent 具备 HTTP 文件传输能力。
        </p>
        <div class="modal-buttons">
          <button class="secondary" @click="downloadConfig">
            <ArrowDownToLine :size="15" />
            下载 JSON
          </button>
          <button class="primary" @click="closeModal">
            {{ rawKey ? '已保存，完成' : '完成' }}
            <Check :size="16" />
          </button>
        </div>
      </template>
      <template v-else-if="modal === 'revoke'">
        <h2>撤销密钥？</h2>
        <p class="muted">
          使用“{{ form.name }}”的 Agent 将立即失去访问权限。此操作不能恢复，可以重新创建密钥。
        </p>
        <div class="modal-buttons">
          <button class="secondary" @click="closeModal">取消</button>
          <button class="primary destructive" :disabled="busy" @click="revoke(form)">
            确认撤销
          </button>
        </div>
      </template>
      <div v-if="error" class="error" role="alert">{{ error }}</div>
    </section>
  </div>
</template>
