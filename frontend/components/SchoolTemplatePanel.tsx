'use client'

import { useEffect, useRef, useState } from 'react'
import { FileUp, Plus, X } from 'lucide-react'
import { defaultHeadingNumbering, headingNumberingExample, headingStyleOptions, importSchoolTemplate, listFormatProfiles, saveSchoolTemplate, type FormatProfile, type HeadingStyle, type SchoolTemplateDraft } from '../lib/schoolTemplates'
import { DEFAULT_DOCUMENT_STYLE, cloneDocumentStyle, type DocumentStyle } from '../lib/documentStudio'

type Props = {
  apiBase: string
  open: boolean
  selectedProfileId: string
  onClose: () => void
  onSelect: (profileId: string, profile: FormatProfile) => void | Promise<void>
  guideActive?: boolean
  disabled?: boolean
}

const emptyDraft = (): SchoolTemplateDraft => ({ name: '', institution: '', heading_numbering: defaultHeadingNumbering(), source_name: null, warnings: [] })
const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '连接失败，模板设置尚未保存，请重试。'

export default function SchoolTemplatePanel({ apiBase, open, selectedProfileId, onClose, onSelect, guideActive = false, disabled = false }: Props) {
  const [profiles, setProfiles] = useState<FormatProfile[]>([])
  const [choice, setChoice] = useState('')
  const [draft, setDraft] = useState<SchoolTemplateDraft>(emptyDraft)
  const [editing, setEditing] = useState(false)
  const [working, setWorking] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [loadNonce, setLoadNonce] = useState(0)
  const fileRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const workingRef = useRef(false)
  const sessionRef = useRef(0)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { if (!workingRef.current) closeRef.current(); return }
      if (event.key !== 'Tab') return
      const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]') ?? []).filter(element => element.offsetParent !== null)
      if (!controls.length) { event.preventDefault(); return }
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [open])

  useEffect(() => {
    const session = ++sessionRef.current
    if (!open) return
    const controller = new AbortController()
    workingRef.current = false
    setWorking(''); setLoading(true); setError(''); setStatus(''); setEditing(false)
    void listFormatProfiles(apiBase, controller.signal).then(items => {
      if (session !== sessionRef.current) return
      setProfiles(items)
      const selected = items.find(profile => profile.id === selectedProfileId) ?? items.find(profile => profile.id === 'generic-thesis') ?? items[0]
      setChoice(selected?.id ?? ''); setDraft(selected ? { ...selected, heading_numbering: [...selected.heading_numbering] } : emptyDraft())
    }).catch(failure => { if (!controller.signal.aborted && session === sessionRef.current) setError(errorText(failure)) }).finally(() => { if (session === sessionRef.current) setLoading(false) })
    return () => controller.abort()
  }, [apiBase, open, selectedProfileId, loadNonce])

  if (!open) return null
  const blocked = disabled || loading || !!working
  const selected = profiles.find(profile => profile.id === choice)

  function choose(id: string) {
    const profile = profiles.find(item => item.id === id)
    if (!profile) return
    setChoice(id); setDraft({ ...profile, heading_numbering: [...profile.heading_numbering] }); setEditing(false); setError(''); setStatus('')
  }

  function updateDraft(update: Partial<SchoolTemplateDraft>) { setDraft(value => ({ ...value, ...update })); setEditing(true); setError(''); setStatus('') }
  function updateStyle(key: keyof DocumentStyle, value: string | number) {
    if (!draft.document_style) return
    updateDraft({ document_style: { ...draft.document_style, [key]: value } })
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''
    if (!file || workingRef.current || disabled) return
    if (!/\.(pdf|docx|json)$/i.test(file.name)) { setError('学校模板支持 PDF、DOCX 样例或 JSON 规则文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setError('模板文件不能超过 10 MB。'); return }
    const session = sessionRef.current
    workingRef.current = true; setWorking('正在读取模板…'); setError(''); setStatus('')
    try {
      const imported = await importSchoolTemplate(apiBase, file)
      if (session !== sessionRef.current) return
      setDraft({ ...imported, heading_numbering: [...imported.heading_numbering] }); setEditing(true)
      setStatus('模板已读取。请核对学校名称和各级编号，确认后保存并选用。')
    } catch (failure) { if (session === sessionRef.current) setError(errorText(failure)) }
    finally { workingRef.current = false; if (session === sessionRef.current) setWorking('') }
  }

  async function apply() {
    if (blocked || workingRef.current) return
    if (editing && !draft.name.trim()) { setError('请填写模板名称。'); return }
    if (!editing && !selected) { setError('请先选择或上传一个模板。'); return }
    const session = sessionRef.current
    workingRef.current = true; setWorking(editing ? '正在保存并选用…' : '正在选用模板…'); setError(''); setStatus('')
    try {
      let profile = selected!
      if (editing) {
        profile = await saveSchoolTemplate(apiBase, { name: draft.name.trim(), institution: draft.institution.trim() || '自定义学校模板', heading_numbering: draft.heading_numbering, source_name: draft.source_name, warnings: draft.warnings, document_style: draft.document_style ?? null })
        if (session !== sessionRef.current) return
        setProfiles(items => [...items, profile]); setChoice(profile.id); setDraft({ ...profile, heading_numbering: [...profile.heading_numbering] }); setEditing(false)
      }
      await onSelect(profile.id, profile)
      if (session === sessionRef.current) setStatus('已选用“' + profile.name + '”。重新运行格式扫描后，按此模板生成修改建议。')
    } catch (failure) { if (session === sessionRef.current) setError(errorText(failure)) }
    finally { workingRef.current = false; if (session === sessionRef.current) setWorking('') }
  }

  return <div className="st-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !workingRef.current) onClose() }}>
    <div className="st-dialog" role="dialog" aria-modal="true" aria-labelledby="school-template-title" tabIndex={-1} ref={dialogRef}>
      <div className="st-header"><div><h2 id="school-template-title">学校格式模板</h2><p>确认标题编号、字体、字号及学校要求的版式。</p></div><button type="button" className="ghost-button" onClick={onClose} disabled={!!working} aria-label="关闭学校模板"><X size={18}/></button></div>
      <div className="st-body">
        <p className="st-status">选用模板不会立即修改正文。请重新扫描，再审核并接受修改建议。</p>
        <label className="st-profile-select">已有模板<select aria-label="选择学校模板" value={choice} disabled={blocked || !profiles.length} onChange={event => choose(event.target.value)}>{!profiles.length && <option value="">{loading ? '正在读取模板…' : '暂无可用模板'}</option>}{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.institution} · {profile.name}{profile.id === selectedProfileId ? '（当前）' : ''}</option>)}</select></label>
        <div className="st-upload"><input hidden ref={fileRef} type="file" accept=".pdf,.docx,.json" onChange={upload}/><button type="button" className="soft-button" onClick={() => fileRef.current?.click()} disabled={blocked}><FileUp size={16}/>上传学校模板</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => { setDraft(emptyDraft()); setEditing(true); setError(''); setStatus('请填写名称，并设置学校要求的各级标题编号。') }}><Plus size={16}/>新建规则</button><small>支持 PDF、DOCX 样例、JSON 规则，最大 10 MB。</small></div>
        <p className="st-status">Word 样例读取样式定义；PDF 样例读取文字、字体和字号，规范文件优先读取明确写出的排版要求。未识别的项目请按学校规范确认；版式将在“预期效果与导出”中先预览。</p>
        <div className="st-fields"><label>模板名称<input aria-label="模板名称" value={draft.name} onChange={event => updateDraft({ name: event.target.value })} maxLength={120} disabled={blocked} placeholder="例如：某大学本科论文"/></label><label>学校或学院<input aria-label="学校或学院" value={draft.institution} onChange={event => updateDraft({ institution: event.target.value })} maxLength={120} disabled={blocked} placeholder="填写学校或学院名称"/></label></div>
        {draft.source_name && <p className="st-status">来源文件：{draft.source_name}</p>}
        {!!draft.warnings?.length && <div className="st-warnings" role="status"><strong>请核对识别结果</strong><ul>{draft.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
        <div className="st-level-grid">{draft.heading_numbering.map((style, index) => <label key={index}><span>{index + 1} 级标题</span><select aria-label={(index + 1) + '级标题编号'} disabled={blocked} value={style} onChange={event => { const levels = [...draft.heading_numbering]; levels[index] = event.target.value as HeadingStyle; updateDraft({ heading_numbering: levels }) }}>{headingStyleOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select><small className="st-rule-preview">{headingNumberingExample(style, index + 1)}</small></label>)}</div>
        <p className="st-status">预览展示编号形式；实际编号按正文的标题层级计数。编辑已有规则时，会保存为新的学校模板。</p>
        <label className="st-layout-toggle"><input type="checkbox" checked={!!draft.document_style} disabled={blocked} onChange={event => updateDraft({ document_style: event.target.checked ? cloneDocumentStyle(DEFAULT_DOCUMENT_STYLE) : null })}/>包含学校排版参数</label>
        {draft.document_style && <fieldset className="st-layout-settings" disabled={blocked}><legend>学校版式（可核对、修改）</legend><div className="st-layout-grid">
          <label>正文字体<select aria-label="学校正文字体" value={draft.document_style.font_family} onChange={event => updateStyle('font_family', event.target.value)}>{['宋体','黑体','仿宋','楷体','微软雅黑','Times New Roman','Arial'].map(font => <option key={font}>{font}</option>)}</select></label>
          {([['font_size_pt','正文字号（磅）',8,36],['line_spacing','正文行距（倍）',1,3],['paragraph_before_pt','段前（磅）',0,72],['paragraph_after_pt','段后（磅）',0,72],['first_line_indent_pt','首行缩进（磅）',0,72],['margin_top_mm','上页边距（毫米）',10,60],['margin_bottom_mm','下页边距（毫米）',10,60],['margin_left_mm','左页边距（毫米）',10,60],['margin_right_mm','右页边距（毫米）',10,60]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input aria-label={'学校'+label} type="number" min={min} max={max} step="0.1" value={draft.document_style![key]} onChange={event => updateStyle(key, Number(event.target.value))}/></label>)}
        </div><div className="st-heading-appearance">{draft.document_style.headings.map((heading,index) => <div key={index}><strong>{index+1}级标题版式</strong><label>字体<select aria-label={(index+1)+'级标题字体'} value={heading.font_family} onChange={event => { const style=cloneDocumentStyle(draft.document_style!); style.headings[index].font_family=event.target.value as DocumentStyle['font_family']; updateDraft({document_style:style}) }}>{['宋体','黑体','仿宋','楷体','微软雅黑','Times New Roman','Arial'].map(font => <option key={font}>{font}</option>)}</select></label>{([['font_size_pt','字号',8,48],['line_spacing','行距',1,3],['space_before_pt','段前',0,72],['space_after_pt','段后',0,72]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input aria-label={(index+1)+'级标题'+label} type="number" min={min} max={max} step="0.1" value={heading[key]} onChange={event => { const style=cloneDocumentStyle(draft.document_style!); style.headings[index][key]=Number(event.target.value); updateDraft({document_style:style}) }}/></label>)}</div>)}</div></fieldset>}
        {error && <p className="st-error" role="alert">{error}</p>}
        {status && <p className="st-status" role="status">{status}</p>}
        {loading && <p className="st-status" role="status">正在读取模板列表…</p>}
        {error && !profiles.length && <button type="button" className="ghost-button" disabled={loading} onClick={() => setLoadNonce(value => value + 1)}>重新读取模板</button>}
      </div>
      <div className="st-footer"><button type="button" className="ghost-button" onClick={onClose} disabled={!!working}>关闭</button><button type="button" className="primary-button" data-guide-target={guideActive && !blocked ? 'true' : undefined} onClick={apply} disabled={blocked || (!editing && !selected)}>{working || (editing ? '确认保存并选用' : '应用所选模板')}</button></div>
    </div>
  </div>
}
