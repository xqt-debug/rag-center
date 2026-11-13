<script setup lang="ts">
import { ArrowRight, ArrowUpRight, FileText, Search, Sparkles } from 'lucide-vue-next'

import { useWorkspaceContext } from '../context'
const workspace = useWorkspaceContext()
const {
  busy,
  bases,
  query,
  mode,
  searchBase,
  results,
  warnings,
  searched,
  expanded,
  search,
  expand,
} = workspace
</script>

<template>
  <section class="search-panel">
    <form @submit.prevent="search">
      <div class="search-input">
        <Search :size="22" />
        <input v-model="query" placeholder="你想从资料中找到什么？" maxlength="2000" required />
        <button class="primary" :disabled="busy || !query.trim()">
          {{ busy ? '检索中…' : '搜索' }}
          <ArrowRight :size="16" />
        </button>
      </div>
      <div class="search-options">
        <div class="segmented">
          <button
            v-for="item in [
              ['hybrid', '混合检索'],
              ['semantic', '向量检索'],
              ['keyword', '关键词检索'],
            ]"
            :key="item[0]"
            type="button"
            :class="{ active: mode === item[0] }"
            @click="mode = item[0]!"
          >
            {{ item[1] }}
          </button>
        </div>
        <select v-model="searchBase" aria-label="检索知识库">
          <option value="">全部知识库</option>
          <option v-for="b in bases" :value="b.id" :key="b.id">{{ b.name }}</option>
        </select>
      </div>
    </form>
    <p class="search-tip">
      <Sparkles :size="14" />
      混合检索结合语义与关键词匹配，返回资料原文和出处。
    </p>
  </section>
  <div v-for="warning in warnings" :key="warning" class="notice">{{ warning }}</div>
  <div v-if="!searched" class="empty search-empty">
    <Search :size="35" />
    <h3>从一个问题开始</h3>
    <p>试试资料里的概念、项目名称，或者一个精确编号。</p>
  </div>
  <div v-else-if="!results.length" class="empty">
    <Search :size="32" />
    <h3>没有找到相关片段</h3>
    <p>换个关键词，或检查资料是否已完成处理。</p>
  </div>
  <template v-else>
    <div class="section-title">
      <h2>
        检索结果
        <span>{{ results.length }}</span>
      </h2>
      <span class="muted small">来自你的授权资料</span>
    </div>
    <article class="result-card" v-for="(hit, index) in results" :key="hit.chunk_id">
      <div class="result-heading">
        <span class="result-index">{{ String(index + 1).padStart(2, '0') }}</span>
        <FileText :size="17" />
        <strong>{{ hit.title }}</strong>
        <span class="muted small">{{ hit.page ? `第 ${hit.page} 页` : '文本片段' }}</span>
      </div>
      <p class="result-text">{{ expanded[hit.chunk_id] || hit.text }}</p>
      <div class="result-bottom">
        <span>{{ hit.citation }}</span>
        <button class="text-button" @click="expand(hit)">
          展开上下文
          <ArrowUpRight :size="14" />
        </button>
      </div>
    </article>
  </template>
</template>
