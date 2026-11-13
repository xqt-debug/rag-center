<script setup lang="ts">
import { Code2, KeyRound, Plus, ShieldCheck } from 'lucide-vue-next'
import { date } from '../api'
import { useWorkspaceContext } from '../context'
const workspace = useWorkspaceContext()
const { keys, rawKey, scopeLabels, openModal, newKey } = workspace

function showConfigTemplate() {
  // 配置模板不得复用此前创建流程中展示过的完整密钥。
  rawKey.value = ''
  openModal('config')
}
</script>

<template>
  <div class="connect-banner">
    <div class="connect-symbol"><Code2 :size="35" /></div>
    <div>
      <span class="mini-label">MODEL CONTEXT PROTOCOL</span>
      <h2>资料在这里，Agent 由你选择。</h2>
      <p>创建访问密钥，选择知识库与操作权限，再复制配置到支持的客户端。</p>
    </div>
    <span class="protocol-badge">HTTP + MCP</span>
  </div>
  <div class="section-title">
    <h2>
      访问密钥
      <span>{{ keys.length }}</span>
    </h2>
    <button class="text-button" @click="showConfigTemplate">
      <Code2 :size="15" />
      查看配置模板
    </button>
  </div>
  <div v-if="!keys.length" class="empty">
    <div class="empty-icon"><KeyRound :size="30" /></div>
    <h3>为你的 Agent 开一扇门</h3>
    <p>每个密钥独立授权，可随时撤销。</p>
    <button class="secondary" @click="newKey">
      <Plus :size="16" />
      创建第一个密钥
    </button>
  </div>
  <div v-else class="key-list">
    <article v-for="key in keys" :key="key.id" class="key-card">
      <span class="key-icon"><KeyRound :size="21" /></span>
      <div class="key-info">
        <div>
          <h3>{{ key.name }}</h3>
          <span
            class="status"
            :class="
              key.revoked || (key.expires_at && key.expires_at * 1000 < Date.now())
                ? 'cancelled'
                : 'ready'
            "
          >
            {{
              key.revoked
                ? '已撤销'
                : key.expires_at && key.expires_at * 1000 < Date.now()
                  ? '已过期'
                  : '有效'
            }}
          </span>
        </div>
        <code>{{ key.prefix }}••••••••</code>
        <p>{{ key.scopes.map((s) => scopeLabels[s]).join(' · ') }}</p>
        <small>
          授权 {{ key.knowledge_base_ids.length }} 个知识库 · 最近使用
          {{ date(key.last_used_at) }} · 到期
          {{ key.expires_at ? date(key.expires_at) : '长期有效' }}
        </small>
      </div>
      <button v-if="!key.revoked" class="secondary danger" @click="openModal('revoke', key)">
        撤销
      </button>
    </article>
  </div>
  <div class="notice plain">
    <ShieldCheck :size="17" />
    完整密钥仅在创建时显示一次。已有密钥配置使用占位符，请在你的客户端中填入保存的密钥。
  </div>
</template>
