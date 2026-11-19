import type { DocumentStyle } from './documentStudio'

export type HeadingStyle = 'preserve' | 'decimal' | 'chinese' | 'chinese-paren' | 'arabic-paren' | 'chapter' | 'none'
export type DocumentKind = 'thesis' | 'course_report' | 'lab_report' | 'internship_report' | 'proposal' | 'generic'
export type TemplateRuleSource = { id: string; source: string; quote: string; field: string; value: unknown; status: 'explicit' | 'inferred' | 'confirmed' | 'conflict'; note: string }
export const documentKindOptions: { value: DocumentKind; label: string }[] = [{ value: 'thesis', label: '毕业论文' }, { value: 'course_report', label: '课程报告' }, { value: 'lab_report', label: '实验报告' }, { value: 'internship_report', label: '实习报告' }, { value: 'proposal', label: '项目申报书' }, { value: 'generic', label: '通用文档' }]
export const defaultSections: Record<DocumentKind, string[]> = { thesis: ['摘要','绪论','研究方法','结论','参考文献'], course_report: ['项目概述','主要内容','总结'], lab_report: ['实验目的','实验方法','实验结果','分析与讨论'], internship_report: ['实习概况','实习内容','总结'], proposal: ['项目背景','实施方案','预期成果','进度安排'], generic: [] }
export type TemplateIssue = { id: string; severity: string; title: string; explanation: string; field: string; proposed_value: unknown; can_apply: boolean }
export type TemplateAudit = { issues: TemplateIssue[]; unresolved_conflicts: number; pending_sources: number; summary: string }

export type SchoolTemplateDraft = {
  name: string
  institution: string
  heading_numbering: HeadingStyle[]
  source_name?: string | null
  warnings?: string[]
  document_style?: DocumentStyle | null
  document_kind?: DocumentKind
  review_profile_id?: 'academic' | 'general'
  required_sections?: string[]
  cover_fields?: string[]
  submission_checklist?: string[]
  rule_sources?: TemplateRuleSource[]
  parent_template_id?: string | null
}

export type FormatProfile = SchoolTemplateDraft & {
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
  return profiles.map(profile => ({ ...profile, heading_numbering: profile.heading_numbering?.length === 6 ? profile.heading_numbering : defaultHeadingNumbering(), warnings: profile.warnings ?? [], document_kind: profile.document_kind ?? 'thesis', review_profile_id: profile.review_profile_id ?? 'academic', required_sections: profile.required_sections ?? [], cover_fields: profile.cover_fields ?? [], submission_checklist: profile.submission_checklist ?? [], rule_sources: profile.rule_sources ?? [] }))
}

export function importSchoolTemplate(apiBase: string, file: File, kind: DocumentKind = 'thesis', baseProfileId?: string): Promise<SchoolTemplateDraft> {
  if (/\.(pdf|txt|md|markdown)$/i.test(file.name)) return request<SchoolTemplateDraft>(apiBase, '/template-tools/import?filename=' + encodeURIComponent(file.name) + '&document_kind=' + kind + (baseProfileId ? '&base_profile_id=' + encodeURIComponent(baseProfileId) : ''), {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  })
  return request<SchoolTemplateDraft>(apiBase, '/format-profiles/import?filename=' + encodeURIComponent(file.name), {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  })
}

function templateTool<T>(apiBase: string, action: string, value: unknown): Promise<T> {
  return request<T>(apiBase, '/template-tools/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}
export function extractTemplateRules(apiBase: string, text: string, draft: SchoolTemplateDraft): Promise<SchoolTemplateDraft> {
  return templateTool(apiBase, 'extract', { text, source_name: '粘贴的规范', document_kind: draft.document_kind ?? 'thesis', base_template: draft })
}
export function auditTemplate(apiBase: string, draft: SchoolTemplateDraft): Promise<TemplateAudit> { return templateTool(apiBase, 'audit', { draft }) }
export function resolveTemplateRules(apiBase: string, draft: SchoolTemplateDraft, confirm_ids: string[], reject_ids: string[] = []): Promise<SchoolTemplateDraft> { return templateTool(apiBase, 'resolve', { draft, confirm_ids, reject_ids }) }
export function optimizeTemplate(apiBase: string, draft: SchoolTemplateDraft, selected_issue_ids: string[], parent_template_id?: string): Promise<SchoolTemplateDraft> { return templateTool(apiBase, 'optimize', { draft, selected_issue_ids, parent_template_id }) }
export function mergeTemplateRules(apiBase: string, draft: SchoolTemplateDraft, incoming: SchoolTemplateDraft): Promise<SchoolTemplateDraft> { return templateTool(apiBase, 'merge', { draft, incoming }) }

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
