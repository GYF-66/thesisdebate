'use client'

import { useEffect, useRef, useState } from 'react'
import { Search, ExternalLink, Plus, Check } from 'lucide-react'
import { assistantHash } from '../lib/documentAssistance'
import { errorText, textLevelLabels, workspaceRequest, type LiteratureSearch, type LiteratureSource } from '../lib/workspaceAssistant'

type Props = { apiBase: string; documentId: string; prepare: () => Promise<string>; disabled: boolean }

export default function LiteraturePanel({ apiBase, documentId, prepare, disabled }: Props) {
  const [query, setQuery] = useState('')
  const [from, setFrom] = useState(''), [to, setTo] = useState('')
  const [result, setResult] = useState<LiteratureSearch | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [working, setWorking] = useState(''), [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const controller = useRef<AbortController | null>(null)
  const alive = useRef(true)
  const route = '/literature/documents/' + encodeURIComponent(documentId)
  useEffect(() => {
    alive.current = true
    const request = new AbortController()
    void workspaceRequest<{ searches: LiteratureSearch[]; sources: LiteratureSource[] }>(apiBase, route, 'GET', undefined, request.signal).then(state => {
      const last = state.searches.at(-1)
      if (request.signal.aborted) return
      setSelected(state.sources.map(s => s.id))
      if (last) { setResult(last); setQuery(last.query); setFrom(last.year_from?.toString() ?? ''); setTo(last.year_to?.toString() ?? '') }
    }).catch(error => { if (!request.signal.aborted) setError(errorText(error)) })
    return () => { alive.current = false; request.abort(); controller.current?.abort() }
  }, [apiBase, route])

  async function search(page = 1) {
    if (working || disabled) return
    setWorking('search'); setError(''); setNotice('')
    const request = new AbortController(); controller.current = request
    try {
      // Paging keeps the original filters, even while the next query is being typed.
      const input = page === 1 ? { query: query.trim(), year_from: from ? Number(from) : null, year_to: to ? Number(to) : null } : { query: result!.query, year_from: result!.year_from, year_to: result!.year_to }
      const found = await workspaceRequest<LiteratureSearch>(apiBase, route + '/search', 'POST', { ...input, page }, request.signal)
      if (!request.signal.aborted) setResult(found)
    } catch (error) { if (!request.signal.aborted) setError(errorText(error)) }
    finally { if (alive.current) setWorking('') }
  }

  async function select(source: LiteratureSource) {
    if (!result || working || disabled) return
    setWorking(source.id); setError('')
    try {
      const content = await prepare()
      if (!alive.current) return
      await workspaceRequest(apiBase, route + '/sources', 'POST', { search_id: result.id, source_id: source.id, expected_content_hash: await assistantHash(content) })
      if (alive.current) { setSelected(ids => [...new Set([...ids, source.id])]); setNotice(source.text ? '已加入当前文档资料。审查时可引用已取得的摘要。' : '题录已保存到资料库；补充摘要或全文后可用于论据核对。') }
    } catch (error) { if (alive.current) setError(errorText(error)) }
    finally { if (alive.current) setWorking('') }
  }

  return <section className="literature-panel" aria-label="查找文献">
    <div className="panel-intro"><h2>查找文献</h2><p>先找到相关研究，再选择要带入这篇文档的资料。</p></div>
    <form className="literature-form" onSubmit={event => { event.preventDefault(); void search() }}>
      <label htmlFor="literature-query">关键词、题名或 DOI</label>
      <div className="search-input-row"><input id="literature-query" value={query} onChange={e => setQuery(e.target.value)} placeholder="例如：learning analytics" minLength={2} maxLength={500} required/><button className="primary-button" disabled={!!working || query.trim().length < 2 || disabled} aria-label="搜索文献"><Search size={16}/>{working === 'search' ? '检索中…' : '搜索'}</button></div>
      <details className="search-filters"><summary>限定发表年份</summary><div><label>从<input aria-label="起始年份" type="number" min={1500} max={2100} value={from} onChange={e => setFrom(e.target.value)}/></label><label>至<input aria-label="结束年份" type="number" min={from || 1500} max={2100} value={to} onChange={e => setTo(e.target.value)}/></label></div></details>
      <p className="field-note">检索 Crossref 与 OpenAlex，仅发送检索词和年份。</p>
    </form>
    {error && <p className="review-alert" role="alert">{error}</p>}
    {notice && <p className="inline-notice" role="status">{notice}</p>}
    {result && <>
      <div className="literature-summary"><span>“{result.query}” · 第 {result.page} 页</span><small>{result.results.length} 条结果</small></div>
      {result.warnings.map(warning => <p className="field-note warning" key={warning}>{warning}</p>)}
      {!result.results.length && <div className="panel-empty"><p>{result.status === 'failed' ? '本次检索尚未完成。请稍后重试。' : '这组检索词没有返回结果。可缩短题名，或使用英文关键词。'}</p><button className="soft-button" onClick={() => void search()} disabled={!!working}>重新检索</button></div>}
      <ol className="literature-results">{result.results.map(source => <li key={source.id}>
        <div className="source-meta"><span>{source.year ?? '年份待核对'}</span><span>{textLevelLabels[source.text_level]}</span></div>
        <h3>{source.title}</h3><p className="source-authors">{source.authors.slice(0, 3).join('、') || '作者信息待核对'}{source.authors.length > 3 ? ' 等' : ''}</p>
        {source.text ? <details className="source-abstract"><summary>阅读摘要</summary><p>{source.text}</p></details> : <p className="field-note">尚未取得摘要或全文；此条仅用于核对题录。</p>}
        <div className="source-actions">{source.url && <a href={source.url} target="_blank" rel="noopener noreferrer">查看来源<ExternalLink size={13}/></a>}<button className="soft-button" disabled={!!working || selected.includes(source.id) || disabled} onClick={() => void select(source)}>{selected.includes(source.id) ? <Check size={14}/> : <Plus size={14}/>}{selected.includes(source.id) ? '已加入资料' : working === source.id ? '加入中…' : '加入资料'}</button></div>
        <details className="source-provenance"><summary>检索记录 · {source.providers.join(' / ')}</summary><p>{new Date(source.retrieved_at).toLocaleString('zh-CN')}<br/>检索词：{source.query}<br/>DOI：{source.doi ?? '未提供'}</p><code title={source.text_hash}>文本校验：{source.text_hash.slice(0, 16)}</code></details>
      </li>)}</ol>
      <nav className="literature-pagination" aria-label="文献结果分页"><button className="ghost-button" disabled={!!working || result.page <= 1} onClick={() => void search(result.page - 1)}>上一页</button><span>第 {result.page} 页</span><button className="ghost-button" disabled={!!working || !result.has_more} onClick={() => void search(result.page + 1)}>下一页</button></nav>
    </>}
    {!result && <div className="panel-empty"><p>输入主题即可开始。结果按 DOI 合并重复项，摘要和题录会分别标注。</p></div>}
  </section>
}
