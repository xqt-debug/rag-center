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
const {
  busy,
  phone,
  code,
  devCode,
  cooldown,
  error,
  sendCode,
  login,
  username,
  password,
  passwordLogin,
} = workspace
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
        <span class="mini-label">开始连接</span>
        <h2>欢迎来到 RAG Center</h2>
        <p class="muted">
          {{
            passwordLogin
              ? '使用测试账号和密码进入知识空间。'
              : '使用手机号登录，首次登录将自动创建账户。'
          }}
        </p>
        <template v-if="passwordLogin">
          <label>
            账号
            <input
              v-model="username"
              placeholder="输入测试账号"
              autocomplete="username"
              maxlength="64"
              required
            />
          </label>
          <label>
            密码
            <input
              v-model="password"
              type="password"
              placeholder="输入测试密码"
              autocomplete="current-password"
              maxlength="256"
              required
            />
          </label>
          <div class="notice">测试模式 · 账号密码登录</div>
        </template>
        <template v-else>
          <label>
            手机号码
            <input
              v-model="phone"
              type="tel"
              placeholder="输入中国大陆手机号"
              maxlength="11"
              autocomplete="tel"
              required
              pattern="1[3-9][0-9]{9}"
            />
          </label>
          <label>
            短信验证码
            <div class="code-input">
              <input
                v-model="code"
                placeholder="6 位验证码"
                maxlength="6"
                inputmode="numeric"
                autocomplete="one-time-code"
                required
                pattern="[0-9]{6}"
              />
              <button
                type="button"
                :disabled="busy || cooldown > 0 || phone.length !== 11"
                @click="sendCode"
              >
                {{ cooldown ? `${cooldown}s 后重试` : '获取验证码' }}
              </button>
            </div>
          </label>
          <div v-if="devCode" class="notice">开发模式 · 本次验证码 {{ devCode }}，已自动填入。</div>
        </template>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="primary login-submit" :disabled="busy">
          <span>{{ busy ? '正在连接…' : '登录，进入知识空间' }}</span>
          <ArrowRight :size="18" />
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
