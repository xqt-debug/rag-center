<script setup lang="ts">
import { Activity } from 'lucide-vue-next'
import { date } from '../api'
import { useWorkspaceContext } from '../context'
const workspace = useWorkspaceContext()
const { audit, actionLabels } = workspace
</script>

<template>
  <div v-if="!audit.length" class="empty">
    <Activity :size="35" />
    <h3>这里会留下管理操作记录</h3>
    <p>创建知识库、修改文件和密钥管理都会记录在这里。</p>
  </div>
  <div v-else class="audit-list">
    <article v-for="event in audit" :key="event.id">
      <span class="audit-dot"></span>
      <div>
        <strong>{{ actionLabels[event.action] || event.action }}</strong>
        <p>
          {{ event.actor.startsWith('key:') ? 'Agent 密钥' : '网页用户' }} ·
          {{ event.target_id.slice(0, 12) }}
        </p>
      </div>
      <time>{{ date(event.created_at) }}</time>
    </article>
  </div>
  <p class="muted small">显示最近 30 条管理操作。不记录文件正文与凭证明文。</p>
</template>
