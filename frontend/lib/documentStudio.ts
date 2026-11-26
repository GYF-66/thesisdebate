import type { CSSProperties } from 'react'

export const DOCUMENT_FONTS = ['宋体', '黑体', '仿宋', '楷体', '微软雅黑', 'Times New Roman', 'Arial'] as const
export type DocumentFont = typeof DOCUMENT_FONTS[number]
export type DocumentHeadingStyle = { font_family: DocumentFont; font_size_pt: number; line_spacing: number; space_before_pt: number; space_after_pt: number }
export type DocumentStyle = {
  font_family: DocumentFont; font_size_pt: number; line_spacing: number
  paragraph_before_pt: number; paragraph_after_pt: number; first_line_indent_pt: number
  margin_top_mm: number; margin_bottom_mm: number; margin_left_mm: number; margin_right_mm: number
  headings: DocumentHeadingStyle[]
}
export type DocumentLayout = { document_id: string; revision: number; style: DocumentStyle }
export type StyleChange = { field: string; before: string | number; after: string | number; label: string }
export type StudioMessage = { role: 'user' | 'assistant'; content: string }
export type ChatResult = { reply: string; proposed_style: DocumentStyle | null; changes: StyleChange[]; provider: 'local' | 'configured'; warnings: string[] }
export type ExportFormat = 'pdf' | 'md' | 'docx'
export type ExportResult = { blob: Blob; limitations: string[] }

export const DEFAULT_DOCUMENT_STYLE: DocumentStyle = {
  font_family: '宋体', font_size_pt: 12, line_spacing: 1.5,
  paragraph_before_pt: 0, paragraph_after_pt: 6, first_line_indent_pt: 24,
  margin_top_mm: 25.4, margin_bottom_mm: 25.4, margin_left_mm: 25.4, margin_right_mm: 25.4,
  headings: [18, 16, 14, 12, 12, 12].map((size, index) => ({ font_family: '黑体', font_size_pt: size, line_spacing: 1.5, space_before_pt: [18, 12, 12, 6, 6, 6][index], space_after_pt: [12, 6, 6, 6, 6, 6][index] })),
}
export function cloneDocumentStyle(style: DocumentStyle): DocumentStyle { return { ...style, headings: style.headings.map(heading => ({ ...heading })) } }

const fieldLabels: Record<string, string> = {
  font_family: '正文字体', font_size_pt: '正文字号（磅）', line_spacing: '正文行距（倍）',
  paragraph_before_pt: '正文段前（磅）', paragraph_after_pt: '正文段后（磅）', first_line_indent_pt: '首行缩进（磅）',
  margin_top_mm: '上页边距（毫米）', margin_bottom_mm: '下页边距（毫米）', margin_left_mm: '左页边距（毫米）', margin_right_mm: '右页边距（毫米）',
}
const headingLabels: Record<keyof DocumentHeadingStyle, string> = { font_family: '字体', font_size_pt: '字号（磅）', line_spacing: '行距（倍）', space_before_pt: '段前（磅）', space_after_pt: '段后（磅）' }
export function documentStyleChanges(before: DocumentStyle, after: DocumentStyle): StyleChange[] {
  const changes: StyleChange[] = []
  for (const field of Object.keys(fieldLabels) as Array<Exclude<keyof DocumentStyle, 'headings'>>) {
    if (before[field] !== after[field]) changes.push({ field, before: before[field], after: after[field], label: fieldLabels[field] })
  }
  after.headings.forEach((heading, index) => {
    for (const field of Object.keys(headingLabels) as Array<keyof DocumentHeadingStyle>) {
      if (before.headings[index][field] !== heading[field]) changes.push({ field: 'headings.' + index + '.' + field, before: before.headings[index][field], after: heading[field], label: (index + 1) + ' 级标题' + headingLabels[field] })
    }
  })
  return changes
}
export function copyStyleField(target: DocumentStyle, source: DocumentStyle, field: string): DocumentStyle {
  const next = cloneDocumentStyle(target)
  const heading = field.match(/^headings\.([0-5])\.(font_family|font_size_pt|line_spacing|space_before_pt|space_after_pt)$/)
  if (heading) Object.assign(next.headings[Number(heading[1])], { [heading[2]]: source.headings[Number(heading[1])][heading[2] as keyof DocumentHeadingStyle] })
  else if (Object.hasOwn(fieldLabels, field)) Object.assign(next, { [field]: source[field as Exclude<keyof DocumentStyle, 'headings'>] })
  return next
}

export function documentStyleCss(style: DocumentStyle): CSSProperties {
  const value: Record<string, string | number> = {
    fontFamily: '"' + style.font_family + '", serif', fontSize: style.font_size_pt + 'pt', lineHeight: style.line_spacing,
    '--ds-font-family': '"' + style.font_family + '", serif', '--ds-font-size': style.font_size_pt + 'pt', '--ds-line-spacing': style.line_spacing,
    '--ds-paragraph-before': style.paragraph_before_pt + 'pt', '--ds-paragraph-after': style.paragraph_after_pt + 'pt', '--ds-first-indent': style.first_line_indent_pt + 'pt',
    '--ds-margin-top': style.margin_top_mm + 'mm', '--ds-margin-bottom': style.margin_bottom_mm + 'mm', '--ds-margin-left': style.margin_left_mm + 'mm', '--ds-margin-right': style.margin_right_mm + 'mm',
  }
  style.headings.forEach((heading, index) => {
    value['--ds-h' + (index + 1) + '-font'] = '"' + heading.font_family + '", sans-serif'
    value['--ds-h' + (index + 1) + '-size'] = heading.font_size_pt + 'pt'
    value['--ds-h' + (index + 1) + '-line'] = heading.line_spacing
    value['--ds-h' + (index + 1) + '-before'] = heading.space_before_pt + 'pt'
    value['--ds-h' + (index + 1) + '-after'] = heading.space_after_pt + 'pt'
  })
  for (const [key, item] of Object.entries(value)) if (key.startsWith('--ds-')) value[key.replace('--ds-', '--doc-')] = item
  return value as CSSProperties
}

export function validateDocumentStyle(value: unknown): DocumentStyle {
  if (!value || typeof value !== 'object') throw new Error('排版样式格式无效。')
  const style = value as DocumentStyle
  const font = (name: unknown) => DOCUMENT_FONTS.includes(name as DocumentFont)
  const number = (item: unknown, min: number, max: number) => typeof item === 'number' && Number.isFinite(item) && item >= min && item <= max
  if (!font(style.font_family) || !number(style.font_size_pt, 8, 36) || !number(style.line_spacing, 1, 3)
    || ![style.paragraph_before_pt, style.paragraph_after_pt, style.first_line_indent_pt].every(item => number(item, 0, 72))
    || ![style.margin_top_mm, style.margin_bottom_mm, style.margin_left_mm, style.margin_right_mm].every(item => number(item, 10, 60))
    || !Array.isArray(style.headings) || style.headings.length !== 6
    || !style.headings.every(heading => heading && font(heading.font_family) && number(heading.font_size_pt, 8, 48) && number(heading.line_spacing, 1, 3) && number(heading.space_before_pt, 0, 72) && number(heading.space_after_pt, 0, 72))) throw new Error('排版样式超出支持范围，请检查字号、段距、行距和页边距。')
  return cloneDocumentStyle(style)
}

async function responseError(response: Response): Promise<Error> {
  const value = await response.json().catch(() => null) as { detail?: unknown } | null
  return new Error(typeof value?.detail === 'string' ? value.detail : response.status === 409 ? '文档已被其他操作更新，请重新打开排版窗口再应用。' : '排版服务请求失败（' + response.status + '）')
}
async function jsonRequest<T>(apiBase: string, path: string, input?: unknown, signal?: AbortSignal, method = 'POST'): Promise<T> {
  const response = await fetch(apiBase + path, { method: input === undefined ? 'GET' : method, headers: input === undefined ? undefined : { 'Content-Type': 'application/json' }, body: input === undefined ? undefined : JSON.stringify(input), signal })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}
export async function getDocumentLayout(apiBase: string, documentId: string, signal?: AbortSignal): Promise<DocumentLayout> {
  const result = await jsonRequest<DocumentLayout>(apiBase, '/workspace/documents/' + encodeURIComponent(documentId) + '/layout', undefined, signal)
  return { ...result, style: validateDocumentStyle(result.style) }
}
export async function saveDocumentLayout(apiBase: string, documentId: string, revision: number, style: DocumentStyle, signal?: AbortSignal): Promise<DocumentLayout> {
  const result = await jsonRequest<DocumentLayout>(apiBase, '/workspace/documents/' + encodeURIComponent(documentId) + '/layout', { expected_revision: revision, style: validateDocumentStyle(style) }, signal, 'PUT')
  return { ...result, style: validateDocumentStyle(result.style) }
}
export async function chatDocumentStyle(apiBase: string, input: { document_id: string; message: string; style: DocumentStyle; history: StudioMessage[]; content?: string; profile_style?: DocumentStyle }, signal?: AbortSignal): Promise<ChatResult> {
  const result = await jsonRequest<ChatResult>(apiBase, '/agent-chat', input, signal)
  if (!result || typeof result.reply !== 'string' || !Array.isArray(result.warnings) || !['local', 'configured'].includes(result.provider)) throw new Error('排版对话返回格式无效，请重试。')
  return { ...result, proposed_style: result.proposed_style === null ? null : validateDocumentStyle(result.proposed_style) }
}
export async function exportDocument(apiBase: string, name: string, content: string, style: DocumentStyle, format: ExportFormat, signal?: AbortSignal, history?: { document_id: string; export_scope: 'original' | 'expected' | 'current' }): Promise<ExportResult> {
  const response = await fetch(apiBase + '/document-export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, content, style: validateDocumentStyle(style), format, ...(history ? { ...history, track_history: true } : {}) }), signal })
  if (!response.ok) throw await responseError(response)
  const blob = await response.blob()
  if (!blob.size) throw new Error('导出文件为空，请重试。')
  const header = response.headers.get('X-Export-Limitations')
  let limitations: string[] = []
  if (header) {
    try { const values: unknown = JSON.parse(decodeURIComponent(header)); limitations = Array.isArray(values) ? values.filter((value): value is string => typeof value === 'string') : ['导出提示：' + decodeURIComponent(header)] }
    catch { limitations = ['导出提示：' + header] }
  }
  return { blob, limitations }
}
