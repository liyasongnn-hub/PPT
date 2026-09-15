<template>
  <main class="auth-page">
    <section class="showcase"><div class="showcase-inner">
      <div class="brand"><div class="brand-mark">P</div><div><strong>PPT Agent</strong><span>智能演示创作空间</span></div></div>
      <div class="showcase-gallery" aria-label="生成效果预览">
        <figure v-for="(shot, index) in showcaseShots" :key="shot" class="shot">
          <img :src="shot" :alt="`演示稿预览 ${index + 1}`" loading="lazy" />
        </figure>
      </div>
      <div class="showcase-copy"><p class="kicker"><span class="kicker-dot"></span> AI POWERED PRESENTATION STUDIO</p><h1>把想法，<br /><em>讲成一场好演示。</em></h1><p class="lead">从一个主题到一套完整演示，PPT Agent 帮你梳理结构、生成内容，并在编辑器里完成最后的表达。</p></div>
      <div class="feature-grid"><div v-for="feature in features" :key="feature.title" class="feature"><component :is="feature.icon" theme="outline" size="24" strokeWidth="4" /><div><strong>{{ feature.title }}</strong><span>{{ feature.detail }}</span></div></div></div>
      <div class="showcase-footer"><span>01</span><div class="progress"><i></i></div><span>让内容更有说服力</span></div>
    </div></section>
    <section class="auth-side">
      <div class="auth-top"><span>{{ mode === 'login' ? '还没有账号？' : '已有账号？' }}</span><button type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')">{{ mode === 'login' ? '注册账号' : '返回登录' }} <span>↗</span></button></div>
      <div class="auth-card"><div class="auth-heading"><p class="eyebrow">{{ mode === 'login' ? 'WELCOME BACK' : 'GET STARTED' }}</p><h2>{{ mode === 'login' ? '登录你的工作台' : '创建你的创作空间' }}</h2><p>{{ mode === 'login' ? '继续管理项目，或开启一份新的演示。' : '注册后即可保存项目并使用 AI 生成功能。' }}</p></div>
        <div class="mode-switch" role="tablist"><button type="button" :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</button><button type="button" :class="{ active: mode === 'register' }" @click="switchMode('register')">注册</button></div>
        <form class="auth-form" @submit.prevent="submit"><label v-if="mode === 'register'"><span>显示名称</span><input v-model.trim="form.display_name" autocomplete="name" maxlength="80" placeholder="例如：李小明" required /></label><label><span>邮箱</span><input v-model.trim="form.email" type="email" autocomplete="email" maxlength="160" placeholder="name@example.com" required /></label><label><span>密码</span><input v-model="form.password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" minlength="8" maxlength="128" placeholder="至少 8 位字符" required /></label><p v-if="error" class="error" role="alert">{{ error }}</p><button class="submit-btn" :disabled="busy"><span>{{ busy ? '正在验证...' : mode === 'login' ? '进入工作台' : '创建账号并开始' }}</span><span class="arrow">→</span></button></form>
        <p class="security"><span>◉</span> 你的项目内容将安全保存在个人空间</p>
      </div><footer>DeepSeek 内容生成 · 通义千问向量检索</footer>
    </section>
  </main>
</template>

<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api, { PLATFORM_TOKEN_KEY, clearPlatformSession, savePlatformSession } from '@/services'
const router = useRouter(); const mode = ref<'login' | 'register'>('login'); const busy = ref(false); const error = ref(''); const form = reactive({ display_name: '', email: '', password: '' })
const features = [{ title: '结构化创作', detail: '从主题生成清晰大纲', icon: 'IconListView' }, { title: '智能视觉', detail: '模板与版式一键匹配', icon: 'IconGraphicDesign' }, { title: '自由编辑', detail: '像专业工具一样调整', icon: 'IconEdit' }, { title: '多格式导出', detail: 'PPTX、PDF 随时下载', icon: 'IconDownload' }]
const showcaseShots = ['/api/data/template_1.jpg', '/api/data/template_4.jpg', '/api/data/template_3.jpg']
function switchMode(next: 'login' | 'register') { mode.value = next; error.value = '' }
async function submit() { busy.value = true; error.value = ''; try { const result = mode.value === 'login' ? await api.platformLogin({ email: form.email, password: form.password }) : await api.platformRegister(form); savePlatformSession(result); await router.replace('/dashboard') } catch (err: any) { error.value = err.message || (mode.value === 'login' ? '登录失败，请检查账号和密码' : '注册失败，请稍后重试') } finally { busy.value = false } }
onMounted(async () => { if (!localStorage.getItem(PLATFORM_TOKEN_KEY)) return; try { await api.platformMe(); await router.replace('/dashboard') } catch { clearPlatformSession() } })
</script>

<style scoped>
:global(body) { margin: 0; background: var(--sun-25); color: var(--sun-ink); font-family: Inter, "Microsoft YaHei", sans-serif; }
.auth-page { min-height: 100vh; display: grid; grid-template-columns: minmax(480px, 5fr) minmax(400px, 3fr); }
.showcase { position: relative; overflow: hidden; background: var(--sun-grad-dark); color: #fff4ea; }
.showcase:after { content: ''; position: absolute; width: 460px; height: 460px; right: -220px; bottom: -220px; border: 1px solid rgba(255, 169, 77, .2); border-radius: 50%; box-shadow: 0 0 0 70px rgba(255, 169, 77, .05), 0 0 0 140px rgba(255, 169, 77, .035); }
.showcase-inner { position: relative; z-index: 1; width: auto; max-width: 1020px; margin: 0 auto 0 clamp(24px, 4%, 56px); min-height: 100%; box-sizing: border-box; padding: 40px 72px 32px 48px; display: flex; flex-direction: column; }
.brand { display: flex; align-items: center; gap: 11px; }
.brand-mark { width: 40px; height: 40px; display: grid; place-items: center; border-radius: 10px; background: var(--sun-grad-soft); color: #3d1f0e; font-size: 21px; font-weight: 800; box-shadow: 0 6px 18px rgba(255, 122, 24, .32); }
.brand strong, .brand span { display: block; }
.brand strong { font-size: 16px; }
.brand span { margin-top: 4px; color: #c8a288; font-size: 11.5px; }

/* 上方展示图：三张模板预览错落排布 */
.showcase-gallery { position: relative; display: flex; align-items: flex-end; margin: auto 0 28px; }
.showcase-gallery .shot { flex: 1; min-width: 0; margin: 0; border-radius: 13px; overflow: hidden; border: 1px solid rgba(255, 169, 77, .3); background: #241209; box-shadow: 0 26px 54px rgba(0, 0, 0, .5); }
.showcase-gallery .shot img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
.showcase-gallery .shot:nth-child(1) { z-index: 1; transform: rotate(-3deg) translateY(5px); }
.showcase-gallery .shot:nth-child(2) { z-index: 3; flex: 1.26; margin: 0 -22px; transform: translateY(-9px); }
.showcase-gallery .shot:nth-child(3) { z-index: 2; transform: rotate(3deg) translateY(5px); }

.showcase-copy { max-width: 760px; margin: 0 0 30px; }
.kicker { display: flex; align-items: center; gap: 9px; margin: 0 0 20px; color: var(--sun-300); font-size: 11px; letter-spacing: 1.2px; }
.kicker-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--sun-300); box-shadow: 0 0 0 4px rgba(255, 169, 77, .18); }
.showcase h1 { margin: 0; font-size: clamp(36px, 3.6vw, 54px); line-height: 1.15; letter-spacing: 0; }
.showcase h1 em { color: var(--sun-300); font-style: normal; }
.lead { max-width: 560px; margin: 22px 0 0; color: #c8a288; font-size: 15.5px; line-height: 1.85; }
.feature-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; max-width: 780px; }
.feature { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 12px; min-height: 128px; box-sizing: border-box; padding: 24px 20px; border: 1px solid rgba(255, 169, 77, .26); border-radius: 12px; background: rgba(255, 255, 255, .07); color: var(--sun-300); }
.feature strong, .feature span { display: block; }
.feature strong { color: #fff4ea; font-size: 16px; }
.feature span { margin-top: 8px; color: #c8a288; font-size: 13px; line-height: 1.55; }
.showcase-footer { display: flex; align-items: center; gap: 13px; margin-top: 28px; color: #b98d68; font-size: 11px; }
.progress { width: 110px; height: 2px; background: rgba(255, 169, 77, .2); }
.progress i { display: block; width: 35%; height: 100%; background: var(--sun-400); }.auth-side { display: flex; flex-direction: column; box-sizing: border-box; padding: 34px clamp(28px, 6vw, 88px) 24px; background: var(--sun-25); }
.auth-top { display: flex; align-items: center; justify-content: flex-end; gap: 11px; color: var(--sun-muted); font-size: 12px; }
.auth-top button { border: 0; padding: 5px 0; color: var(--sun-600); background: transparent; cursor: pointer; font: inherit; font-weight: 700; }
.auth-top button span { margin-left: 4px; font-size: 15px; }
.auth-card { width: min(405px, 100%); margin: auto; padding: 38px 40px 29px; box-sizing: border-box; border: 1px solid var(--sun-line); border-radius: 12px; background: #fff; box-shadow: 0 18px 45px rgba(90, 45, 10, .1); }
.eyebrow { margin: 0 0 10px; color: var(--sun-500); font-size: 10px; font-weight: 800; letter-spacing: 1.5px; }
.auth-heading h2 { margin: 0; font-size: 27px; letter-spacing: 0; }
.auth-heading > p:last-child { margin: 10px 0 0; color: var(--sun-muted); font-size: 13px; line-height: 1.55; }
.mode-switch { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin: 29px 0 23px; padding: 4px; border-radius: 7px; background: var(--sun-50); }
.mode-switch button { border: 0; border-radius: 5px; padding: 10px; color: var(--sun-muted); background: transparent; cursor: pointer; font: inherit; font-size: 13px; }
.mode-switch button.active { color: var(--sun-ink); background: #fff; box-shadow: 0 2px 7px rgba(90, 45, 10, .12); font-weight: 700; }.auth-form, label { display: flex; flex-direction: column; }
.auth-form { gap: 17px; }
label { gap: 7px; color: var(--sun-ink-soft); font-size: 12px; font-weight: 700; }
input { width: 100%; height: 44px; box-sizing: border-box; border: 1px solid var(--sun-line); border-radius: 6px; padding: 0 13px; color: var(--sun-ink); background: var(--sun-50); outline: none; font: inherit; }
input:focus { border-color: var(--sun-400); box-shadow: 0 0 0 3px rgba(255, 122, 24, .14); }
.submit-btn { display: flex; align-items: center; justify-content: space-between; height: 46px; margin-top: 2px; padding: 0 16px 0 18px; border: 0; border-radius: 6px; color: #fff; background: var(--sun-grad); cursor: pointer; font: inherit; font-size: 13px; font-weight: 700; }
.submit-btn:hover { background: var(--sun-600); }
.submit-btn:disabled { opacity: .6; cursor: not-allowed; }
.arrow { font-size: 20px; font-weight: 400; }
.error { margin: 0; padding: 9px 11px; border: 1px solid #f4c9b4; border-radius: 5px; color: #b23b2e; background: #fff5f1; font-size: 12px; line-height: 1.45; }
.security { margin: 21px 0 0; color: var(--sun-muted-light); text-align: center; font-size: 11px; }
.security span { margin-right: 5px; color: var(--sun-400); }
footer { color: var(--sun-muted-light); text-align: center; font-size: 11px; }@media (max-width: 900px) { .auth-page { grid-template-columns: 1fr; }.showcase { min-height: 580px; }.showcase-inner { padding: 30px 26px; }.showcase-gallery { margin: 44px 0 30px; }.showcase-copy { margin: 0 0 32px; }.auth-side { min-height: 570px; padding: 24px 18px; }.auth-card { margin: 42px auto; } }
@media (max-width: 470px) { .feature-grid { grid-template-columns: 1fr; }.showcase h1 { font-size: 34px; }.auth-card { padding: 30px 22px 24px; } }
</style>
