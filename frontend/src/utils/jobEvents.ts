// 订阅后台 Job 的事件流（SSE），把每一段事件解析成对象后回调。
// 用于替代旧的前端直连 /tools/* 流式端点，统一走 jobs 体系。
import { platformFetch, SERVER_URL } from '@/services'

export interface JobEvent {
  id: number
  job_id: string
  event_type: string
  stage: string
  progress: number
  payload: Record<string, any>
  created_at: string
}

function parseSSEBlock(block: string): JobEvent | null {
  const lines = block.split(/\r?\n/)
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  const data = dataLines.join('\n')
  if (!data) return null
  try {
    return JSON.parse(data) as JobEvent
  } catch {
    return null
  }
}

export async function subscribeJobEvents(
  jobId: string,
  onEvent: (event: JobEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await platformFetch(`${SERVER_URL}/v1/jobs/${encodeURIComponent(jobId)}/events`, { signal })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `事件流请求失败 (${response.status})`)
  }
  if (!response.body) throw new Error('事件流没有返回可读取的数据')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE 以空行分隔事件：\n\n（可能是 \r\n\r\n）
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() || ''
      for (const block of parts) {
        const event = parseSSEBlock(block)
        if (event) onEvent(event)
      }
    }
    // 流结束：兜底处理缓冲里最后一条
    const trailing = parseSSEBlock(buffer)
    if (trailing) onEvent(trailing)
  } finally {
    reader.cancel().catch(() => undefined)
  }
}
