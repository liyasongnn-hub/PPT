<template>
  <div class="workspace-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">P</div>
        <div><strong>PPT Agent</strong><span>智能演示创作空间</span></div>
      </div>

      <div class="workspace-label">工作台</div>
      <nav class="side-nav" aria-label="工作台导航">
        <button v-for="item in navItems" :key="item.to" class="nav-item" :class="{ active: isActive(item.to) }" @click="router.push(item.to)">
          <component :is="item.icon" theme="outline" size="18" />
          <span>{{ item.label }}</span>
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </button>
      </nav>

      <div class="sidebar-bottom">
        <div class="tip-card"><span class="tip-icon">✦</span><div><strong>灵感助手</strong><p>输入一个主题，马上开始创作</p></div></div>
        <button class="account" @click="logout"><span class="avatar">{{ initials }}</span><span class="account-info"><strong>{{ user?.display_name || '创作者' }}</strong><small>{{ user?.email || '已登录' }}</small></span><IconLogout theme="outline" size="17" /></button>
      </div>
    </aside>

    <main class="workspace-main"><slot /></main>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearPlatformSession } from '@/services'

const props = defineProps<{ user?: any }>()
const router = useRouter()
const route = useRoute()
const navItems = [
  { to: '/dashboard', label: '项目概览', icon: 'IconListView' },
  { to: '/create', label: 'AI 创作（先生成大纲）', icon: 'IconMagic', badge: '01' },
]
const isActive = (to: string) => route.path === to || (to === '/dashboard' && route.path === '/') || (to === '/create' && ['/ppt', '/editor'].includes(route.path))
const initials = computed(() => (props.user?.display_name || 'P').slice(0, 1).toUpperCase())
function logout() {
  clearPlatformSession()
  router.replace('/login')
}
</script>

<style scoped>
.workspace-shell { min-height: 100vh; display: flex; background: var(--sun-25); color: var(--sun-ink); font-family: Inter, "Microsoft YaHei", sans-serif; }
.sidebar { width: 288px; flex: 0 0 288px; display: flex; flex-direction: column; box-sizing: border-box; padding: 30px 20px 20px; background: var(--sun-grad-dark); color: #f7e6d8; }
.brand { display: flex; align-items: center; gap: 12px; padding: 0 10px 32px; }
.brand-mark { width: 42px; height: 42px; display: grid; place-items: center; border-radius: 11px; background: var(--sun-grad-soft); color: #3d1f0e; font-weight: 800; font-size: 22px; box-shadow: 0 6px 16px rgba(255, 122, 24, .3); }
.brand strong, .brand span { display: block; letter-spacing: 0; }.brand strong { font-size: 16px; }.brand span { margin-top: 4px; font-size: 12px; color: #c8a288; }
.workspace-label { padding: 0 12px 12px; color: #b98d68; font-size: 11.5px; letter-spacing: 1.3px; text-transform: uppercase; }
.side-nav { display: grid; gap: 6px; }.nav-item { display: flex; align-items: center; gap: 12px; width: 100%; padding: 13px 14px; border: 0; border-radius: 9px; background: transparent; color: #ddbda1; text-align: left; cursor: pointer; font: inherit; font-size: 14.5px; }.nav-item:hover { color: #fff; background: rgba(255, 255, 255, .08); }.nav-item.active { color: #fff; background: linear-gradient(90deg, rgba(255, 122, 24, .34), rgba(255, 122, 24, .1)); box-shadow: inset 3px 0 var(--sun-400); font-weight: 600; }.nav-badge { margin-left: auto; padding: 2px 6px; border-radius: 5px; background: var(--sun-100); color: var(--sun-600); font-size: 10px; font-weight: 800; }
.sidebar-bottom { margin-top: auto; }.tip-card { display: flex; gap: 11px; padding: 15px 13px; border: 1px solid rgba(255, 169, 77, .26); border-radius: 10px; background: rgba(255, 255, 255, .06); }.tip-icon { color: var(--sun-300); font-size: 19px; }.tip-card strong { font-size: 13px; }.tip-card p { margin: 5px 0 0; color: #c3a183; font-size: 12px; line-height: 1.5; }.account { display: flex; align-items: center; gap: 10px; width: 100%; margin-top: 16px; padding: 12px 8px; border: 0; border-top: 1px solid rgba(255, 255, 255, .12); background: transparent; color: #f3e0d0; cursor: pointer; text-align: left; font: inherit; }.avatar { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: var(--sun-grad-soft); color: #3d1f0e; font-size: 13px; font-weight: 800; }.account-info { flex: 1; min-width: 0; }.account-info strong, .account-info small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.account-info strong { font-size: 13px; }.account-info small { margin-top: 3px; color: #bd9b81; font-size: 11px; }
.workspace-main { flex: 1; min-width: 0; }.workspace-main :deep(button) { letter-spacing: 0; }
@media (max-width: 900px) { .sidebar { width: 84px; flex-basis: 84px; padding: 20px 12px; }.brand { justify-content: center; padding: 0 0 26px; }.brand > div:last-child, .workspace-label, .nav-item span, .tip-card, .account-info { display: none; }.nav-item { justify-content: center; padding: 14px 0; }.nav-badge { display: block; position: absolute; margin: -25px 0 0 27px; }.account { justify-content: center; padding: 13px 0; }.account :deep(svg) { display: none; } }
</style>
