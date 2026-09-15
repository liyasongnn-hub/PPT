<template>
  <WorkspaceShell>
  <div class="aippt-page">
    <!-- 全局背景：渐变 + 网格 -->
    <div class="page-bg" aria-hidden="true">
      <div class="bg-blob b1"></div>
      <div class="bg-blob b2"></div>
      <div class="grid"></div>
    </div>

    <div class="aippt-dialog">
      <!-- 头部：标题/说明 居中、层级清晰 -->
      <header class="header" role="banner">
        <div class="header-content">
          <h1 class="title">PPTAgent</h1>
          <p class="subtitle">从下方挑选合适的模板，开始生成 PPT</p>
          <div class="header-decoration" aria-hidden="true">
            <div class="decoration-dot"></div>
            <div class="decoration-dot"></div>
            <div class="decoration-dot"></div>
          </div>
        </div>
      </header>

      <section class="select-template" aria-label="模板选择">
        <div v-if="isOutlineFromFile" class="generate-option">
          <Checkbox v-model:value="generateFromUploadedFile">根据上传的文件生成PPT</Checkbox>
          <Checkbox v-model:value="generateFromWebSearch">使用网络搜索生成PPT</Checkbox>
        </div>

        <div class="templates-container">
          <div class="templates">
            <div
              class="template-card"
              :class="{ selected: selectedTemplate === template.id }"
              v-for="template in templates"
              :key="template.id"
              @click="!loading && (selectedTemplate = template.id)"
            >
              <div class="template-image">
                <img :src="template.cover" :alt="template.name" />
                <div class="overlay">
                  <div class="check-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                      <polyline points="20,6 9,17 4,12"></polyline>
                    </svg>
                  </div>
                </div>
              </div>
              <div class="template-info">
                <span class="template-name">{{ template.name || '经典模板' }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="actions">
          <Button class="btn btn-primary" type="primary" :disabled="loading || !selectedTemplate" @click="createPPT()">
            <span>{{ loading ? '正在生成…' : '生成PPT' }}</span>
            <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12,6 12,12 16,14"></polyline>
            </svg>
          </Button>
          <Button class="btn btn-secondary" :disabled="loading" @click="$router.back()">
            <span>返回大纲</span>
            <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15,18 9,12 15,6"></polyline>
            </svg>
          </Button>
        </div>
      </section>
    </div>

    
  </div>
  </WorkspaceShell>
</template>

<script lang="ts" setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import api from '@/services'
import useAIPPT from '@/hooks/useAIPPT'
import useAddSlidesOrElements from '@/hooks/useAddSlidesOrElements'
import useSlideHandler from '@/hooks/useSlideHandler'
import type { AIPPTSlide } from '@/types/AIPPT'
import type { Slide, SlideTheme } from '@/types/slides'
import { useMainStore, useSlidesStore } from '@/store'
import Button from '@/components/Button.vue'
import Checkbox from '@/components/Checkbox.vue'
import { isPC } from '@/utils/common'
import { subscribeJobEvents } from '@/utils/jobEvents'
import WorkspaceShell from '@/components/WorkspaceShell.vue'
import message from '@/utils/message'

const route = useRoute()
const router = useRouter()
const mainStore = useMainStore()
const slideStore = useSlidesStore()
const { templates, slides, theme, title, viewportSize, viewportRatio } = storeToRefs(slideStore)
const { isOutlineFromFile, generateFromUploadedFile, generateFromWebSearch } =
  storeToRefs(mainStore)

const { AIPPTGenerator, presetImgPool } = useAIPPT()
const { addSlidesFromDataToEnd } = useAddSlidesOrElements()
const { isEmptySlide } = useSlideHandler()

const projectId = ref(typeof route.query.project_id === 'string' ? route.query.project_id : '')
const projectConfig = ref<Record<string, any>>({})
const projectTitle = ref('未命名演示文稿')
const outline = ref('')
const language = ref('中文')
const model = ref('GLM-4.5-Air')
const style = ref('通用')
const img = ref('')
const selectedTemplate = ref<string>('')

onMounted(async () => {
  if (!projectId.value) {
    message.info('请先创建项目并生成大纲')
    router.replace('/create')
    return
  }
  try {
    const project = await api.getProject(projectId.value)
    if (!project.outline?.trim()) {
      message.info('该项目还没有内容大纲，请先完成第一步')
      router.replace({ name: 'Outline', query: { project_id: projectId.value } })
      return
    }
    projectConfig.value = project.config || {}
    projectTitle.value = project.title || '未命名演示文稿'
    outline.value = project.outline
    language.value = project.config?.language || language.value
    model.value = project.config?.model || model.value
    style.value = project.config?.style || style.value
    mainStore.setOutlineFromFile(Boolean(project.config?.outline_from_file))

    await slideStore.fetchTemplates()
    const savedTemplate = project.config?.template_id
    selectedTemplate.value = templates.value.some(item => item.id === savedTemplate)
      ? savedTemplate
      : (templates.value?.[0]?.id || '')
  } catch (error: any) {
    message.error(error?.message || '项目加载失败')
    router.replace('/dashboard')
  }
})
const loading = ref(false)

const createPPT = async () => {
  if (!projectId.value || !outline.value.trim() || !selectedTemplate.value) return
  mainStore.setGenerating(true)
  loading.value = true

  slideStore.resetSlides()
  slideStore.setTitle(projectTitle.value)

  try {
    projectConfig.value = {
      ...projectConfig.value,
      language: language.value,
      model: model.value,
      style: style.value,
      template_id: selectedTemplate.value,
    }
    await api.updateProject(projectId.value, {
      status: 'GENERATING',
      config: projectConfig.value,
    })
    // 初始化图片池（mock 兜底）
    const mockImgs = await api.getMockData('imgs')
    presetImgPool(mockImgs)

    const templateData = await api.getFileData(selectedTemplate.value)
    const templateSlides: Slide[] = templateData.slides
    const templateTheme: SlideTheme = templateData.theme
    slideStore.setTheme(templateTheme)

    // 根据模板的宽度和高度动态设置 viewportSize 和 viewportRatio
    if (templateData.width && templateData.height) {
      slideStore.setViewportSize(templateData.width)
      slideStore.setViewportRatio(templateData.height / templateData.width)
    }

    const job = await api.generateContentJob(projectId.value, {
      generate_from_uploaded_file: generateFromUploadedFile.value,
      generate_from_web_search: generateFromWebSearch.value,
    })

    let generatedSlides = 0

    const handleSlideText = (text: string) => {
      // 某些模型可能会包围 ```json``` fence，这里做容错
      const jsonText = text.replace(/```json|```/g, '').trim()
      const slide: AIPPTSlide = JSON.parse(jsonText)

      // 处理后端返回的图片池
      if (slide.images?.length) {
        const backendImages = slide.images.map((img: any) => ({
          id: img.id || Math.random().toString(),
          src: img.src,
          width: img.width || 1920,
          height: img.height || 1080
        }))
        presetImgPool(backendImages)
      }

      // 用模板生成并插入
      const slideGenerator = AIPPTGenerator(templateSlides, [slide])
      for (const generatedSlide of slideGenerator) {
        generatedSlides += 1
        if (isEmptySlide.value) {
          slideStore.setSlides([generatedSlide])
        } else {
          addSlidesFromDataToEnd([generatedSlide])
        }
      }
    }

    await new Promise<void>((resolve, reject) => {
      subscribeJobEvents(job.id, (event) => {
        if (event.event_type === 'slide.progress') {
          const text = event.payload?.text
          if (typeof text === 'string') {
            try {
              handleSlideText(text)
            } catch (e) {
              // 单页 slide JSON 解析失败不影响整体流程，跳过本条事件
              console.warn('解析单页 slide JSON 失败，跳过本条事件：', e)
            }
          }
        } else if (event.event_type === 'job.completed') {
          resolve()
        } else if (event.event_type === 'job.failed') {
          reject(new Error(event.payload?.error || '内容生成失败'))
        }
      }).catch(reject)
    })

    if (generatedSlides === 0) throw new Error('AI 服务未生成任何页面，请返回第一步重新生成大纲')
    await api.updateProject(projectId.value, {
      status: 'EDITING',
      config: projectConfig.value,
      document: JSON.parse(JSON.stringify({
        slides: slides.value,
        theme: theme.value,
        title: title.value,
        viewportSize: viewportSize.value,
        viewportRatio: viewportRatio.value,
      })),
    })
    router.push({
      name: 'Editor',
      query: {
        project_id: projectId.value,
        ...(isPC() ? { isPc: 'true' } : {}),
      },
    })
  } catch (e: any) {
    await api.updateProject(projectId.value, { status: 'OUTLINE_REVIEW' }).catch(() => undefined)
    message.error(e?.message || 'PPT 生成失败，请稍后重试')
    // eslint-disable-next-line no-console
    console.error(e)
  } finally {
    loading.value = false
    mainStore.setGenerating(false)
  }
}
</script>

<style lang="scss" scoped>
/* 页面容器，提供稳定的全屏背景承载 */
.aippt-page {
  position: relative;
  min-height: 100dvh;
  overflow: hidden;
}

/* 背景层 */
.page-bg {
  position: fixed;
  inset: 0;
  z-index: 0;
  background: radial-gradient(1200px 600px at 10% -10%, rgba(255, 122, 24, 0.12), rgba(0, 0, 0, 0) 60%),
    radial-gradient(1000px 600px at 90% 110%, rgba(255, 169, 77, 0.14), rgba(0, 0, 0, 0) 60%),
    linear-gradient(135deg, #fffbf7 0%, #fdf3ea 100%);
  pointer-events: none;
}
.page-bg .grid {
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(120, 58, 10, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120, 58, 10, 0.05) 1px, transparent 1px);
  background-size: 32px 32px, 32px 32px;
  mask-image: radial-gradient(60% 50% at 50% 50%, #000 60%, transparent 100%);
}
.bg-blob {
  position: absolute;
  filter: blur(40px);
  opacity: 0.6;
}
.bg-blob.b1 { width: 520px; height: 520px; left: -160px; top: -160px; background: #ffd9b8; }
.bg-blob.b2 { width: 420px; height: 420px; right: -120px; bottom: -120px; background: #ffe7d4; }

/* 主内容卡片 */
.aippt-dialog {
  position: relative;
  z-index: 1;
  margin: 0 auto;
  padding: 40px 24px 32px;
  max-width: 1160px;
  box-sizing: border-box;
}

/* 头部区块：居中布局 */
.header {
  text-align: center;
  margin-bottom: 28px;
  .title {
    font-weight: 900;
    font-size: 36px;
    margin: 0 0 10px 0;
    background: var(--sun-grad-soft);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    letter-spacing: -0.5px;
    line-height: 1.15;
  }
  .subtitle {
    color: var(--sun-ink-soft);
    font-size: 16px;
    margin: 0 auto;
    font-weight: 500;
    line-height: 1.6;
    max-width: 680px;
  }
  .header-decoration {
    margin: 14px auto 0;
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: center;
    .decoration-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--sun-grad-soft);
      opacity: 0.7; animation: pulse 2s ease-in-out infinite;
      &:nth-child(2) { animation-delay: 0.25s; }
      &:nth-child(3) { animation-delay: 0.5s; }
    }
  }
}

@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 0.6; }
  50% { transform: scale(1.2); opacity: 1; }
}

/* 模板区域 */
.select-template {
  .templates-container {
    background: rgba(255, 255, 255, 0.9);
    backdrop-filter: saturate(120%) blur(2px);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 8px 30px rgba(120, 58, 10, 0.07);
  }

  .templates {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 18px;
  }

  .template-card {
    position: relative;
    border: 2px solid var(--sun-line);
    border-radius: 14px;
    overflow: hidden;
    cursor: pointer;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    background: white;
    box-shadow: 0 1px 3px rgba(120, 58, 10, 0.06);

    &:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 26px -4px rgba(120, 58, 10, 0.11);
      border-color: var(--sun-200);
    }

    &.selected {
      border-color: var(--sun-400);
      box-shadow: 0 0 0 3px rgba(255, 122, 24, 0.16), 0 12px 28px -6px rgba(255, 122, 24, 0.28);
      .overlay { opacity: 1; visibility: visible; }
      .template-info { background: var(--sun-grad); color: #fff; }
    }

    .template-image {
      position: relative; aspect-ratio: 16/9; overflow: hidden;
      img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s ease; }
      .overlay {
        position: absolute; inset: 0; background: rgba(255, 122, 24, 0.2);
        display: flex; align-items: center; justify-content: center;
        opacity: 0; visibility: hidden; transition: all 0.25s ease;
        .check-icon {
          width: 32px; height: 32px; color: #fff; background: var(--sun-400); border-radius: 50%;
          display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 12px rgba(255, 122, 24, 0.36);
          svg { width: 16px; height: 16px; }
        }
      }
    }

    .template-info {
      padding: 12px 14px; background: var(--sun-50); transition: all 0.25s ease;
      .template-name { font-size: 14px; font-weight: 700; color: inherit; }
    }

    &:hover .template-image img { transform: scale(1.045); }
  }

  .generate-option {
    background: rgba(255, 255, 255, 0.9);
    backdrop-filter: saturate(120%) blur(2px);
    border-radius: 16px;
    padding: 20px 24px;
    margin-bottom: 20px;
    box-shadow: 0 8px 30px rgba(120, 58, 10, 0.07);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
    color: var(--sun-ink-soft);
    font-size: 14px;
  }

  .actions {
    display: flex; justify-content: center; gap: 14px; align-items: center; margin-top: 18px;
    .btn {
      min-width: 148px; height: 48px; display: flex; align-items: center; justify-content: center; gap: 8px;
      font-weight: 700; font-size: 14px; border-radius: 12px; transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative; overflow: hidden;
      &:disabled { opacity: 0.6; cursor: not-allowed; filter: grayscale(10%); }
      .btn-icon { width: 18px; height: 18px; transition: transform 0.25s ease; }
      &.btn-primary {
        background: var(--sun-grad); border: none; color: #fff;
        box-shadow: 0 6px 16px rgba(232, 98, 10, 0.34);
        &:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 10px 24px rgba(232, 98, 10, 0.46); .btn-icon { transform: rotate(90deg); } }
        &:active:not(:disabled) { transform: translateY(0); }
      }
      &.btn-secondary {
        background: #fff; border: 2px solid var(--sun-line); color: var(--sun-muted);
        &:hover:not(:disabled) {
          border-color: var(--sun-200); background: var(--sun-50); color: var(--sun-ink-soft); transform: translateY(-1px);
          box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08); .btn-icon { transform: translateX(-2px); }
        }
        &:active:not(:disabled) { transform: translateY(0); }
      }
    }
  }
}

/* 响应式 */
@media (max-width: 768px) {
  .aippt-dialog { padding: 24px 16px; }
  .header { .title { font-size: 28px; } .subtitle { font-size: 14px; } }
  .select-template {
    .templates-container { padding: 16px; }
    .templates { grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .template-card .template-info { padding: 8px 10px; .template-name { font-size: 12px; } }
    .actions { flex-direction: column; gap: 12px; .btn { width: 100%; max-width: 320px; } }
  }
}
@media (max-width: 480px) {
  .aippt-dialog { padding: 16px 12px; }
  .header .title { font-size: 24px; }
  .select-template {
    .templates-container { padding: 12px; }
    .templates { grid-template-columns: 1fr; gap: 10px; }
  }
}
</style>
