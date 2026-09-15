<template>
  <WorkspaceShell :user="user">
    <div class="dashboard">
      <header class="topbar"><div><p class="eyebrow">工作台 / OVERVIEW</p><h1>你好，{{ user?.display_name || '创作者' }}</h1><p class="muted">今天也让一份好演示，替你表达重要的想法。</p></div><div class="top-actions"><span class="health" :class="healthReady ? 'ok' : 'warn'"><i></i>{{ healthReady ? '服务就绪' : '检查服务' }}</span><button class="new-btn" @click="router.push('/create')"><span>＋</span> 新建 PPT</button></div></header>
      <section class="stats"><div><span class="stat-label">全部项目</span><strong>{{ projects.length }}</strong><small>持续积累你的创作</small></div><div><span class="stat-label">进行中</span><strong>{{ activeCount }}</strong><small>AI 正在处理的任务</small></div><div><span class="stat-label">本周生成</span><strong>{{ generatedCount }}</strong><small>把灵感变成页面</small></div></section>
      <section class="quick-start"><div class="section-head"><div><p class="eyebrow">CREATE A PRESENTATION</p><h2>三步完成一份演示</h2></div><span>按顺序完成内容规划、视觉选择与精细编辑</span></div><div class="quick-grid"><button class="quick-card featured" @click="router.push('/create')"><span class="step-number">01</span><span class="quick-icon">✦</span><strong>生成内容大纲</strong><p>输入主题或上传资料，由 AI 梳理章节和每页要点。</p><span class="quick-link">从第一步开始 <b>→</b></span></button><button class="quick-card" @click="openLatest('template')"><span class="step-number">02</span><span class="quick-icon muted-icon">▦</span><strong>选择演示模板</strong><p>大纲确认后选择版式，让内容匹配合适的视觉风格。</p><span class="quick-link">继续已有项目 <b>→</b></span></button><button class="quick-card" @click="openLatest('editor')"><span class="step-number">03</span><span class="quick-icon muted-icon">↗</span><strong>编辑并导出</strong><p>调整页面细节，检查效果并导出最终演示文件。</p><span class="quick-link">继续已有项目 <b>→</b></span></button></div></section>
      <section class="projects-section"><div class="section-head"><div><p class="eyebrow">YOUR PROJECTS</p><h2>最近的项目</h2></div><button class="refresh" @click="loadProjects">刷新列表 <span>↻</span></button></div><div v-if="projects.length === 0" class="empty"><div class="empty-mark">✦</div><strong>还没有项目</strong><p>从上方选择一种创作方式，开始你的第一份演示。</p><button @click="router.push('/create')">创建第一个项目 →</button></div><div v-else class="project-list"><article v-for="project in projects" :key="project.id" class="project-row"><div class="project-symbol">P</div><div class="project-main"><h3>{{ project.title }}</h3><p>{{ project.prompt }}</p><time>{{ formatTime(project.updated_at) }}</time></div><span :class="['badge', statusClass(project.status)]">{{ statusLabel(project.status) }}</span><div class="project-actions"><button @click="continueProject(project)">{{ continueLabel(project) }}</button><button class="quiet" @click="checkQuality(project)">质量检查</button></div><p v-if="projectMessages[project.id]" class="job-message">{{ projectMessages[project.id] }}</p></article></div></section>
    </div>
  </WorkspaceShell>
</template>

<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/services'
import message from '@/utils/message'
import WorkspaceShell from '@/components/WorkspaceShell.vue'
const router = useRouter(); const user = ref<any>(null); const error = ref(''); const healthReady = ref(false); const projects = ref<any[]>([]); const projectMessages = reactive<Record<string, string>>({})
const activeCount = computed(() => projects.value.filter(p => ['QUEUED', 'RUNNING', 'ACTIVE', 'GENERATING', 'EDITING'].includes(p.status)).length); const generatedCount = computed(() => projects.value.filter(p => hasDocument(p) || ['OUTLINE_REVIEW', 'COMPLETED'].includes(p.status)).length)
async function loadProjects() { try { projects.value = (await api.listProjects()).data || [] } catch (err: any) { error.value = err.message || '项目加载失败' } }
function hasDocument(project: any) { return Array.isArray(project.document?.slides) && project.document.slides.length > 0 }
function continueProject(project: any) { if (hasDocument(project)) router.push({ name: 'Editor', query: { project_id: project.id } }); else if (project.outline?.trim()) router.push({ name: 'PPT', query: { project_id: project.id } }); else router.push({ name: 'Outline', query: { project_id: project.id } }) }
function continueLabel(project: any) { return hasDocument(project) ? '继续编辑' : project.outline?.trim() ? '选择模板' : '完善大纲' }
function openLatest(stage: 'template' | 'editor') { const project = projects.value.find(item => stage === 'editor' ? hasDocument(item) : Boolean(item.outline?.trim())); if (!project) { message.info(stage === 'editor' ? '还没有已生成的演示文稿，请先完成前两步' : '还没有可选模板的项目，请先生成大纲'); return } router.push({ name: stage === 'editor' ? 'Editor' : 'PPT', query: { project_id: project.id } }) }
async function checkQuality(project: any) { try { const result = await api.qualityCheck(project.id); projectMessages[project.id] = `质量分数 ${result.score} · ${result.status}` } catch (err: any) { projectMessages[project.id] = err.message || '检查失败' } }
async function checkHealth() { try { const response = await fetch('/api/health/ready'); healthReady.value = response.ok && (await response.json()).status === 'READY' } catch { healthReady.value = false } }
function statusClass(status: string) { return status === 'OUTLINE_REVIEW' ? 'review' : status === 'DRAFT' ? 'draft' : 'active' }; function statusLabel(status: string) { const labels: Record<string, string> = { OUTLINE_REVIEW: '大纲已完成', DRAFT: '草稿', GENERATING: '正在生成', EDITING: '编辑中', COMPLETED: '已完成' }; return labels[status] || status || '处理中' }; function formatTime(value: string) { return value ? new Date(value).toLocaleString() : '' }
onMounted(async () => { try { user.value = await api.platformMe(); await loadProjects(); await checkHealth() } catch { router.replace('/login') } })
</script>

<style scoped>
:global(body) { margin: 0; background: var(--sun-25); color: var(--sun-ink); font-family: Inter, "Microsoft YaHei", sans-serif; }
.dashboard { max-width: 1220px; margin: 0 auto; padding: 42px 46px 70px; }
.topbar, .top-actions, .section-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.topbar { padding-bottom: 28px; border-bottom: 1px solid var(--sun-line); }
.eyebrow { margin: 0 0 8px; color: var(--sun-500); font-size: 10px; letter-spacing: 1.4px; font-weight: 800; }
.topbar h1 { margin: 0; font-size: 31px; letter-spacing: 0; }
.muted { margin: 9px 0 0; color: var(--sun-muted); font-size: 13px; }
.health { display: inline-flex; align-items: center; gap: 7px; color: var(--sun-muted); font-size: 11px; }
.health i { width: 7px; height: 7px; border-radius: 50%; background: var(--sun-300); }
.health.ok i { background: var(--sun-500); }
.new-btn { height: 39px; padding: 0 16px; border: 0; border-radius: 7px; color: #fff; background: var(--sun-grad); cursor: pointer; font: inherit; font-size: 12px; font-weight: 700; box-shadow: 0 6px 15px rgba(232, 98, 10, .28); }
.new-btn:hover { background: var(--sun-600); }
.new-btn span { margin-right: 5px; font-size: 18px; vertical-align: -1px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 25px 0 36px; }
.stats > div { padding: 17px 20px; border: 1px solid var(--sun-line); border-radius: 9px; background: #fff; }
.stat-label, .stats small { display: block; color: var(--sun-muted); font-size: 11px; }
.stats strong { display: block; margin: 6px 0 3px; color: var(--sun-600); font-size: 27px; }
.quick-start, .projects-section { margin-top: 30px; }
.section-head { align-items: flex-end; margin-bottom: 16px; }
.section-head h2 { margin: 0; font-size: 19px; }
.section-head > span { color: var(--sun-muted-light); font-size: 12px; }
.quick-grid { display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 12px; }
.quick-card { min-height: 178px; padding: 22px; border: 1px solid var(--sun-line); border-radius: 9px; background: #fff; text-align: left; cursor: pointer; transition: transform .2s, box-shadow .2s, border-color .2s; }
.quick-card:hover { transform: translateY(-2px); border-color: var(--sun-200); box-shadow: 0 10px 24px rgba(120, 58, 10, .1); }
.quick-card.featured { border-color: var(--sun-200); background: var(--sun-50); }
.quick-icon { display: grid; place-items: center; width: 33px; height: 33px; margin-bottom: 18px; border-radius: 8px; background: var(--sun-grad); color: #fff; font-size: 18px; }
.muted-icon { background: var(--sun-100); color: var(--sun-600); }
.quick-card strong { font-size: 15px; }
.quick-card p { min-height: 37px; margin: 8px 0 17px; color: var(--sun-muted); font-size: 12px; line-height: 1.55; }
.quick-link { color: var(--sun-600); font-size: 12px; font-weight: 700; }
.quick-link b { margin-left: 4px; font-size: 16px; font-weight: 400; }
.refresh { border: 0; color: var(--sun-600); background: transparent; cursor: pointer; font: inherit; font-size: 12px; }
.refresh span { margin-left: 4px; font-size: 17px; }
.project-list { overflow: hidden; border: 1px solid var(--sun-line); border-radius: 9px; background: #fff; }
.project-row { position: relative; display: grid; grid-template-columns: 36px minmax(0, 1fr) auto auto; align-items: center; gap: 14px; padding: 17px 20px; border-bottom: 1px solid var(--sun-line-soft); }
.project-row:last-child { border-bottom: 0; }
.project-symbol { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 7px; color: var(--sun-600); background: var(--sun-100); font-weight: 800; }
.project-main { min-width: 0; }
.project-main h3 { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.project-main p { overflow: hidden; margin: 5px 0; color: var(--sun-muted); text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.project-main time { color: var(--sun-muted-light); font-size: 10px; }
.badge { padding: 5px 8px; border-radius: 4px; color: var(--sun-muted); background: var(--sun-50); font-size: 10px; }
.badge.review { color: var(--sun-700); background: var(--sun-100); }
.badge.active { color: var(--sun-500); background: var(--sun-50); }
.project-actions { display: flex; gap: 6px; }
.project-actions button { border: 0; border-radius: 5px; padding: 7px 9px; color: var(--sun-600); background: var(--sun-50); cursor: pointer; font: inherit; font-size: 11px; }
.project-actions button.quiet { color: var(--sun-muted); background: var(--sun-50); }
.project-actions button:disabled { opacity: .6; cursor: not-allowed; }
.job-message { grid-column: 2 / -1; margin: -4px 0 0; color: var(--sun-600); font-size: 11px; }
.empty { padding: 54px 20px; border: 1px dashed var(--sun-line); border-radius: 9px; background: #fff; text-align: center; }
.empty-mark { margin: 0 auto 12px; color: var(--sun-400); font-size: 25px; }
.empty strong { font-size: 14px; }
.empty p { margin: 7px 0 17px; color: var(--sun-muted); font-size: 12px; }
.empty button { border: 0; color: var(--sun-600); background: transparent; cursor: pointer; font: inherit; font-size: 12px; font-weight: 700; }
.error { color: #b23b2e; font-size: 12px; }
@media (max-width: 900px) { .dashboard { padding: 30px 24px 54px; }.quick-grid { grid-template-columns: 1fr 1fr; }.quick-card.featured { grid-column: 1 / -1; } }.topbar { align-items: flex-start; }@media (max-width: 620px) { .topbar, .section-head { align-items: flex-start; flex-direction: column; }.top-actions { width: 100%; justify-content: space-between; }.stats { gap: 7px; }.stats > div { padding: 13px; }.stats strong { font-size: 22px; }.quick-grid { grid-template-columns: 1fr; }.quick-card.featured { grid-column: auto; }.project-row { grid-template-columns: 32px minmax(0, 1fr) auto; }.project-row > .badge { grid-column: 2; justify-self: start; }.project-actions { grid-column: 2 / -1; }.job-message { grid-column: 2 / -1; } }
.quick-card { position: relative; }
.step-number { position: absolute; top: 22px; right: 22px; color: #9eb1ae; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
</style>
