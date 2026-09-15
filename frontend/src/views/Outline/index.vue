<template>
  <WorkspaceShell>
  <div class="aippt-page">
    <!-- 全局背景：渐变 + 网格 -->
    <div class="page-bg" aria-hidden="true">
      <div class="tech-grid"></div>
      <div class="float-sphere s1"></div>
      <div class="float-sphere s2"></div>
      <div class="float-sphere s3"></div>
    </div>

    <div class="aippt-dialog">
      <div class="header-section">
        <button class="template-btn" @click="goToEditor">
          <span class="btn-inner">制作模板</span>
        </button>
        <button class="template-btn workspace-btn" @click="router.push('/dashboard')">
          <span class="btn-inner">项目工作台</span>
        </button>

        <div class="brand">
          <h1 class="title">
            <span class="title-main">PPTAgent</span>
            <span class="title-badge">AI</span>
          </h1>
          <div class="subtitle">
            {{ step === 'outline' ? '智能大纲已生成 · 选择模板开始创作' : 'AI驱动的演示文稿生成系统' }}
          </div>
        </div>

        <div class="progress-flow">
          <div class="flow-item" :class="{ active: step === 'setup' }">
            <div class="flow-dot"></div>
            <span class="flow-text">INPUT</span>
          </div>
          <div class="flow-connector" :class="{ active: step === 'outline' }"></div>
          <div class="flow-item" :class="{ active: step === 'outline' }">
            <div class="flow-dot"></div>
            <span class="flow-text">PROCESS</span>
          </div>
        </div>
      </div>

      <div v-if="step === 'setup'" class="setup-section">
        <div class="input-module">
          <div class="input-field">
            <textarea
              ref="inputRef"
              v-model="keyword"
              class="text-input"
              placeholder="输入演示主题或粘贴文档内容..."
              rows="6"
            ></textarea>
            <div class="input-actions">
              <div class="action-left">
                <span class="character-count">{{ keyword.length }}/10000</span>
                <div class="lang-select-wrapper">
                  <label for="language-select">语言:</label>
                  <select id="language-select" v-model="language" class="language-select">
                    <option value="中文">中文</option>
                    <option value="English">English</option>
                    <option value="日本語">日本語</option>
                  </select>
                </div>
              </div>
              <div class="buttons-wrapper">
                <button class="generate-btn" @click="createOutline" :disabled="!keyword.trim() || showProcessingModal">
                  <span class="btn-icon">✨</span>
                  {{ showProcessingModal ? '生成中...' : 'AI 生成' }}
                </button>
                <input
                  type="file"
                  ref="fileInputRef"
                  style="display: none"
                  @change="handleFileChange"
                />
                <button class="generate-btn" @click="triggerFileUpload" :disabled="showProcessingModal">
                  <span class="btn-icon">📄</span>
                  依据文件撰写
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="bubble-section">
          <div class="section-title">
            <span class="title-text">快速选择</span>
          </div>
          <div class="bubble-container">
            <div class="bubble-track">
              <div class="bubble-list">
                <button
                  v-for="(item, index) in [...recommends, ...recommends]"
                  :key="`${index}-1`"
                  class="bubble-item"
                  @click="setKeyword(recommends[index % recommends.length])"
                >
                  {{ item }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="config-module" style="display: none;"></div>
      </div>

      <div v-if="step === 'outline'" class="outline-section">
        <div class="outline-header">
          <div class="section-title">
            <span class="title-text">内容大纲</span>
          </div>
          <div class="hint-text">可编辑内容 · 右键管理节点</div>
        </div>

        <div class="outline-container">
          <div v-if="outlineCreating" class="generating-view">
            <div class="gen-indicator">
              <div class="gen-dots">
                <span></span>
                <span></span>
                <span></span>
              </div>
              <span class="gen-text">AI正在生成</span>
            </div>
            <pre ref="outlineRef" class="outline-display">{{ outline }}</pre>
          </div>
          <div v-else class="editor-view">
            <OutlineEditor v-model:value="outline" />
          </div>
        </div>

        <div v-if="!outlineCreating" class="action-group">
          <button class="act-btn secondary" @click="resetToSetup">
            重新生成
          </button>
          <button class="act-btn primary" @click="goPPT">
            创建PPT
          </button>
        </div>
      </div>
    </div>
        <!-- Processing Modal -->
    <div v-if="showProcessingModal" class="processing-modal-overlay">
      <div class="processing-modal">
        <div class="processing-content">
          <div class="processing-spinner"></div>
          <div class="processing-text">正在处理中，请稍候...</div>
        </div>
      </div>
    </div>
  </div>
  </WorkspaceShell>
</template>

<script lang="ts" setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/services'
import useAIPPT from '@/hooks/useAIPPT'
import message from '@/utils/message'
import { subscribeJobEvents } from '@/utils/jobEvents'
import FullscreenSpin from '@/components/FullscreenSpin.vue'
import OutlineEditor from '@/components/OutlineEditor.vue'
import { useMainStore } from '@/store/main'
import WorkspaceShell from '@/components/WorkspaceShell.vue'

const router = useRouter()
const route = useRoute()
const { getMdContent } = useAIPPT()
const mainStore = useMainStore()

const language = ref('中文')
const keyword = ref('')
const outline = ref('')
const loading = ref(false)
const outlineCreating = ref(false)
const step = ref<'setup' | 'outline'>('setup')
const model = ref('GLM-4.5-Air')
const outlineRef = ref<HTMLElement>()
const inputRef = ref<HTMLTextAreaElement>()
const fileInputRef = ref<HTMLInputElement>()
const showProcessingModal = ref(false)
const projectId = ref(typeof route.query.project_id === 'string' ? route.query.project_id : '')
const projectConfig = ref<Record<string, any>>({})

const recommends = ref([
  '2025科技前沿动态',
  '大数据如何改变世界',
  '餐饮市场调查与研究',
  'AIGC在教育领域的应用',
  '社交媒体与品牌营销',
  '5G技术如何改变我们的生活',
  '年度工作总结与展望',
  '区块链技术及其应用',
  '大学生职业生涯规划',
  '公司年会策划方案',
])

onMounted(async () => {
  if (projectId.value) {
    try {
      const project = await api.getProject(projectId.value)
      keyword.value = project.prompt || ''
      outline.value = project.outline || ''
      projectConfig.value = project.config || {}
      language.value = project.config?.language || language.value
      model.value = project.config?.model || model.value
      if (outline.value.trim()) step.value = 'outline'
    } catch (error: any) {
      message.error(error?.message || '项目加载失败')
      router.replace('/dashboard')
      return
    }
  }
  setTimeout(() => {
    if (step.value === 'setup') inputRef.value?.focus()
  }, 500)
})

const projectTitle = (prompt: string) => {
  const firstLine = prompt.split(/\r?\n/).find(line => line.trim())?.trim() || '未命名演示文稿'
  return firstLine.slice(0, 80)
}

const ensureProject = async (prompt: string) => {
  const config = {
    ...projectConfig.value,
    language: language.value,
    model: model.value,
  }
  if (projectId.value) {
    const project = await api.updateProject(projectId.value, {
      title: projectTitle(prompt),
      prompt,
      config,
    })
    projectConfig.value = project.config || config
    return project
  }

  const project = await api.createProject({
    title: projectTitle(prompt),
    prompt,
    language: language.value,
    page_count: 10,
    style: '通用',
    aspect_ratio: '16:9',
    template_id: 'template_1',
  })
  projectId.value = project.id
  projectConfig.value = { ...(project.config || {}), model: model.value }
  await api.updateProject(project.id, { config: projectConfig.value })
  await router.replace({ name: 'Outline', query: { project_id: project.id } })
  return project
}

const setKeyword = (value: string) => {
  keyword.value = value
  inputRef.value?.focus()
}

const resetToSetup = () => {
  outline.value = ''
  step.value = 'setup'
  mainStore.setOutlineFromFile(false)
  setTimeout(() => {
    inputRef.value?.focus()
  }, 100)
}

const goToEditor = () => {
  if (!projectId.value || !outline.value.trim()) {
    message.info('请先生成并确认内容大纲')
    return
  }
  void goPPT()
}

const consumeOutlineStream = async (response: Response) => {
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `服务返回 ${response.status}`)
  }
  if (!response.body) throw new Error('服务没有返回可读取的内容')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let received = false
  outline.value = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const chunk = decoder.decode(value, { stream: true })
    if (!chunk) continue
    if (!received) {
      received = true
      step.value = 'outline'
      showProcessingModal.value = false
    }
    outline.value += chunk
    if (outlineRef.value) outlineRef.value.scrollTop = outlineRef.value.scrollHeight + 20
  }
  if (!received || !outline.value.trim()) throw new Error('AI 服务未返回大纲内容')
  outline.value = getMdContent(outline.value)
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<think>[\s\S]*?<\/think>/g, '')
  if (!outline.value.trim()) throw new Error('生成结果为空，请重新尝试')
}

const consumeOutlineJob = async (jobId: string) => {
  let received = false
  outline.value = ''
  await new Promise<void>((resolve, reject) => {
    subscribeJobEvents(jobId, (event) => {
      if (event.event_type === 'outline.progress') {
        const text = event.payload?.text
        if (typeof text !== 'string') return
        if (!received) {
          received = true
          step.value = 'outline'
          showProcessingModal.value = false
        }
        outline.value += text
        if (outlineRef.value) outlineRef.value.scrollTop = outlineRef.value.scrollHeight + 20
      } else if (event.event_type === 'job.completed') {
        resolve()
      } else if (event.event_type === 'job.failed') {
        reject(new Error(event.payload?.error || '大纲生成失败'))
      }
    }).catch(reject)
  })
  if (!received || !outline.value.trim()) throw new Error('AI 服务未返回大纲内容')
  outline.value = getMdContent(outline.value)
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<think>[\s\S]*?<\/think>/g, '')
  if (!outline.value.trim()) throw new Error('生成结果为空，请重新尝试')
}

const createOutline = async () => {
  if (!keyword.value.trim()) {
    message.error('请先输入PPT主题')
    return
  }
  mainStore.setOutlineFromFile(false)

  loading.value = true
  outlineCreating.value = true
  //进度蒙版
  showProcessingModal.value = true

  try {
    await ensureProject(keyword.value.trim())
    const job = await api.generateOutlineJob(projectId.value)
    await consumeOutlineJob(job.id)
    await api.updateProject(projectId.value, {
      outline: outline.value,
      status: 'OUTLINE_REVIEW',
      config: { ...projectConfig.value, language: language.value, model: model.value },
    })
  } catch (error: any) {
    step.value = 'setup'
    message.error(error?.message || '生成失败，请重试')
  } finally {
    loading.value = false
    outlineCreating.value = false
    showProcessingModal.value = false
  }
}

const goPPT = async () => {
  if (!outline.value.trim()) {
    message.error('请先成功生成并确认内容大纲')
    step.value = 'setup'
    return
  }
  try {
    await ensureProject(keyword.value.trim() || '未命名演示文稿')
    await api.updateProject(projectId.value, {
      outline: outline.value,
      status: 'OUTLINE_REVIEW',
      config: { ...projectConfig.value, language: language.value, model: model.value },
    })
    router.push({ name: 'PPT', query: { project_id: projectId.value } })
  } catch (error: any) {
    message.error(error?.message || '大纲保存失败，请重试')
  }
}

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (input.files && input.files[0]) {
    const file = input.files[0]
    uploadWordAndCreateOutline(file)
  }
}

const uploadWordAndCreateOutline = async (file: File) => {
  mainStore.setOutlineFromFile(true)
  loading.value = true
  outlineCreating.value = true
  showProcessingModal.value = true

  try {
    const filePrompt = keyword.value.trim() || `依据文件 ${file.name} 创建演示文稿`
    keyword.value = filePrompt
    await ensureProject(filePrompt)
    const stream = await api.AIPPT_Outline_From_File(file, mainStore.sessionId, language.value)

    await consumeOutlineStream(stream)
    await api.updateProject(projectId.value, {
      outline: outline.value,
      status: 'OUTLINE_REVIEW',
      config: {
        ...projectConfig.value,
        language: language.value,
        model: model.value,
        outline_from_file: true,
      },
    })
  } catch (error: any) {
    step.value = 'setup'
    message.error(error?.message || '文件处理失败，请重试')
  } finally {
    loading.value = false
    outlineCreating.value = false
    showProcessingModal.value = false
  }
}
</script>

<style lang="scss" scoped>
/* 与大纲页保持同样的页面骨架与背景 */
  /* 页面容器，提供稳定的全屏背景承载 */
.aippt-page {
  position: relative;
  min-height: 100dvh;
  background: linear-gradient(135deg, #fffbf7 0%, #ffffff 100%);
  overflow: hidden;
}

/* 背景层 */
.page-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;

  .tech-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(255, 122, 24, 0.05) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 122, 24, 0.05) 1px, transparent 1px);
    background-size: 40px 40px;
  }

  .float-sphere {
    position: absolute;
    border-radius: 50%;

    &.s1 {
      width: 400px;
      height: 400px;
      background: radial-gradient(circle at 30% 30%, rgba(255, 122, 24, 0.16), transparent 70%);
      top: -100px;
      left: -100px;
      animation: float1 20s ease-in-out infinite;
    }

    &.s2 {
      width: 300px;
      height: 300px;
      background: radial-gradient(circle at 70% 70%, rgba(255, 169, 77, 0.16), transparent 70%);
      bottom: -50px;
      right: -50px;
      animation: float2 15s ease-in-out infinite;
    }

    &.s3 {
      width: 250px;
      height: 250px;
      background: radial-gradient(circle at 50% 50%, rgba(255, 206, 158, 0.18), transparent 70%);
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      animation: float3 25s ease-in-out infinite;
    }
  }
}

@keyframes float1 {
  0%, 100% { transform: translate(0, 0) rotate(0deg); }
  33% { transform: translate(30px, -30px) rotate(120deg); }
  66% { transform: translate(-20px, 20px) rotate(240deg); }
}

@keyframes float2 {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(-30px, -30px); }
}

@keyframes float3 {
  0%, 100% { transform: translate(-50%, -50%) scale(1); }
  50% { transform: translate(-50%, -50%) scale(1.1); }
}

.aippt-dialog {
  position: relative;
  z-index: 1;
  max-width: 1100px;
  margin: 0 auto;
  padding: 40px 20px;
}

.header-section {
  text-align: center;
  margin-bottom: 50px;
  position: relative;

  .template-btn {
    position: absolute;
    top: 0;
    right: 0;
    background: var(--sun-grad);
    color: white;
    border: none;
    padding: 12px 28px;
    border-radius: 100px;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.3s;

    &.workspace-btn {
      right: 132px;
    }
    box-shadow: 0 4px 15px rgba(232, 98, 10, 0.3);

    &:hover {
      transform: translateY(-2px);
      box-shadow: 0 8px 25px rgba(232, 98, 10, 0.4);
    }
  }

  .brand {
    margin-bottom: 35px;

    .title {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      margin: 0 0 12px 0;

      .title-main {
        font-size: 42px;
        font-weight: 700;
        background: var(--sun-grad-soft);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        letter-spacing: -1px;
      }

      .title-badge {
        background: var(--sun-grad);
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
      }
    }

    .subtitle {
      color: var(--sun-muted);
      font-size: 15px;
    }
  }

  .progress-flow {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0;

    .flow-item {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;

      .flow-dot {
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: var(--sun-line);
        transition: all 0.5s;
      }

      .flow-text {
        font-size: 11px;
        color: var(--sun-muted-light);
        letter-spacing: 1.5px;
        font-weight: 500;
      }

      &.active {
        .flow-dot {
          background: var(--sun-grad-soft);
          box-shadow: 0 0 0 4px rgba(255, 122, 24, 0.2);
        }

        .flow-text {
          color: var(--sun-500);
        }
      }
    }

    .flow-connector {
      width: 80px;
      height: 2px;
      background: var(--sun-line);
      margin: 0 -8px;
      margin-bottom: 24px;
      transition: all 0.5s;

      &.active {
        background: linear-gradient(90deg, #ff7a18 0%, #ffa94d 100%);
      }
    }
  }
}

.setup-section {
  .input-module {
    background: white;
    border-radius: 20px;
    padding: 32px;
    box-shadow: 0 10px 40px rgba(120, 58, 10, 0.07);
    margin-bottom: 32px;

    .input-field {
      .text-input {
        width: 100%;
        font-size: 16px;
        padding: 12px 16px;
        border: 2px solid var(--sun-line);
        border-radius: 12px;
        outline: none;
        transition: all 0.3s;
        background: transparent;
        resize: vertical;
        min-height: 140px;

        &::placeholder {
          color: var(--sun-200);
        }

        &:focus {
          border-color: var(--sun-500);
          box-shadow: 0 0 0 3px rgba(255, 122, 24, 0.14);
        }
      }

      .input-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-top: 16px;

        .action-left {
          display: flex;
          align-items: center;
          gap: 24px;
        }

        .character-count {
          font-size: 0.875rem;
          color: var(--sun-muted);
        }

        .lang-select-wrapper {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.875rem;
          color: var(--sun-muted);

          .language-select {
            background-color: var(--sun-50);
            border: 1px solid var(--sun-line);
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 0.875rem;
            color: var(--sun-ink-soft);
            outline: none;
            cursor: pointer;
            transition: border-color 0.2s, box-shadow 0.2s;
            -webkit-appearance: none;
            appearance: none;
            background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e");
            background-position: right 0.5rem center;
            background-repeat: no-repeat;
            background-size: 1.25em;
            padding-right: 2.5rem;

            &:hover {
              border-color: var(--sun-200);
            }

            &:focus {
              border-color: var(--sun-500);
              box-shadow: 0 0 0 3px rgba(255, 122, 24, 0.14);
            }
          }
        }

        .buttons-wrapper {
          display: flex;
          gap: 1rem;
        }

        .generate-btn {
          background: var(--sun-grad);
          color: white;
          border: none;
          padding: 0.75rem 1.5rem;
          border-radius: 0.75rem;
          font-weight: 600;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 0.5rem;
          transition: all 0.3s ease;
          font-size: 0.95rem;

          &:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(232, 98, 10, 0.3);
          }

          &:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .btn-icon {
            font-size: 1.1rem;
          }
        }
      }

      .input-meta {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 20px;

        .counter {
          color: var(--sun-muted-light);
          font-size: 13px;
        }

        .submit-btn {
          background: var(--sun-grad-soft);
          color: white;
          border: none;
          padding: 12px 32px;
          border-radius: 100px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.3s;

          &:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(232, 98, 10, 0.32);
          }

          &:disabled {
            opacity: 0.4;
            cursor: not-allowed;
          }
        }
      }
    }
  }

  .bubble-section {
    margin-bottom: 32px;

    .bubble-container {
      margin-top: 16px;
      overflow: hidden;
      position: relative;

      &::before,
      &::after {
        content: '';
        position: absolute;
        top: 0;
        bottom: 0;
        width: 60px;
        z-index: 2;
        pointer-events: none;
      }

      &::before {
        left: 0;
        background: linear-gradient(90deg, #fffbf7, transparent);
      }

      &::after {
        right: 0;
        background: linear-gradient(90deg, transparent, #fffbf7);
      }

      .bubble-track {
        overflow: hidden;
        padding: 8px 0;

        .bubble-list {
          display: flex;
          gap: 12px;
          animation: scrollBubbles 30s linear infinite;

          .bubble-item {
            flex-shrink: 0;
            background: white;
            border: 2px solid var(--sun-line);
            padding: 10px 20px;
            border-radius: 100px;
            color: var(--sun-ink-soft);
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s;
            white-space: nowrap;

            &:hover {
              background: var(--sun-grad-soft);
              color: white;
              border-color: transparent;
              transform: scale(1.05);
            }
          }
        }
      }
    }
  }

  .config-module {
    background: white;
    border-radius: 20px;
    padding: 28px;
    box-shadow: 0 10px 40px rgba(120, 58, 10, 0.07);

    .config-grid {
      display: grid;
      grid-template-columns: 1fr 2fr;
      gap: 20px;
      margin-top: 20px;

      .config-item {
        .config-label {
          display: block;
          font-size: 13px;
          color: var(--sun-muted);
          margin-bottom: 8px;
          font-weight: 500;
        }

        .config-select {
          width: 100%;
          padding: 10px 16px;
          border: 2px solid var(--sun-line);
          border-radius: 12px;
          background: white;
          outline: none;
          transition: all 0.3s;
          color: var(--sun-ink-soft);
          cursor: pointer;

          &:focus {
            border-color: var(--sun-500);
            box-shadow: 0 0 0 3px rgba(255, 122, 24, 0.14);
          }
        }
      }
    }
  }
}

.section-title {
  margin-bottom: 16px;

  .title-text {
    font-size: 14px;
    font-weight: 600;
    color: var(--sun-ink-soft);
    text-transform: uppercase;
    letter-spacing: 1px;
  }
}

.outline-section {
  background: white;
  border-radius: 20px;
  padding: 32px;
  box-shadow: 0 10px 40px rgba(120, 58, 10, 0.07);

  .outline-header {
    margin-bottom: 24px;

    .hint-text {
      color: var(--sun-muted-light);
      font-size: 13px;
      margin-top: 8px;
    }
  }

  .outline-container {
    background: var(--sun-50);
    border-radius: 16px;
    padding: 24px;
    min-height: 400px;
    margin-bottom: 28px;

    .generating-view {
      .gen-indicator {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        margin-bottom: 24px;

        .gen-dots {
          display: flex;
          gap: 6px;

          span {
            width: 8px;
            height: 8px;
            background: var(--sun-500);
            border-radius: 50%;
            animation: bounce 1.4s ease-in-out infinite;

            &:nth-child(2) { animation-delay: 0.2s; }
            &:nth-child(3) { animation-delay: 0.4s; }
          }
        }

        .gen-text {
          color: var(--sun-500);
          font-size: 14px;
          font-weight: 500;
        }
      }

      .outline-display {
        color: var(--sun-ink-soft);
        line-height: 1.8;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 350px;
        overflow-y: auto;
        font-family: system-ui, -apple-system, sans-serif;

        &::-webkit-scrollbar {
          width: 6px;
        }

        &::-webkit-scrollbar-track {
          background: var(--sun-line);
          border-radius: 3px;
        }

        &::-webkit-scrollbar-thumb {
          background: var(--sun-200);
          border-radius: 3px;

          &:hover {
            background: var(--sun-muted-light);
          }
        }
      }
    }

    .editor-view {
      max-height: 400px;
      overflow-y: auto;

      &::-webkit-scrollbar {
        width: 6px;
      }

      &::-webkit-scrollbar-track {
        background: var(--sun-line);
        border-radius: 3px;
      }

      &::-webkit-scrollbar-thumb {
        background: var(--sun-200);
        border-radius: 3px;

        &:hover {
          background: var(--sun-muted-light);
        }
      }
    }
  }

  .action-group {
    display: flex;
    justify-content: center;
    gap: 16px;

    .act-btn {
      padding: 14px 36px;
      border-radius: 100px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.3s;
      border: none;

      &.primary {
        background: var(--sun-grad-soft);
        color: white;
        box-shadow: 0 4px 15px rgba(232, 98, 10, 0.32);

        &:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 25px rgba(232, 98, 10, 0.42);
        }
      }

      &.secondary {
        background: var(--sun-50);
        color: var(--sun-ink-soft);

        &:hover {
          background: var(--sun-line);
        }
      }
    }
  }
}

@keyframes scrollBubbles {
  0% {
    transform: translateX(0);
  }
  100% {
    transform: translateX(-50%);
  }
}

@keyframes bounce {
  0%, 60%, 100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-10px);
  }
}

@media (max-width: 768px) {
  .aippt-dialog { padding: 24px 16px; }

  .header-section {
    margin-bottom: 30px;
    .template-btn {
      position: static;
      margin-bottom: 24px;
    }

    .brand {
      margin-bottom: 24px;
      .title {
        .title-main { font-size: 32px; }
        .title-badge { font-size: 10px; padding: 3px 8px; }
      }
      .subtitle { font-size: 13px; }
    }
    .flow-connector { width: 50px; }
  }

  .setup-section {
    .input-module { padding: 20px 16px; border-radius: 16px; }
    .text-input { min-height: 120px; font-size: 15px; }
    .input-actions {
      flex-direction: column;
      gap: 12px;
    }
    .buttons-wrapper {
      flex-direction: column;
      gap: 10px;
    }
    .generate-btn {
      width: 100%;
      justify-content: center;
    }
    .config-module .config-grid {
      grid-template-columns: 1fr;
    }
    .bubble-section { margin-bottom: 20px; }
    .bubble-item { padding: 8px 14px; font-size: 13px; }
  }

  .outline-section {
    padding: 20px 16px;
    border-radius: 16px;

    .outline-container { padding: 16px; min-height: 300px; }
    .outline-display { max-height: 280px; font-size: 14px; }
    .editor-view { max-height: 350px; }

    .references-section {
      .references-container {
        padding: 16px;
        max-height: 400px;
      }
      .reference-item {
        flex-direction: column;
        gap: 8px;
      }
      .reference-number { width: auto; }
      .reference-title { font-size: 14px; }
      .reference-abstract { -webkit-line-clamp: 2; line-clamp: 2; }
      .reference-info { flex-wrap: wrap; gap: 8px; }
    }
  }

  .action-group {
    flex-direction: column;
    gap: 12px;

    .act-btn {
      width: 100%;
      padding: 12px 24px;
    }
  }
}

@media (max-width: 480px) {
  .aippt-dialog { padding: 16px 12px; }

  .header-section {
    margin-bottom: 20px;
    .brand .title .title-main { font-size: 28px; }
    .brand .subtitle { font-size: 12px; }
  }

  .setup-section {
    .input-module { padding: 16px 12px; }
    .control-group {
      flex-direction: column;
      align-items: flex-start !important;
      gap: 4px !important;
    }
  }

  .outline-section {
    padding: 16px 12px;
    .outline-container { padding: 12px; }
    .references-section .references-container { padding: 12px; }
  }
}

.processing-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}
.processing-modal {
  background: #fff;
  border-radius: 1rem;
  padding: 2rem;
  box-shadow: 0 20px 40px rgba(0,0,0,.15);
  max-width: 300px;
  width: 90%;
  text-align: center;
}
.processing-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}
.processing-spinner {
  width: 40px;
  height: 40px;
  border: 4px solid var(--sun-100);
  border-top: 4px solid var(--sun-400);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
.processing-text {
  color: var(--sun-ink-soft);
  font-size: 1rem;
  font-weight: 500;
}
@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>
