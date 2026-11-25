'use client'

type Spacing = { line_spacing: number; line_spacing_pt?: number }
export default function LineSpacingControl({ value, label, disabled, onChange }: {
  value: Spacing; label: string; disabled?: boolean; onChange: (patch: Spacing) => void
}) {
  const fixed = !!value.line_spacing_pt
  return <div className="line-spacing-control">
    <label>{label}方式<select aria-label={label + '方式'} disabled={disabled} value={fixed ? 'fixed' : 'multiple'} onChange={event => onChange({ line_spacing: value.line_spacing, line_spacing_pt: event.target.value === 'fixed' ? 24 : 0 })}><option value="multiple">倍数行距</option><option value="fixed">固定值（磅）</option></select></label>
    <label>{label}{fixed ? '（磅）' : '（倍）'}<input aria-label={label + (fixed ? '（磅）' : '（倍）')} disabled={disabled} type="number" min={fixed ? 1 : 1} max={fixed ? 72 : 3} step={0.1} value={fixed ? value.line_spacing_pt : value.line_spacing} onChange={event => onChange({ line_spacing: fixed ? value.line_spacing : Number(event.target.value), line_spacing_pt: fixed ? Number(event.target.value) : 0 })}/></label>
  </div>
}
