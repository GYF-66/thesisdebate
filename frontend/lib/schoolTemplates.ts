import type { DocumentStyle } from './documentStudio'

export type HeadingStyle = 'preserve' | 'decimal' | 'chinese' | 'chinese-paren' | 'arabic-paren' | 'chapter' | 'none'

export type SchoolTemplateDraft = {
  name: string
  institution: string
  heading_numbering: HeadingStyle[]
  source_name?: string | null
  warnings?: string[]
  document_style?: DocumentStyle | null
}

export type FormatProfile = SchoolTemplateDraft & {
  rules: { id: string; category: 'format' | 'structure' | 'reference'; property: string; description: string; enabled_by_default: boolean }[]
  id: string
  version: string
  description: string
  rules_count: number
  custom: boolean
  updated_at: string
}

export const headingStyleOptions: { value: HeadingStyle; label: string }[] = [
  { value: 'preserve', label: '保留已有编号，缺失时补层级数字' },
  { value: 'decimal', label: '层级数字：1 / 1.1 / 1.1.1' },
  { value: 'chinese', label: '中文序号：一、二、三、' },
  { value: 'chinese-paren', label: '中文括号：（一）（二）' },
  { value: 'arabic-paren', label: '数字括号：（1）（2）' },
  { value: 'chapter', label: '中文章号：第一章、第二章' },
  { value: 'none', label: '不添加标题编号' },
]

export const defaultHeadingNumbering = (): HeadingStyle[] => Array.from({ length: 6 }, () => 'preserve')

async function request<T>(apiBase: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiBase + path, { ...init, signal: init?.signal ?? AbortSignal.timeout(30000) })
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof error?.detail === 'string' ? error.detail : response.status === 422 ? '模板内容不符合规则，请检查文件或标题编号设置。' : '模板请求失败（' + response.status + '）')
  }
  return response.json() as Promise<T>
}

export async function listFormatProfiles(apiBase: string, signal?: AbortSignal): Promise<FormatProfile[]> {
  const profiles = await request<FormatProfile[]>(apiBase, '/format-profiles', { signal })
  if (!Array.isArray(profiles)) throw new Error('模板列表格式无效，请重试。')
  return profiles.map(profile => ({ ...profile, heading_numbering: profile.heading_numbering?.length === 6 ? profile.heading_numbering : defaultHeadingNumbering(), warnings: profile.warnings ?? [] }))
}

export function importSchoolTemplate(apiBase: string, file: File): Promise<SchoolTemplateDraft> {
  return request<SchoolTemplateDraft>(apiBase, '/format-profiles/import?filename=' + encodeURIComponent(file.name), {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  })
}

export function saveSchoolTemplate(apiBase: string, draft: SchoolTemplateDraft): Promise<FormatProfile> {
  return request<FormatProfile>(apiBase, '/format-profiles', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
  })
}

export function headingNumberingExample(style: HeadingStyle, level: number): string {
  const title = ['一级标题', '二级标题', '三级标题', '四级标题', '五级标题', '六级标题'][level - 1] ?? '标题'
  const prefix: Record<HeadingStyle, string> = { preserve: '保留原编号 · ', decimal: Array.from({ length: level }, () => '1').join('.') + ' ', chinese: '一、', 'chinese-paren': '（一）', 'arabic-paren': '（1）', chapter: '第一章 ', none: '' }
  return prefix[style] + title
}
