<script setup lang="ts">
import { computed } from 'vue'
import { Check, LoaderCircle } from 'lucide-vue-next'
import { date } from '../api'
import {
  activeIndexStep,
  indexSteps,
  isIndexing,
  isRealIndexReady,
  readyIndexLabel,
} from '../documentIndex'
import { statusLabels } from '../labels'
import type { DocumentRecord } from '../types'

const props = defineProps<{ document: DocumentRecord }>()
const step = computed(() => activeIndexStep(props.document.status))
const previousVersion = computed(
  () =>
    props.document.current_version &&
    props.document.current_version !== props.document.latest_version,
)
</script>

<template>
  <div class="document-index-status">
    <span
      class="status"
      :class="
        document.status === 'ready' && !isRealIndexReady(document)
          ? 'index-warning'
          : document.status
      "
    >
      <Check v-if="isRealIndexReady(document)" :size="12" />
      <LoaderCircle v-else-if="isIndexing(document.status)" class="index-spinner" :size="12" />
      <span v-else></span>
      {{
        document.status === 'ready'
          ? readyIndexLabel(document)
          : statusLabels[document.status] || document.status
      }}
    </span>
    <ol
      v-if="isIndexing(document.status) || isRealIndexReady(document)"
      class="index-steps"
      aria-label="文档索引处理阶段"
    >
      <li
        v-for="(label, index) in indexSteps"
        :key="label"
        :class="{
          done: document.status === 'ready' || index < step,
          active: document.status !== 'ready' && index === step,
        }"
        :aria-current="document.status !== 'ready' && index === step ? 'step' : undefined"
      >
        <span class="index-step-dot"></span>
        {{ label }}
      </li>
    </ol>
    <small v-if="document.indexed_at" class="index-completion-time">
      {{
        document.index_kind === 'test'
          ? '测试索引生成于'
          : document.needs_reindex
            ? '原索引生成于'
            : previousVersion
              ? '上一版本完成于'
              : '完成于'
      }}
      {{ date(document.indexed_at) }}
    </small>
    <small v-if="document.status === 'ready' && !isRealIndexReady(document)" class="index-failure">
      {{
        document.index_kind === 'test'
          ? '未调用真实向量模型，配置后请重建'
          : '当前模型与索引不一致，请重建'
      }}
    </small>
    <small v-if="document.status === 'failed'" class="index-failure" role="alert">
      {{ document.error || '索引处理失败，请重试' }}
    </small>
    <small v-else-if="document.status === 'stored'" class="muted">
      原图可预览、下载；未建立文字索引
    </small>
    <small v-else-if="document.status === 'queued'" class="muted">
      等待后台处理，状态将自动更新
    </small>
  </div>
</template>
