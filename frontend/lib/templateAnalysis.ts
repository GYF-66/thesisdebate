import type { SchoolTemplateDraft } from './schoolTemplates'

const names: Record<string, string> = {
  institution: '学校或学院', font_family: '字体', font_size_pt: '字号', line_spacing: '倍数行距', line_spacing_pt: '固定行距',
  paragraph_before_pt: '段前', paragraph_after_pt: '段后', space_before_pt: '段前', space_after_pt: '段后', first_line_indent_pt: '首行缩进',
  margin_top_mm: '上页边距', margin_bottom_mm: '下页边距', margin_left_mm: '左页边距', margin_right_mm: '右页边距',
  required_sections: '必要章节', cover_fields: '封面字段', submission_checklist: '提交清单',
}
export function templateFieldLabel(field: string): string {
  const heading = field.match(/^document_style\.headings\.([0-5])\.(.+)$/)
  if (heading) return `${Number(heading[1]) + 1} 级标题 · ${names[heading[2]] ?? heading[2]}`
  const numbering = field.match(/^heading_numbering\.([0-5])$/)
  if (numbering) return `${Number(numbering[1]) + 1} 级标题编号`
  const key = field.replace('document_style.', '')
  return (field.startsWith('document_style.') && !key.startsWith('margin_') ? '正文 · ' : '') + (names[key] ?? key)
}
export function analysisSummary(draft: SchoolTemplateDraft) {
  const active = (draft.rule_sources ?? []).filter(item => !item.note.startsWith('未采用'))
  const conflicts = new Set(active.filter(item => item.status === 'conflict').map(item => item.field))
  const filled = new Set(active.filter(item => item.note.startsWith('已自动填入') && !conflicts.has(item.field)).map(item => item.field))
  const state = (field: string) => conflicts.has(field) ? 'conflict' : filled.has(field) ? 'filled' : 'default'
  return { filled, conflicts, state }
}
