'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, GitCompare } from 'lucide-react'
import { TextDiff } from './RevisionDiff'

export type FormatDiffRow = { kind: 'equal' | 'insert' | 'delete' | 'replace'; source_line_no: number | null; preview_line_no: number | null; before: string | null; after: string | null }
export type FormatDiffReview = { run_id: string; source_hash: string; preview_hash: string; applied_count: number; diff_rows: FormatDiffRow[] }
type SuggestedPreview = { source: string; preview: string; diff_rows: FormatDiffRow[]; applied_count: number; proposed_count: number; blocked_change_ids: string[]; notes: string[] }
type Props = { apiBase: string; runId: string | null; review: FormatDiffReview | null; pendingCount: number; dirty: boolean; disabled?: boolean }
type View = 'suggested' | 'applied'
type DisplayItem = { kind: 'row'; index: number } | { kind: 'omission'; start: number; count: number }
const emptyRows: FormatDiffRow[] = []
const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '建议预览连接失败，请重试。当前正文未改变。'

function displayedItems(rows: FormatDiffRow[], full: boolean): DisplayItem[] {
  if (full) return rows.map((_, index) => ({ kind: 'row', index }))
  const included = new Set<number>()
  rows.forEach((row, index) => {
    if (row.kind === 'equal') return
    for (let cursor = Math.max(0, index - 2); cursor <= Math.min(rows.length - 1, index + 2); cursor++) included.add(cursor)
  })
  if (!included.size) return []
  const result: DisplayItem[] = []
  let previous = -1
  for (const index of [...included].sort((a, b) => a - b)) {
    if (index > previous + 1) result.push({ kind: 'omission', start: previous + 1, count: index - previous - 1 })
    result.push({ kind: 'row', index }); previous = index
  }
  if (previous < rows.length - 1) result.push({ kind: 'omission', start: previous + 1, count: rows.length - previous - 1 })
  return result
}

export default function FormatDiffPanel({ apiBase, runId, review, pendingCount, dirty, disabled = false }: Props) {
  const [view, setView] = useState<View>('applied')
  const [showFull, setShowFull] = useState(false)
  const [activeLocation, setActiveLocation] = useState(0)
  const [suggested, setSuggested] = useState<{ key: string; result: SuggestedPreview } | null>(null)
  const [loadError, setLoadError] = useState<{ key: string; message: string } | null>(null)
  const [loadNonce, setLoadNonce] = useState(0)
  const manualViewRef = useRef(false)
  const previousRunRef = useRef<string | null>(null)
  const requestSerialRef = useRef(0)
  const rowRefs = useRef(new Map<number, HTMLDivElement>())
  const sourceHash = review?.source_hash ?? ''
  const previewHash = review?.preview_hash ?? ''
  const appliedCount = review?.applied_count ?? 0
  const hasCurrentReview = !!runId && !!review && review.run_id === runId
  const requestKey = JSON.stringify([runId, sourceHash, previewHash, pendingCount])
  const currentSuggestion = suggested?.key === requestKey ? suggested.result : null
  const currentError = loadError?.key === requestKey ? loadError.message : ''
  const loading = hasCurrentReview && !dirty && !currentSuggestion && !currentError
  const rows = !hasCurrentReview ? emptyRows : view === 'applied' ? review?.diff_rows ?? emptyRows : currentSuggestion?.diff_rows ?? emptyRows
  const locations = useMemo(() => rows.reduce<number[]>((result, row, index) => { if (row.kind !== 'equal' && (index === 0 || rows[index - 1].kind === 'equal')) result.push(index); return result }, []), [rows])
  const items = useMemo(() => displayedItems(rows, showFull), [rows, showFull])
  const selectedLocation = Math.min(activeLocation, Math.max(0, locations.length - 1))
  const selectedStart = locations[selectedLocation] ?? -1
  let selectedEnd = selectedStart
  while (selectedEnd >= 0 && selectedEnd < rows.length && rows[selectedEnd].kind !== 'equal') selectedEnd++

  useEffect(() => {
    if (previousRunRef.current !== runId) { previousRunRef.current = runId; manualViewRef.current = false; setShowFull(false) }
    if (!manualViewRef.current) setView(pendingCount > 0 && appliedCount === 0 ? 'suggested' : 'applied')
  }, [runId, pendingCount, appliedCount])

  useEffect(() => {
    const serial = ++requestSerialRef.current
    if (!runId || !hasCurrentReview || dirty) return
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 25000)
    setLoadError(null)
    void (async () => {
      try {
        const response = await fetch(apiBase + '/format-runs/' + encodeURIComponent(runId) + '/suggested-preview', { signal: controller.signal })
        if (!response.ok) {
          const data = await response.json().catch(() => null) as { detail?: unknown } | null
          throw new Error(typeof data?.detail === 'string' ? data.detail : '建议预览读取失败（' + response.status + '）')
        }
        const result = await response.json() as SuggestedPreview
        if (!Array.isArray(result.diff_rows) || !Array.isArray(result.notes) || !Array.isArray(result.blocked_change_ids)) throw new Error('建议预览格式无效，请重试。')
        if (serial === requestSerialRef.current && !controller.signal.aborted) setSuggested({ key: requestKey, result })
      } catch (failure) {
        if (serial === requestSerialRef.current) setLoadError({ key: requestKey, message: controller.signal.aborted ? '建议预览读取超时，请重试。当前正文未改变。' : errorText(failure) })
      } finally { clearTimeout(timeout) }
    })()
    return () => { requestSerialRef.current++; clearTimeout(timeout); controller.abort() }
  }, [apiBase, runId, sourceHash, previewHash, pendingCount, dirty, requestKey, loadNonce, hasCurrentReview])

  useEffect(() => { setActiveLocation(0) }, [requestKey, view])

  function changeView(next: View) { manualViewRef.current = true; setView(next); setActiveLocation(0) }
  function navigate(direction: number) {
    if (!locations.length) return
    const next = (selectedLocation + direction + locations.length) % locations.length
    setActiveLocation(next)
    rowRefs.current.get(locations[next])?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const ready = hasCurrentReview && !dirty
  const canDisplay = ready && (view === 'applied' || !!currentSuggestion)
  const hasDifferences = canDisplay && locations.length > 0
  const proposedCount = currentSuggestion?.proposed_count ?? 0

  return <section className="document-diff fdp-panel" aria-label="修改对比">
    <div className="diff-title"><span><GitCompare size={13}/>修改对比</span><span>{view === 'suggested' ? currentSuggestion ? '可预览待接受 ' + proposedCount + ' 项' : '待接受 ' + pendingCount + ' 项' : '已应用 ' + appliedCount + ' 项'}</span></div>
    <div className="fdp-toolbar">
      <div className="fdp-view-switch" role="group" aria-label="修改对比范围"><button type="button" className="ghost-button" aria-pressed={view === 'suggested'} aria-label="待接受建议预览" onClick={() => changeView('suggested')} disabled={disabled || !review}>待接受建议预览</button><button type="button" className="ghost-button" aria-pressed={view === 'applied'} aria-label="已应用修改" onClick={() => changeView('applied')} disabled={disabled || !review}>已应用修改</button></div>
      <div className="fdp-view-switch" role="group" aria-label="差异显示方式"><button type="button" className="ghost-button" aria-pressed={!showFull} onClick={() => setShowFull(false)} disabled={disabled || !hasDifferences}>只看差异</button><button type="button" className="ghost-button" aria-pressed={showFull} onClick={() => setShowFull(true)} disabled={disabled || !hasDifferences}>显示全文</button></div>
      <div className="fdp-navigation"><button type="button" className="ghost-button" aria-label="上一处差异" onClick={() => navigate(-1)} disabled={disabled || !hasDifferences}><ChevronUp size={14}/>上一处</button><span role="status" aria-live="polite">{hasDifferences ? (selectedLocation + 1) + '/' + locations.length + ' 处差异' : '0 处差异'}</span><button type="button" className="ghost-button" aria-label="下一处差异" onClick={() => navigate(1)} disabled={disabled || !hasDifferences}>下一处<ChevronDown size={14}/></button></div>
    </div>
    {!review || !runId ? <p className="fdp-empty">运行格式扫描后，可预览待接受建议；接受后可查看已应用修改。</p> : !hasCurrentReview ? <p className="fdp-empty" role="status">正在读取当前扫描的对比结果…</p> : dirty ? <p className="fdp-empty" role="status">正文或模板已变化，请重新运行格式扫描后查看最新差异。</p> : view === 'suggested' && loading ? <p className="fdp-empty" role="status">正在生成待接受建议预览…</p> : view === 'suggested' && currentError ? <div className="fdp-empty"><p role="alert">{currentError}</p><button type="button" className="ghost-button" onClick={() => setLoadNonce(value => value + 1)} disabled={disabled}>重试建议预览</button></div> : <>
      {view === 'applied' && appliedCount === 0 ? <p className="fdp-empty">尚未接受任何修改（已应用 0 项），原稿与当前正文相同。切换到“待接受建议预览”查看拟修改内容。</p> : <>
        <p className="fdp-description">{view === 'suggested' ? proposedCount > 0 ? '建议稿包含已应用修改及 ' + proposedCount + ' 项待接受建议；这些待接受内容尚未写入正文。' : appliedCount > 0 ? '没有新的待接受建议；下方展示当前已应用修改。' : '当前没有可预览的待接受文本修改。' : '下方对比扫描原稿与当前已接受的正文。'}{hasDifferences && !showFull ? ' 已折叠相同内容，仅保留差异及前后 2 行上下文。' : ''}</p>
        {!hasDifferences ? <p className="fdp-empty">{view === 'suggested' && pendingCount > 0 ? '待接受建议没有可预览的文本差异，请查看修改建议中的冲突或失效提示。' : '当前两稿没有文本差异。'}</p> : <div className="aligned-diff" role="table" aria-label={view === 'suggested' ? '待接受建议行差异' : '已应用修改行差异'}><div className="diff-heading" role="row"><span role="columnheader">扫描原稿</span><span role="columnheader">{view === 'suggested' ? '建议稿（尚未应用）' : '当前正文（已应用）'}</span></div><div className="diff-body">{items.map(item => {
          if (item.kind === 'omission') return <div className="fdp-omission" key={'gap-' + item.start} role="row"><span role="cell">… 已折叠 {item.count} 行相同内容</span></div>
          const row = rows[item.index]
          const current = item.index >= selectedStart && item.index < selectedEnd
          return <div className={'diff-row diff-' + row.kind + (current ? ' fdp-current' : '')} key={item.index} role="row" data-diff-kind={row.kind} data-diff-index={item.index} aria-current={current ? 'true' : undefined} ref={node => { if (node) rowRefs.current.set(item.index, node); else rowRefs.current.delete(item.index) }}><div className={'diff-cell diff-source' + (row.before === null ? ' diff-gap' : '')} role="cell"><span className="diff-line-number">{row.source_line_no ?? '·'}</span><code><TextDiff before={row.before} after={row.after} side="before"/></code></div><div className={'diff-cell diff-preview' + (row.after === null ? ' diff-gap' : '')} role="cell"><span className="diff-line-number">{row.preview_line_no ?? '·'}</span><code><TextDiff before={row.before} after={row.after} side="after"/></code></div></div>
        })}</div></div>}
      </>}
      {view === 'suggested' && currentSuggestion && <div className="fdp-notes">{!!currentSuggestion.blocked_change_ids.length && <p>另有 {currentSuggestion.blocked_change_ids.length} 项存在冲突或失效，未加入建议预览。</p>}{currentSuggestion.notes.map((note, index) => <p key={index}>{note}</p>)}</div>}
    </>}
  </section>
}
