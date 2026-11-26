'use client'

import { useState, type DragEvent } from 'react'
import { ArrowRight, ArrowUpRight, BookOpen, Check, ChevronRight, CircleHelp, FileText, FileUp, FolderOpen, GraduationCap, Search, ShieldCheck, X } from 'lucide-react'

type DocumentItem = { id: string; name: string; document_type: string; updated_at: string; last_run_id: string | null }
type Props = {
  documents: DocumentItem[]; activeId: string | null; name: string; content: string
  pending: number; applied: number; hasRun: boolean; loading: boolean; busy: boolean
  profile: string; error: string; query: string; onQuery: (value: string) => void
  onImport: () => void; onDrop: (file: File) => void; onCreate: () => void; onExample: () => void
  onOpen: (id: string) => void; onReview: () => void; onTemplates: () => void; onExport: () => void
}

export default function WorkspaceOverview(p: Props) {
  const [dragging, setDragging] = useState(false)
  const [help, setHelp] = useState(false)
  const [filter, setFilter] = useState('all')
  const [showAll, setShowAll] = useState(false)
  const [dropError, setDropError] = useState('')
  const matches = p.documents.filter(doc => doc.name.toLocaleLowerCase().includes(p.query.toLocaleLowerCase()) && (filter === 'all' || !!doc.last_run_id))
  const visible = showAll ? matches : matches.slice(0, 4)
  const excerpt = p.content.replace(/^[#>*\-]+\s*/gm, '').trim().slice(0, 260)
  const checked = p.documents.filter(doc => doc.last_run_id).length
  const currentName = p.name.replace(/\.[^.]+$/, '')
  const pendingLabel = !p.hasRun ? '扫描后生成建议' : p.pending ? '逐项确认后应用' : '本次建议已处理'
  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault(); setDragging(false)
    if (p.busy || p.loading) return
    if (event.dataTransfer.files.length !== 1) { setDropError('请一次导入一份文稿'); return }
    const file = event.dataTransfer.files[0]
    if (!file || !/\.(docx|pdf|md|markdown|txt)$/i.test(file.name)) { setDropError('请选择 DOCX、PDF、Markdown 或 TXT 文件'); return }
    setDropError(''); p.onDrop(file)
  }
  return <div className="overview-page" aria-label="工作台总览">
    <div className="overview-heading"><div><span className="eyebrow">论文修订工作区</span><h1>把精力留给研究</h1><p>从格式检查到逐项修订，让每一次提交更从容</p></div><span className="workspace-local"><ShieldCheck/>文稿保存在本机</span></div>
    <div className="overview-top">
      <section className={'import-panel' + (dragging ? ' is-dragging' : '')} aria-label="导入论文" onDragOver={e => { e.preventDefault(); if (!p.busy) setDragging(true) }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false) }} onDrop={drop}>
        <div className="import-copy"><span className="section-index">01 / 开始修订</span><h2>让论文规范，<br/>让研究出色</h2><p>导入文稿，按学校要求检查与修订</p><div className="import-actions"><button className="primary-button" onClick={p.onImport} disabled={p.busy || p.loading}><FileUp/>选择文稿<ArrowRight/></button><button className="import-secondary" onClick={p.onCreate} disabled={p.busy || p.loading}>新建空白文稿</button></div><span className="import-formats">或拖放文件到这里 · DOCX / PDF / MD / TXT · 最大 10 MB</span></div>
        <div className="import-workflow" aria-label="修订步骤"><span className="workflow-caption">每一步，都有依据</span>{[{label:'导入文稿',detail:'保留原始文件'},{label:'检查规范',detail:'关联学校模板'},{label:'确认修订',detail:'逐项对照原文'}].map((step, i) => <div className="workflow-item" key={step.label}><span className="workflow-number">{String(i + 1).padStart(2, '0')}</span><div><strong>{step.label}</strong><span>{step.detail}</span></div>{i === 2 && <Check/>}</div>)}<span className="workflow-end">清楚地修改，放心地提交 <ArrowUpRight/></span></div>
      </section>
      <section className="overview-metrics" aria-label="真实工作区统计"><div className="metric-row"><span>我的文档</span><strong>{p.loading ? '—' : p.documents.length.toLocaleString('zh-CN')}<small>份</small></strong><span className="metric-note">{checked} 份已有格式扫描</span></div><div className="metric-row"><span>当前文稿待处理</span><strong className={p.hasRun && p.pending ? 'metric-attention' : ''}>{p.hasRun ? p.pending : '—'}<small>项</small></strong><span className="metric-note">{pendingLabel}</span></div><div className="metric-row"><span>当前文稿已应用</span><strong>{p.hasRun ? p.applied : '—'}<small>项</small></strong><span className="metric-note">每项修订都可追溯</span></div></section>
    </div>
    {(p.error || dropError) && <div className="overview-error" role="alert">{dropError || p.error}</div>}
    <div className="overview-middle">
      <section className="overview-panel documents-panel" id="overview-documents"><div className="overview-panel-head"><h2>我的文档<span>{p.documents.length}</span></h2><button className="text-action" onClick={() => setShowAll(value => !value)}>{showAll ? '收起列表' : '全部文档'}<ArrowUpRight/></button></div>
        <div className="document-filters"><div className="document-tabs" role="group" aria-label="文档筛选"><button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部文稿</button><button aria-pressed={filter === 'checked'} onClick={() => setFilter('checked')}>已扫描</button></div><label className="overview-search"><Search/><input aria-label="搜索我的文档" placeholder="搜索文稿" value={p.query} onChange={e => p.onQuery(e.target.value)}/>{p.query && <button aria-label="清除文稿搜索" onClick={() => p.onQuery('')}><X/></button>}</label></div>
        <div className="overview-document-table"><table><thead><tr><th>文档名称</th><th>最近修改</th><th>扫描状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{visible.map(doc => <tr key={doc.id} className={doc.id === p.activeId ? 'current-document' : ''}><td><button className="document-open" onClick={() => p.onOpen(doc.id)} disabled={p.busy}><span className="document-file-icon"><FileText/></span><span><strong>{doc.id === p.activeId ? p.name || doc.name : doc.name}</strong><small>{({docx:'Word 文档',pdf:'PDF 文字稿',markdown:'Markdown',txt:'文本文件'} as Record<string,string>)[doc.document_type] || '论文文稿'}</small></span></button></td><td><time dateTime={doc.updated_at}>{new Intl.DateTimeFormat('zh-CN', { month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai' }).format(new Date(doc.updated_at))}</time></td><td><span className={'document-status ' + (doc.last_run_id ? 'scanned' : '')}><i/>{doc.last_run_id ? '已有扫描' : '待扫描'}</span></td><td><button className="document-arrow" aria-label={'打开文稿：' + doc.name} onClick={() => p.onOpen(doc.id)} disabled={p.busy}><ChevronRight/></button></td></tr>)}</tbody></table></div>
        {!visible.length && <div className="document-empty"><FolderOpen/><h3>{p.loading ? '正在读取文档' : p.query || filter !== 'all' ? '没有匹配的文稿' : '你的下一篇论文，从这里开始'}</h3><p>{p.query || filter !== 'all' ? '试试其他名称，或查看全部文稿' : '导入一份文稿，修订进度会显示在这里'}</p>{p.query || filter !== 'all' ? <button className="text-action" onClick={() => { p.onQuery(''); setFilter('all') }}>清除筛选<ArrowRight/></button> : <button className="text-action" onClick={p.onExample} disabled={p.loading || p.busy}>用示例体验完整流程<ArrowRight/></button>}</div>}
        <div className="document-table-foot"><span>{matches.length ? `显示 ${visible.length} / ${matches.length} 份文稿` : '文件与修订记录分别保存'}</span><span><ShieldCheck/>原稿保留</span></div>
      </section>
      <section className="overview-panel recent-panel"><div className="overview-panel-head"><h2>继续修订</h2><span className="section-index">02 / 当前文稿</span></div>{p.activeId ? <><div className="current-document-title"><FileText/><h3 title={p.name}>{currentName}</h3></div><div className="manuscript-excerpt"><span>正文摘录</span><p>{excerpt || '文稿已创建，开始写下你的研究。'}</p></div><div className="revision-progress"><span>{p.hasRun ? `${p.applied} 项已应用 · ${p.pending} 项待处理` : '尚未运行格式扫描'}</span><span>{p.hasRun ? '当前扫描' : '待开始'}</span></div><div className="review-steps"><span><Check/>原始文件已保留</span><span><BookOpen/>{p.profile}</span></div><button className="continue-button" onClick={() => p.activeId && p.onOpen(p.activeId)} disabled={p.busy}>继续编辑与修订<ArrowRight/></button>{p.hasRun && <button className="text-action report-action" onClick={p.onReview} disabled={p.busy}>查看检查报告<ArrowUpRight/></button>}</> : <div className="recent-empty"><BookOpen/><h3>每一次修订，都有来处</h3><p>导入文稿后，在这里继续编辑、查看建议与检查报告。</p><ol><li><span>01</span>保留原稿</li><li><span>02</span>对照建议</li><li><span>03</span>逐项确认</li></ol></div>}</section>
    </div>
    <div className="overview-bottom"><section className="school-standard"><span className="school-icon"><GraduationCap/></span><div><span className="eyebrow">学校规范</span><h2>{p.profile}</h2><p>标题、段落、参考文献与页面版式</p></div><button className="soft-button" onClick={p.onTemplates} disabled={p.loading || p.busy}>选择规范<ArrowUpRight/></button></section><section className="overview-help"><CircleHelp/><div><h2>第一次使用？</h2><p>四个步骤，完成一次论文修订</p></div><button aria-label={help ? '收起使用指南' : '展开使用指南'} aria-expanded={help} onClick={() => setHelp(v => !v)}><ArrowUpRight/></button></section></div>
    {help && <section className="overview-help-content"><div className="overview-panel-head"><h2>从文稿到定稿</h2><button className="text-action" onClick={() => setHelp(false)}>收起<X/></button></div><ol><li><strong>导入论文</strong><p>DOCX、PDF 会转换为可编辑内容，导出前请核对版式。</p></li><li><strong>选择规范</strong><p>从学校模板中选择适合的标题、段落与页面规则。</p></li><li><strong>逐项修订</strong><p>运行格式扫描，对照原文接受或保留建议，随时撤销。</p></li><li><strong>预览导出</strong><p>核对页面效果后，导出 Word、PDF 或 Markdown。</p></li></ol></section>}
    <footer className="overview-footer"><span>研辩台 · 让研究被认真呈现</span><span>原稿保留 · 修订可追溯</span></footer>
  </div>
}
