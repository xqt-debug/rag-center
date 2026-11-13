<script setup lang="ts">
import {
  BookOpen,
  Check,
  ChevronDown,
  KeyRound,
  Layers3,
  LogOut,
  Plus,
  Search,
  X,
  Activity,
  LoaderCircle,
} from 'lucide-vue-next'
import { size } from './api'
import { provide } from 'vue'
import { workspaceKey } from './context'
import { useWorkspace } from './composables/useWorkspace'
import LoginView from './components/LoginView.vue'
import LibraryView from './components/LibraryView.vue'
import SearchView from './components/SearchView.vue'
import KeysView from './components/KeysView.vue'
import ActivityView from './components/ActivityView.vue'
import WorkspaceDialogs from './components/WorkspaceDialogs.vue'
const workspace = useWorkspace()
provide(workspaceKey, workspace)
const {
  user,
  userLabel,
  userAvatar,
  health,
  booting,
  busy,
  page,
  stats,
  toast,
  error,
  modal,
  titles,
  navigate,
  logout,
  openModal,
  newKey,
} = workspace
</script>

<template>
  <div v-if="booting" class="boot">
    <Layers3 :size="34" />
    <span>正在打开你的知识空间…</span>
  </div>
  <LoginView v-else-if="!user" />
  <div v-else class="app-layout">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="navigate('library')">
        <span class="brand-icon"><Layers3 :size="21" /></span>
        RAG
        <b>Center</b>
      </a>
      <div class="workspace">
        <span class="workspace-avatar">私</span>
        <div>
          <strong>我的知识空间</strong>
          <small>PERSONAL WORKSPACE</small>
        </div>
        <ChevronDown :size="14" />
      </div>
      <div class="nav-label">工作台</div>
      <nav>
        <button
          v-for="item in [
            ['library', '知识库'],
            ['search', '搜索实验室'],
            ['keys', 'MCP 接入'],
            ['activity', '操作记录'],
          ]"
          :key="item[0]"
          :class="{ active: page === item[0] }"
          @click="navigate(item[0]!)"
        >
          <component
            :is="
              { library: BookOpen, search: Search, keys: KeyRound, activity: Activity }[item[0]!]
            "
            :size="18"
          />
          <span>{{ item[1] }}</span>
          <span v-if="item[0] === 'keys'" class="nav-badge">MCP</span>
        </button>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="storage">
        <div>
          <span>存储空间</span>
          <strong>{{ size(stats.storage_bytes || 0) }}</strong>
        </div>
        <div class="meter">
          <span
            :style="{
              width:
                Math.min(
                  100,
                  (100 * (stats.storage_bytes || 0)) / (stats.storage_quota_bytes || 1),
                ) + '%',
            }"
          ></span>
        </div>
        <small>
          共 {{ size(stats.storage_quota_bytes || 0) }} · {{ stats.documents || 0 }} /
          {{ stats.max_documents || 0 }} 个文件
        </small>
      </div>
      <button class="profile" @click="logout">
        <span class="avatar">{{ userAvatar }}</span>
        <span>
          <strong>{{ userLabel }}</strong>
          <small>{{ user.username ? '测试账户' : '个人账户' }}</small>
        </span>
        <LogOut :size="16" />
      </button>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <span>
          我的工作空间
          <span class="slash">/</span>
          <strong>{{ titles[page] }}</strong>
        </span>
        <span class="top-status">
          <span class="live-dot"></span>
          {{ health.environment === 'production' ? '私人知识服务' : '本地开发环境' }}
        </span>
      </header>
      <main>
        <div v-if="error && !modal" class="error global-error" role="alert">
          <span>{{ error }}</span>
          <button class="icon-btn" @click="error = ''">
            <X :size="16" />
          </button>
        </div>
        <div class="page-heading">
          <div>
            <span class="eyebrow">
              {{
                {
                  library: 'YOUR KNOWLEDGE, ORGANIZED',
                  search: 'FIND THE SOURCE',
                  keys: 'CONNECT YOUR AGENT',
                  activity: 'WORKSPACE ACTIVITY',
                }[page]
              }}
            </span>
            <h1>
              {{ titles[page] }}
              <span class="heading-dot">.</span>
            </h1>
            <p>
              {{
                {
                  library: '让每一份资料，都能在需要时被找到。',
                  search: '从语义到关键词，查看每一次检索的真实依据。',
                  keys: '把你的知识空间，连接到你自己的 Agent。',
                  activity: '查看知识空间中的管理操作与变更。',
                }[page]
              }}
            </p>
          </div>
          <button
            v-if="page === 'library'"
            class="primary"
            @click="openModal('base', { name: '', description: '' })"
          >
            <Plus :size="17" />
            新建知识库
          </button>
          <button v-if="page === 'keys'" class="primary" @click="newKey">
            <Plus :size="17" />
            创建密钥
          </button>
        </div>
        <LibraryView v-if="page === 'library'" />
        <SearchView v-else-if="page === 'search'" />
        <KeysView v-else-if="page === 'keys'" />
        <ActivityView v-else />
        <footer class="main-footer">
          <span>RAG CENTER</span>
          <span>把知识连接到可能性。</span>
        </footer>
      </main>
    </div>
  </div>
  <WorkspaceDialogs />
  <div v-if="toast" class="toast" role="status">
    <Check :size="17" />
    {{ toast }}
  </div>
  <div v-if="busy && user" class="working-indicator">
    <LoaderCircle :size="16" />
    处理中
  </div>
</template>
