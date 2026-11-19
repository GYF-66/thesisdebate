'use client'

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { COMMON_DOCUMENT_FONTS, fontAvailability, fontCssFamily, getFontCatalogue, unknownFont, type DocumentFontEntry } from '../lib/fonts'
import './font-picker.css'

export type FontPickerProps = { apiBase: string; value: string; onChange: (name: string) => void; disabled?: boolean; ariaLabel?: string }

export default function FontPicker({ apiBase, value, onChange, disabled = false, ariaLabel = '字体' }: FontPickerProps) {
  const [fonts, setFonts] = useState<DocumentFontEntry[]>(() => COMMON_DOCUMENT_FONTS.map(unknownFont))
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const loading = !loaded && !error
  useEffect(() => {
    let current = true
    setLoaded(false)
    getFontCatalogue(apiBase).then(result => { if (current) { setFonts(result.fonts); setError(result.warnings.join('；')); setLoaded(true) } }).catch(failure => { if (current) setError(failure instanceof Error ? failure.message : '字体目录暂不可用') })
    return () => { current = false }
  }, [apiBase])
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])
  useEffect(() => {
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [])
  const selected = fonts.find(font => font.name === value || font.aliases.some(alias => alias.toLocaleLowerCase() === value.toLocaleLowerCase())) ?? (loaded ? { ...unknownFont(value), installed: false, status: 'missing' as const, pdf_reason: '本机未安装精确字体文件' } : unknownFont(value))
  const results = useMemo(() => {
    const term = query.trim().normalize('NFKC').toLocaleLowerCase()
    const items = term ? fonts.filter(font => [font.name, ...font.aliases].some(name => name.normalize('NFKC').toLocaleLowerCase().includes(term))) : fonts.filter(font => font.common)
    return items.slice(0, 80)
  }, [fonts, query])
  useEffect(() => { if (open) document.getElementById(listId + '-' + active)?.scrollIntoView({ block: 'nearest' }) }, [active, listId, open])
  const choose = (font: DocumentFontEntry) => { onChange(font.name); setOpen(false); setQuery(''); input.current?.focus() }
  return <div className="font-picker" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false) }}>
    <div className="font-picker-control"><input ref={input} role="combobox" aria-label={ariaLabel} aria-expanded={open} aria-controls={listId} aria-autocomplete="list" aria-activedescendant={open && results[active] ? listId + '-' + active : undefined} value={open ? query : value} placeholder={open ? '搜索中文名、英文名或别名' : value} disabled={disabled} onFocus={() => { setOpen(true); setQuery(''); setActive(0) }} onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true) }} onKeyDown={event => {
      if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); setQuery('') }
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); setActive(previous => results.length ? (previous + (event.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length : 0) }
      else if (event.key === 'Enter' && open && results[active]) { event.preventDefault(); choose(results[active]) }
    }}/><span aria-hidden="true">⌄</span></div>
    <div className={'font-picker-status' + (selected.installed === false ? ' is-warning' : '')}>{fontAvailability(selected)}<span> · {selected.installed === null ? '导出待检测' : selected.pdf_available ? 'PDF可用' : 'PDF需处理'}</span></div>
    {open && <div className="font-picker-popover"><div className="font-picker-help">{query ? '搜索结果' : '常用 9 种 · 输入名称搜索全部字体'}<small role="status">{loading ? '正在读取本机字体…' : fonts.filter(font => font.installed).length + ' 个已安装字体族'}</small></div><div id={listId} className="font-picker-options" role="listbox" aria-label={ariaLabel + '搜索结果'}>
      {results.map((font, index) => <div key={font.id} id={listId + '-' + index} role="option" aria-selected={font.name === value} className={'font-picker-option' + (index === active ? ' is-active' : '')} onPointerDown={event => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => choose(font)}>
        <div className="font-picker-option-title"><strong>{font.name}</strong><span className={font.installed === false ? 'is-warning' : ''}>{fontAvailability(font)}</span></div>
        <div className="font-picker-sample" style={{ fontFamily: fontCssFamily(font.name) }}>论文格式 Aa 123</div>
        <small>{font.pdf_available ? 'PDF可导出' : font.pdf_reason ?? '导出待检测'}{font.substitute && '；系统替代为' + font.substitute}</small>
      </div>)}
      {!results.length && <div className="font-picker-empty">{loading ? '正在读取字体目录，请稍候…' : '未找到已知字体。模板中的原字体名称会保留，请先核对或安装所需字体。'}</div>}
    </div>{error && <div className="font-picker-error" role="status">{error}</div>}<div className="font-picker-footnote">缺失字体的样例可能由系统替代；PDF替代需另行确认。</div></div>}
  </div>
}
