<script setup lang="ts">
import {
  ArrowRight,
  ArrowUpRight,
  Code2,
  FileText,
  Layers3,
  Search,
  ShieldCheck,
} from 'lucide-vue-next'

import { useWorkspaceContext } from '../context'
const workspace = useWorkspaceContext()
const { busy, error, login, username, password, confirmPassword, registering, toggleRegistration } =
  workspace
</script>

<template>
  <div class="login-layout">
    <section class="login-story">
      <div class="brand">
        <span class="brand-icon"><Layers3 :size="23" /></span>
        RAG
        <b>Center</b>
      </div>
      <div class="story-body">
        <span class="eyebrow">A HOME FOR YOUR KNOWLEDGE</span>
        <h1>
          让你的资料，
          <br />
          成为 Agent 的
          <br />
          <em>可靠依据。</em>
        </h1>
        <p>
          把散落的文件整理到一个地方。
          <br />
          通过 MCP，让你喜欢的 Agent 找到答案的出处。
        </p>
        <div class="story-tags">
          <span>
            <Search :size="14" />
            混合检索
          </span>
          <span>
            <ShieldCheck :size="14" />
            独立授权
          </span>
          <span>
            <Code2 :size="14" />
            MCP 接入
          </span>
        </div>
      </div>
      <div class="story-bottom">
        <span class="live-dot"></span>
        Your knowledge. Your agents.
      </div>
      <div class="story-art">
        <div class="orbit o1"></div>
        <div class="orbit o2"></div>
        <div class="art-card">
          <FileText />
          <span>知识，有迹可循</span>
        </div>
      </div>
    </section>
    <section class="login-form">
      <div class="login-top">
        你的私人知识工作台
        <ArrowUpRight :size="16" />
      </div>
      <form @submit.prevent="login" class="login-box">
        <span class="mini-label">{{ registering ? '创建账号' : '开始使用' }}</span>
        <h2>{{ registering ? '注册 RAG Center' : '欢迎回来' }}</h2>
        <p class="muted">
          {{ registering ? '创建账号，开启你的知识空间。' : '使用账号和密码进入知识空间。' }}
        </p>
        <label>
          账号
          <input
            v-model="username"
            placeholder="输入账号"
            autocomplete="username"
            maxlength="64"
            pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}"
            required
            :disabled="busy"
          />
        </label>
        <p v-if="registering" class="muted">
          1–64 位，以字母或数字开头，可包含下划线、点和短横线。
        </p>
        <label>
          密码
          <input
            v-model="password"
            type="password"
            placeholder="输入密码"
            :autocomplete="registering ? 'new-password' : 'current-password'"
            :minlength="registering ? 8 : 1"
            maxlength="256"
            required
            :disabled="busy"
          />
        </label>
        <label v-if="registering">
          确认密码
          <input
            v-model="confirmPassword"
            type="password"
            placeholder="再次输入密码"
            autocomplete="new-password"
            minlength="8"
            maxlength="256"
            required
            :disabled="busy"
          />
        </label>
        <p v-if="registering" class="muted">密码至少 8 位。</p>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="primary login-submit" :disabled="busy">
          <span>
            {{ busy ? '正在连接…' : registering ? '注册并进入知识空间' : '登录知识空间' }}
          </span>
          <ArrowRight :size="18" />
        </button>
        <button type="button" :disabled="busy" @click="toggleRegistration">
          {{ registering ? '已有账号？去登录' : '没有账号？创建账号' }}
        </button>
        <p class="login-note">
          <ShieldCheck :size="14" />
          资料按账户隔离，由你决定 Agent 的访问范围
        </p>
      </form>
      <footer>
        RAG CENTER
        <span>·</span>
        KNOWLEDGE, CONNECTED.
      </footer>
    </section>
  </div>
</template>
