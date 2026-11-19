export const COMMON_DOCUMENT_FONTS = ['宋体', '黑体', '仿宋', '楷体', '微软雅黑', 'Times New Roman', 'Arial', '仿宋_GB2312', '楷体_GB2312'] as const
export type DocumentFontEntry = {
  id: string; name: string; aliases: string[]; common: boolean; installed: boolean | null
  status: 'installed' | 'missing' | 'substituted' | 'unknown'; substitute: string | null
  preview_available: boolean; pdf_available: boolean; pdf_reason: string | null
  has_cjk: boolean; styles: string[]
}
export type FontCatalogue = { fonts: DocumentFontEntry[]; total: number; warnings: string[]; common_names: string[] }
export type FontIssue = { requested_font: string; reason: string; suggested_font: string | null }

export function isValidFontName(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 128 && value === value.trim() && !/[\x00-\x1f\x7f;{}<>\\/"\ud800-\udfff\ufffe\uffff]/u.test(value)
}
export function fontCssFamily(name: string): string { return JSON.stringify(name) + ', serif' }
export function unknownFont(name: string): DocumentFontEntry {
  return { id: 'unknown-' + name, name, aliases: [], common: COMMON_DOCUMENT_FONTS.some(font => font === name), installed: null, status: 'unknown', substitute: null, preview_available: false, pdf_available: false, pdf_reason: '字体目录尚未确认，导出前将检查可用性', has_cjk: false, styles: [] }
}
export function fontAvailability(font: DocumentFontEntry): string {
  if (font.installed === null) return '待检测'
  if (font.status === 'substituted') return '缺少精确字体；系统会替代'
  return font.installed ? '本机已安装' : '本机未安装'
}
const catalogueCache = new Map<string, { expires: number; promise: Promise<FontCatalogue> }>()
export function getFontCatalogue(apiBase: string): Promise<FontCatalogue> {
  const previous = catalogueCache.get(apiBase)
  if (previous && previous.expires > Date.now()) return previous.promise
  const promise = fetch(apiBase + '/document-fonts?limit=1000').then(async response => {
    if (!response.ok) throw new Error('字体目录暂不可用（' + response.status + '）')
    const value = await response.json() as FontCatalogue
    if (!value || !Array.isArray(value.fonts) || !Array.isArray(value.warnings)) throw new Error('字体目录格式无效')
    return value
  }).catch(error => { catalogueCache.delete(apiBase); throw error })
  catalogueCache.set(apiBase, { expires: Date.now() + 300_000, promise })
  return promise
}

export class FontConfirmationRequiredError extends Error {
  readonly issues: FontIssue[]
  constructor(message: string, issues: FontIssue[]) { super(message); this.name = 'FontConfirmationRequiredError'; this.issues = issues }
}
