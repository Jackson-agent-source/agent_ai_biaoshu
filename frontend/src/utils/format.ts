/**
 * 一些零散的格式化小工具，多个页面都会用到。
 */

/** 把后端返回的 ISO 时间字符串格式化成「2026-09-14 16:20」；空值显示为 - */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value)
  }
  const pad = (num: number): string => String(num).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/** 把过长的文本截断，避免撑破表格 */
export function truncate(text: string, max = 80): string {
  if (!text) {
    return ''
  }
  return text.length > max ? `${text.slice(0, max)}…` : text
}
