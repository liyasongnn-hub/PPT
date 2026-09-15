<template>
  <!-- 手机端：精简视图，仅显示缩略图 + 下载/返回 -->
  <div class="mobile-editor" v-if="isMobile">
    <div class="mobile-header">
      <button class="mobile-back-btn" @click="router.push('/')">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15,18 9,12 15,6"></polyline></svg>
        <span>首页</span>
      </button>
      <h2 class="mobile-title">PPT 预览</h2>
      <button class="mobile-download-btn" @click="handleMobileDownload" :disabled="isGenerating">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"></path><polyline points="7,10 12,15 17,10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
        <span>下载</span>
      </button>
    </div>
    <Thumbnails class="mobile-thumbnails" />
    <div v-if="isGenerating" class="mobile-loading">
      <div class="mobile-loading-spinner"></div>
      <span>AI 生成中，请耐心等待…</span>
    </div>
  </div>

  <!-- PC端：完整编辑器 -->
  <template v-else>
    <div class="pptist-editor">
      <EditorHeader class="layout-header" />
      <div class="layout-content">
        <Thumbnails class="layout-content-left" />
        <div class="layout-content-center">
          <CanvasTool class="center-top" />
          <Canvas class="center-body" :style="{ height: `calc(100% - ${remarkHeight + 40}px)` }" />
          <Remark
            class="center-bottom"
            v-model:height="remarkHeight"
            :style="{ height: `${remarkHeight}px` }"
          />
        </div>
        <Toolbar class="layout-content-right" />
      </div>
    </div>

    <SelectPanel v-if="showSelectPanel" />
    <SearchPanel v-if="showSearchPanel" />
    <NotesPanel v-if="showNotesPanel" />
    <MarkupPanel v-if="showMarkupPanel" />
    <SymbolPanel v-if="showSymbolPanel" />

    <Modal
      :visible="!!dialogForExport"
      :width="680"
      @closed="closeExportDialog()"
    >
      <ExportDialog />
    </Modal>
    <div v-if="isGenerating" class="bottom-loading">
      <span>AI 生成中，请耐心等待…</span>
    </div>
  </template>
</template>

<script lang="ts" setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { useMainStore, useSlidesStore } from '@/store'
import api from '@/services'
import useGlobalHotkey from '@/hooks/useGlobalHotkey'
import usePasteEvent from '@/hooks/usePasteEvent'
import useExport from '@/hooks/useExport'
import { isPC } from '@/utils/common'
import message from '@/utils/message'

import EditorHeader from './EditorHeader/index.vue'
import Canvas from './Canvas/index.vue'
import CanvasTool from './CanvasTool/index.vue'
import Thumbnails from './Thumbnails/index.vue'
import Toolbar from './Toolbar/index.vue'
import Remark from './Remark/index.vue'
import ExportDialog from './ExportDialog/index.vue'
import SelectPanel from './SelectPanel.vue'
import SearchPanel from './SearchPanel.vue'
import NotesPanel from './NotesPanel.vue'
import SymbolPanel from './SymbolPanel.vue'
import MarkupPanel from './MarkupPanel.vue'
import Modal from '@/components/Modal.vue'

const router = useRouter()
const route = useRoute()
const mainStore = useMainStore()
const slidesStore = useSlidesStore()
const { slides, theme, title, viewportSize, viewportRatio } = storeToRefs(slidesStore)
const { dialogForExport, showSelectPanel, showSearchPanel, showNotesPanel, showSymbolPanel, showMarkupPanel, isGenerating } = storeToRefs(mainStore)
const projectId = typeof route.query.project_id === 'string' ? route.query.project_id : ''
const hydrated = ref(false)
let saveTimer: ReturnType<typeof setTimeout> | undefined
let saveInFlight = false
let savePending = false
let lastSavedDocument = ''
let saveErrorShown = false

const closeExportDialog = () => mainStore.setDialogForExport('')

const remarkHeight = ref(40)
const isMobile = ref(!isPC())

const { exportPPTX } = useExport()

const serializeDocument = () => JSON.stringify({
  slides: slides.value,
  theme: theme.value,
  title: title.value,
  viewportSize: viewportSize.value,
  viewportRatio: viewportRatio.value,
})

const saveProject = async () => {
  if (!hydrated.value || !projectId) return
  if (saveInFlight) {
    savePending = true
    return
  }

  const serialized = serializeDocument()
  if (serialized === lastSavedDocument) return
  saveInFlight = true
  try {
    await api.updateProject(projectId, {
      title: title.value,
      status: 'EDITING',
      document: JSON.parse(serialized),
    })
    lastSavedDocument = serialized
    saveErrorShown = false
  } catch (error) {
    if (!saveErrorShown) {
      message.warning('项目自动保存失败，稍后将继续重试')
      saveErrorShown = true
    }
    // eslint-disable-next-line no-console
    console.error('项目自动保存失败:', error)
  } finally {
    saveInFlight = false
    if (savePending) {
      savePending = false
      void saveProject()
    }
  }
}

const scheduleSave = () => {
  if (!hydrated.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => void saveProject(), 1000)
}

watch([slides, theme, title, viewportSize, viewportRatio], scheduleSave, { deep: true })

onMounted(async () => {
  if (!projectId) {
    message.info('请从工作台选择项目后进入编辑器')
    router.replace('/dashboard')
    return
  }

  try {
    const project = await api.getProject(projectId)
    const saved = project.document || {}
    if (!Array.isArray(saved.slides) || saved.slides.length === 0) {
      message.info('该项目还没有可编辑的演示文稿，请先选择模板并生成')
      router.replace({ name: 'PPT', query: { project_id: projectId } })
      return
    }
    slidesStore.setSlides(saved.slides)
    slidesStore.updateSlideIndex(0)
    slidesStore.setTitle(saved.title || project.title)
    if (saved.theme && typeof saved.theme === 'object') slidesStore.setTheme(saved.theme)
    if (typeof saved.viewportSize === 'number') slidesStore.setViewportSize(saved.viewportSize)
    if (typeof saved.viewportRatio === 'number') slidesStore.setViewportRatio(saved.viewportRatio)
    await nextTick()
    lastSavedDocument = serializeDocument()
    hydrated.value = true
  } catch (error: any) {
    message.error(error?.message || '项目加载失败')
    router.replace('/dashboard')
  }
})

onBeforeUnmount(() => {
  if (saveTimer) clearTimeout(saveTimer)
  void saveProject()
})

// 手机端下载
const handleMobileDownload = async () => {
  try {
    await exportPPTX(slides.value, true, true)
    message.success('PPTX 导出成功')
  } catch (error) {
    console.error('导出PPTX失败:', error)
    message.error('导出失败，请重试')
  }
}

if (!isMobile.value) {
  useGlobalHotkey()
  usePasteEvent()
}
</script>

<style lang="scss" scoped>
/* ========== 手机端样式 ========== */
.mobile-editor {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f5f7;
}

.mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 10;
}

.mobile-back-btn,
.mobile-download-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  padding: 8px 12px;
  border-radius: 8px;
  transition: all 0.2s;
}

.mobile-back-btn {
  color: #64748b;
  &:hover { background: #f1f5f9; }
}

.mobile-download-btn {
  color: #fff;
  background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
  &:hover:not(:disabled) { box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.mobile-title {
  font-size: 16px;
  font-weight: 600;
  color: #334155;
  margin: 0;
}

.mobile-thumbnails {
  flex: 1;
  overflow-y: auto;

  :deep(.add-slide) { display: none; }
  :deep(.page-number) { display: none; }

  :deep(.thumbnail-list) {
    padding: 12px 0;
  }

  :deep(.thumbnail-item) {
    padding: 8px 16px;
    justify-content: center;

    .label { display: none; }
    .thumbnail {
      width: 100% !important;
      border-radius: 8px;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
    }
  }
}

.mobile-loading {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 24px;
  background: rgba(0, 0, 0, 0.8);
  color: #fff;
  border-radius: 100px;
  font-size: 14px;
  z-index: 100;
  white-space: nowrap;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
}

.mobile-loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: mobile-spin 0.8s linear infinite;
}

@keyframes mobile-spin {
  to { transform: rotate(360deg); }
}

/* ========== PC端样式 ========== */
.pptist-editor {
  height: 100%;
}
.layout-header {
  height: 40px;
}
.layout-content {
  height: calc(100% - 40px);
  display: flex;
}
.layout-content-left {
  width: 160px;
  height: 100%;
  flex-shrink: 0;
}
.layout-content-center {
  width: calc(100% - 160px - 260px);

  .center-top {
    height: 40px;
  }
}
.layout-content-right {
  width: 260px;
  height: 100%;
}

.bottom-loading {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  padding: 10px 20px;
  background-color: rgba(0, 0, 0, 0.7);
  color: white;
  border-radius: 8px;
  z-index: 1000;
}
</style>
