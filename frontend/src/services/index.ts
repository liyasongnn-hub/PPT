import axios from './config'

// export const SERVER_URL = 'http://localhost:5000'
export const SERVER_URL = '/api'
export const PLATFORM_TOKEN_KEY = 'ppt_platform_access_token'
export const PLATFORM_REFRESH_TOKEN_KEY = 'ppt_platform_refresh_token'

let refreshRequest: Promise<string | null> | null = null

export function clearPlatformSession() {
  localStorage.removeItem(PLATFORM_TOKEN_KEY)
  localStorage.removeItem(PLATFORM_REFRESH_TOKEN_KEY)
}

export function savePlatformSession(result: { access_token: string; refresh_token?: string }) {
  localStorage.setItem(PLATFORM_TOKEN_KEY, result.access_token)
  if (result.refresh_token) localStorage.setItem(PLATFORM_REFRESH_TOKEN_KEY, result.refresh_token)
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem(PLATFORM_REFRESH_TOKEN_KEY)
  if (!refreshToken) return null
  if (!refreshRequest) {
    refreshRequest = fetch(`${SERVER_URL}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async response => {
        if (!response.ok) return null
        const result = await response.json()
        savePlatformSession(result)
        return result.access_token as string
      })
      .catch(() => null)
      .finally(() => { refreshRequest = null })
  }
  return refreshRequest
}

export async function platformFetch(url: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = localStorage.getItem(PLATFORM_TOKEN_KEY)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response = await fetch(url, { ...init, headers })
  if (response.status === 401 && retry) {
    const refreshedToken = await refreshAccessToken()
    if (refreshedToken) {
      headers.set('Authorization', `Bearer ${refreshedToken}`)
      response = await fetch(url, { ...init, headers })
    }
  }
  if (response.status === 401) clearPlatformSession()
  return response
}

async function platformRequest(path: string, init: RequestInit = {}, authenticated = true) {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const response = authenticated
    ? await platformFetch(`${SERVER_URL}/v1${path}`, { ...init, headers })
    : await fetch(`${SERVER_URL}/v1${path}`, { ...init, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`)
  return data
}

interface AIPPTOutlinePayload {
  content: string
  language: string
  model: string
}

interface AIPPTPayload {
  content: string
  language: string
  style?: string
  model?: string
  generateFromUploadedFile?: boolean
  generateFromWebSearch?: boolean
  sessionId?: string
}

interface AIWritingPayload {
  content: string
  command: string
}

interface AIByIDPayload {
  id: string|number
  language?: string
}


export default {
  getMockData(filename: string): Promise<any> {
    return axios.get(`./mocks/${filename}.json`)
  },

  getFileData(filename: string): Promise<any> {
    return axios.get(`${SERVER_URL}/data/${filename}.json`)
  },

  getTemplates(): Promise<any> {
    return axios.get(`${SERVER_URL}/templates`)
  },

  AIPPT_Outline({
    content,
    language,
    model,
  }: AIPPTOutlinePayload): Promise<any> {
    return platformFetch(`${SERVER_URL}/tools/aippt_outline`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        content,
        language,
        model,
        stream: true,
      }),
    })
  },

  AIPPT_Content({
    content,
    language,
    style,
    model,
    generateFromUploadedFile,
    generateFromWebSearch,
    sessionId,
  }: AIPPTPayload): Promise<any> {
    return platformFetch(`${SERVER_URL}/tools/aippt`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify({
        content,
        language,
        model,
        style,
        stream: true,
        generateFromUploadedFile,
        generateFromWebSearch,
        sessionId,
      }),
    })
  },

  AIPPTByID({
    id,
    language,
  }: AIByIDPayload): Promise<any> {
    return platformFetch(`${SERVER_URL}/tools/aippt_by_id`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        id,
        language,
      }),
    })
  },

  AI_Writing({
    content,
    command,
  }: AIWritingPayload): Promise<any> {
    return platformFetch(`${SERVER_URL}/tools/ai_writing`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        content,
        command,
        stream: true,
      }),
    })
  },

  AIPPT_Outline_From_File(file: File, _userId: string, language: string): Promise<any> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('language', language)
    return platformFetch(`${SERVER_URL}/tools/aippt_outline_from_file`, {
      method: 'POST',
      body: formData,
    })
  },

  platformRegister(payload: { email: string; password: string; display_name?: string }) {
    return platformRequest('/auth/register', { method: 'POST', body: JSON.stringify(payload) }, false)
  },

  platformLogin(payload: { email: string; password: string }) {
    return platformRequest('/auth/login', { method: 'POST', body: JSON.stringify(payload) }, false)
  },

  platformMe() {
    return platformRequest('/me')
  },

  listProjects() {
    return platformRequest('/projects')
  },

  createProject(payload: { title: string; prompt: string; language: string; page_count: number; style: string; aspect_ratio: string; template_id: string }) {
    return platformRequest('/projects', { method: 'POST', body: JSON.stringify(payload) })
  },

  getProject(projectId: string) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}`)
  },

  updateProject(projectId: string, payload: { title?: string; prompt?: string; outline?: string; status?: string; config?: Record<string, any>; document?: Record<string, any> }) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}`, { method: 'PATCH', body: JSON.stringify(payload) })
  },

  generateOutlineJob(projectId: string) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}/outline:generate`, { method: 'POST' })
  },

  generateContentJob(projectId: string, payload: { generate_from_uploaded_file?: boolean; generate_from_web_search?: boolean }) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}/content:generate`, { method: 'POST', body: JSON.stringify(payload) })
  },

  getJob(jobId: string) {
    return platformRequest(`/jobs/${encodeURIComponent(jobId)}`)
  },

  getJobEvents(jobId: string, signal?: AbortSignal) {
    return platformFetch(`${SERVER_URL}/v1/jobs/${encodeURIComponent(jobId)}/events`, { signal })
  },

  cancelJob(jobId: string) {
    return platformRequest(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
  },

  async uploadExport(projectId: string, file: File) {
    const formData = new FormData()
    formData.append('file', file)
    const response = await platformFetch(`${SERVER_URL}/v1/projects/${encodeURIComponent(projectId)}/exports`, {
      method: 'POST',
      body: formData,
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.detail || `上传失败 (${response.status})`)
    return data
  },

  listFiles(projectId: string) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}/files`)
  },

  async downloadFile(fileId: string) {
    const response = await platformFetch(`${SERVER_URL}/v1/files/${encodeURIComponent(fileId)}/download`)
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(detail || `下载失败 (${response.status})`)
    }
    return response.blob()
  },

  qualityCheck(projectId: string) {
    return platformRequest(`/projects/${encodeURIComponent(projectId)}/quality:check`, { method: 'POST' })
  },
}
