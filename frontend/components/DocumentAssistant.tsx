'use client'

import { useEffect, useRef, useState } from 'react'
import { Download, FileUp, LocateFixed, RefreshCw, Search, Trash2 } from 'lucide-react'
import { assistantDownload, assistantHash, assistantRequest, assistantUpload, saveAssistantBlob, type AssistantAnalysis, type AssistantAnswer, type AssistantGuard, type AssistantState, type DocumentKind, type MentorTask, type ReferenceCheck } from '../lib/documentAssistance'
import './document-assistant.css'
import { defaultSections, listFormatProfiles } from '../lib/schoolTemplates'

type Props = { apiBase: string; documentId: string | null; content: string; profileId: string; documentEpoch: number; onLocate: (quote: string) => void; documentName?: string }
const taskLabels = { pending: '待处理', in_progress: '处理中', done: '已处理', skipped: '暂不处理' }
const kindLabels = { thesis: '毕业论文', course_report: '课程报告', lab_report: '实验报告', internship_report: '实习报告', proposal: '项目申报书', generic: '通用文档' }
const tabLabels = { checks: '检查', mentor: '导师意见', materials: '资料', delivery: '交付' }
const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '无法连接文档助手服务，请检查连接后重试。'

export default function DocumentAssistant({ apiBase, documentId, content, profileId, documentEpoch, onLocate, documentName }: Props) {
  const [tab, setTab] = useState<keyof typeof tabLabels>('checks')
  const [state, setState] = useState<AssistantState | null>(null)
  const [analysis, setAnalysis] = useState<AssistantAnalysis | null>(null)
  const [analysisSource, setAnalysisSource] = useState<string | null>(null)
  const [kind, setKind] = useState<DocumentKind>('thesis')
  const [sections, setSections] = useState('')
  const [comments, setComments] = useState('')
  const [taskDrafts, setTaskDrafts] = useState<Record<string, { reply: string; quote: string; status: MentorTask['status'] }>>({})
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [hash, setHash] = useState('')
  const [loadNonce, setLoadNonce] = useState(0)
  const uploadRef = useRef<HTMLInputElement>(null)
  const commentsRef = useRef<HTMLInputElement>(null)
  const busyRef = useRef(false)
  const contextRef = useRef({ documentId, documentEpoch, content, profileId })
  contextRef.current = { documentId, documentEpoch, content, profileId }

  useEffect(() => {
    let active = true
    setHash('')
    void assistantHash(content).then(value => { if (active) setHash(value) }).catch(() => {})
    return () => { active = false }
  }, [content])

  useEffect(() => {
    const controller = new AbortController()
    const captured = { documentId, documentEpoch }
    setState(null); setAnalysis(null); setAnalysisSource(null); setComments(''); setTaskDrafts({}); setAnswer(null); setQuestion(''); setError(''); setMessage(''); setWorking(''); busyRef.current = false
    if (documentId) {
      void Promise.all([assistantRequest<AssistantState>(apiBase, '/documents/' + encodeURIComponent(documentId) + '/state', 'GET', undefined, controller.signal), listFormatProfiles(apiBase, controller.signal)]).then(([value, profiles]) => {
        if (controller.signal.aborted || captured.documentId !== contextRef.current.documentId || captured.documentEpoch !== contextRef.current.documentEpoch) return
        const selected = profiles.find(profile => profile.id === profileId)
        setState(value); setKind(selected?.document_kind ?? value.document_kind); setSections((selected?.required_sections ?? value.required_sections)?.join('\n') ?? '')
      }).catch(failure => { if (!controller.signal.aborted) setError(errorText(failure)) })
    }
    return () => controller.abort()
  }, [apiBase, documentId, documentEpoch, profileId, loadNonce])

  async function perform<T,>(label: string, action: (guard: AssistantGuard) => Promise<T>, apply: (result: T) => void) {
    if (!documentId || busyRef.current) return
    const captured = { documentId, documentEpoch, content, profileId }
    busyRef.current = true; setWorking(label); setError(''); setMessage('')
    try {
      const guard = { document_id: documentId, content, expected_content_hash: await assistantHash(content) }
      const result = await action(guard)
      if (captured.documentId !== contextRef.current.documentId || captured.documentEpoch !== contextRef.current.documentEpoch || captured.content !== contextRef.current.content || captured.profileId !== contextRef.current.profileId) return
      apply(result)
    } catch (failure) {
      if (captured.documentId === contextRef.current.documentId && captured.documentEpoch === contextRef.current.documentEpoch && captured.profileId === contextRef.current.profileId) setError(errorText(failure))
    } finally {
      if (captured.documentId === contextRef.current.documentId && captured.documentEpoch === contextRef.current.documentEpoch && captured.profileId === contextRef.current.profileId) { busyRef.current = false; setWorking('') }
    }
  }

  function scan() {
    void perform('正在检查正文…', guard => assistantRequest<AssistantAnalysis>(apiBase, '/analyze', 'POST', { ...guard, profile_id: profileId, document_kind: kind, required_sections: sections.split(/\n|，|,/).map(value => value.trim()).filter(Boolean) }), result => { setAnalysis(result); setAnalysisSource(content); setMessage('检查完成。逐项核对提示，模型内容修改请使用智能体审阅。') })
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>, type: 'materials' | 'comments') {
    const file = event.target.files?.[0]; event.target.value = ''
    if (!file) return
    if (file.size > 10 * 1024 * 1024) { setError('文件不能超过 10 MB。'); return }
    if (type === 'comments' && !/\.docx$/i.test(file.name) || type === 'materials' && !/\.(txt|md|markdown|pdf|docx)$/i.test(file.name)) { setError(type === 'comments' ? '批注导入支持 DOCX。' : '资料支持 TXT、Markdown、PDF 和 DOCX。'); return }
    await perform(type === 'comments' ? '正在读取 Word 批注…' : '正在提取资料文字…', guard => assistantUpload(apiBase, type === 'comments' ? '/mentor-comments/import' : '/materials', file, guard), result => { setState(result); setMessage(type === 'comments' ? '批注已拆成任务，请核对原文关联。' : '资料文字已保存，可按关键词检索原文。'); if (type === 'materials') setAnswer(null) })
  }

  function saveTask(task: MentorTask, rebase = false) {
    const draft = taskDrafts[task.id] ?? { status: task.status, reply: task.reply, quote: task.quote }
    void perform(rebase ? '正在重新关联任务…' : '正在保存任务…', guard => assistantRequest<AssistantState>(apiBase, '/documents/' + encodeURIComponent(guard.document_id) + '/mentor-tasks/' + encodeURIComponent(task.id), 'PATCH', { content: guard.content, expected_content_hash: guard.expected_content_hash, expected_task_version: task.version, ...draft, rebase }), result => { setState(result); setTaskDrafts(value => { const next = { ...value }; delete next[task.id]; return next }); setMessage('导师任务已保存。') })
  }
  function taskDraft(task: MentorTask, update: Partial<{ reply: string; quote: string; status: MentorTask['status'] }>) {
    setTaskDrafts(value => ({ ...value, [task.id]: { ...(value[task.id] ?? { reply: task.reply, quote: task.quote, status: task.status }), ...update } }))
  }
  function download(type: 'report.md' | 'mentor-replies.csv') {
    void perform('正在生成下载文件…', guard => assistantDownload(apiBase, guard.document_id, guard.expected_content_hash, type), blob => { saveAssistantBlob(blob, (documentName?.replace(/\.[^.]+$/, '') ?? '文档') + (type === 'report.md' ? '-检查报告.md' : '-导师回复表.csv')); setMessage('文件已生成并开始下载。') })
  }
  const staleAnalysis = analysisSource !== null && analysisSource !== content
  const readyAnalysis = analysis && !staleAnalysis
  const busy = !!working
  const tasks = state?.mentor_tasks ?? []
  const pending = tasks.filter(task => !['done', 'skipped'].includes(task.status) || task.source_content_hash !== hash).length

  return <section className="da-panel" aria-label="文档助手">
    <header className="da-intro"><strong>文档助手</strong><p>检查、导师返修、资料证据与提交准备，围绕当前文档保存。</p></header>
    <nav className="da-tabs" aria-label="文档助手功能">{(Object.keys(tabLabels) as (keyof typeof tabLabels)[]).map(value => <button key={value} type="button" aria-pressed={tab === value} onClick={() => setTab(value)}>{tabLabels[value]}</button>)}</nav>
    {!documentId ? <p className="da-empty">请先新建或打开一个工作区文档。</p> : <>
      {working && <p className="da-status" role="status">{working}</p>}
      {error && <p className="da-error" role="alert">{error}<button type="button" disabled={busy} onClick={() => setLoadNonce(value => value + 1)}>刷新任务</button></p>}
      {message && <p className="da-message" role="status">{message}</p>}
      {staleAnalysis && <p className="da-note">正文已变化，之前的检查结果需重新运行。</p>}
      {tab === 'checks' && <div className="da-body">
        <label>文档类型<select value={kind} disabled={busy} onChange={event => { const value = event.target.value as DocumentKind; setKind(value); setSections(defaultSections[value].join('\n')); setAnalysis(null) }}>{(Object.keys(kindLabels) as DocumentKind[]).map(value => <option key={value} value={value}>{kindLabels[value]}</option>)}</select></label>
        <details><summary>必需章节（可选）</summary><textarea aria-label="必需章节" value={sections} onChange={event => { setSections(event.target.value); setAnalysis(null) }} placeholder="每行一个；同义标题用 / 分隔。留空则不强制章节。" rows={3}/><p className="da-muted">初始值来自当前模板，可按实际要求调整；通用文档不强加论文结构。检查只按实际标题识别。</p></details>
        <button className="da-primary" disabled={busy || !content.trim()} onClick={scan}><Search size={14}/>检查当前正文</button>
        <p className="da-muted">本地规则检查，无模型调用。涵盖章节、同名样本量、缩写定义、数字引用与文后条目。</p>
        {analysis && <><div className="da-section-heading"><strong>{analysis.findings.length} 条核对提示</strong><button disabled={busy} onClick={scan} aria-label="重新检查"><RefreshCw size={13}/></button></div>{analysis.findings.length === 0 && <p className="da-empty">本次覆盖的规则未发现提示，仍需核对学校要求和专业内容。</p>}{analysis.findings.map(item => <article className="da-item" key={item.id}><div className="da-item-title"><strong>{item.title}</strong><span>{item.severity === 'error' ? '需处理' : item.severity === 'info' ? '提醒' : '需核对'}</span></div><p>{item.detail}</p>{item.quote && <blockquote>{item.quote}</blockquote>}{item.quote && <button disabled={staleAnalysis} onClick={() => onLocate(item.quote)}><LocateFixed size={12}/>定位原文</button>}</article>)}</>}
        {analysis && analysis.references.length > 0 && <details className="da-references"><summary>参考文献元数据核验（{analysis.references.length} 条）</summary><p className="da-muted">点击单条后联网查询 Crossref / OpenAlex。元数据存在不代表原文支持论文论点。</p>{analysis.references.map((reference, index) => { const checked = state?.reference_checks.find(item => item.reference === reference.text); return <article className="da-item" key={reference.number + ':' + index}><p>{reference.text}</p><button disabled={busy || staleAnalysis} onClick={() => { void perform('正在查询文献元数据…', guard => assistantRequest<ReferenceCheck>(apiBase, '/references/verify', 'POST', { ...guard, reference: reference.text }), result => { setState(value => value ? { ...value, reference_checks: [...value.reference_checks.filter(item => item.reference !== result.reference), result] } : value); setMessage(result.message) }) }}>核验这一条</button>{checked && <div className="da-verification"><strong>{checked.status === 'metadata_matched' ? '元数据匹配' : '尚未核验通过'}</strong><p>{checked.message}</p>{checked.candidates.map((item, candidateIndex) => <p key={candidateIndex}>{item.source}：{item.title}{item.doi ? ' · DOI ' + item.doi : ''}</p>)}{checked.warnings.map(item => <p key={item}>{item}</p>)}<p>格式：未单独核验 · 论点支持：未核验</p></div>}</article> })}</details>}
      </div>}
      {tab === 'mentor' && <div className="da-body">
        <p className="da-muted">粘贴意见或导入带批注的 DOCX。按行拆成任务，含“引号原文”的意见可尝试精确关联。</p>
        <textarea aria-label="导师意见" value={comments} onChange={event => setComments(event.target.value)} rows={4} placeholder={'1. 请统一“研究对象”的表述\n2. 补充研究局限'}/>
        <div className="da-actions"><button className="da-primary" disabled={busy || !comments.trim()} onClick={() => { void perform('正在保存导师任务…', guard => assistantRequest<AssistantState>(apiBase, '/mentor-comments', 'POST', { ...guard, comments }), result => { setState(result); setComments(''); setMessage('导师意见已拆成任务。') }) }}>保存为任务</button><button disabled={busy} onClick={() => commentsRef.current?.click()}><FileUp size={13}/>导入 Word 批注</button></div>
        <input hidden type="file" accept=".docx" ref={commentsRef} onChange={event => { void upload(event, 'comments') }}/>
        <div className="da-section-heading"><strong>{tasks.length} 条任务 · {pending} 条待核对</strong><button disabled={busy} onClick={() => download('mentor-replies.csv')}><Download size={13}/>回复表</button></div>
        {tasks.length === 0 && <p className="da-empty">尚未录入导师任务。</p>}
        {tasks.map(task => { const draft = taskDrafts[task.id] ?? task; const stale = task.source_content_hash !== hash; return <article className="da-item" key={task.id}><strong>{task.comment}</strong><p className="da-muted">{task.source}{stale ? ' · 正文已变，请重新关联' : ''}</p><label>关联原文（需唯一匹配）<textarea rows={2} value={draft.quote} onChange={event => taskDraft(task, { quote: event.target.value })} placeholder="粘贴当前正文中的原句；找不到可留空"/></label>{task.quote && <button onClick={() => onLocate(task.quote)} disabled={stale}><LocateFixed size={12}/>定位原文</button>}<label>处理状态<select value={draft.status} onChange={event => taskDraft(task, { status: event.target.value as MentorTask['status'] })}>{(Object.keys(taskLabels) as MentorTask['status'][]).map(value => <option key={value} value={value}>{taskLabels[value]}</option>)}</select></label><label>处理说明<textarea rows={2} value={draft.reply} onChange={event => taskDraft(task, { reply: event.target.value })} placeholder="写明具体修改及所在章节；完成或暂不处理时必填"/></label><button disabled={busy} onClick={() => saveTask(task, stale)}>{stale ? '核对并关联当前正文' : '保存任务'}</button></article> })}
      </div>}
      {tab === 'materials' && <div className="da-body">
        <p className="da-muted">上传 TXT、Markdown、PDF、DOCX，单份不超过 10 MB。原文按当前文档隔离保存；扫描 PDF 暂不支持 OCR。</p>
        <button disabled={busy} onClick={() => uploadRef.current?.click()}><FileUp size={14}/>上传资料</button><input hidden type="file" accept=".txt,.md,.markdown,.pdf,.docx" ref={uploadRef} onChange={event => { void upload(event, 'materials') }}/>
        {(state?.materials ?? []).map(material => <article className="da-item" key={material.id}><div className="da-item-title"><strong>{material.name}</strong><button aria-label={'删除资料 ' + material.name} disabled={busy} onClick={() => { void perform('正在删除资料…', guard => assistantRequest<AssistantState>(apiBase, '/documents/' + encodeURIComponent(guard.document_id) + '/materials/' + encodeURIComponent(material.id) + '?expected_content_hash=' + guard.expected_content_hash, 'DELETE'), result => { setState(result); setAnswer(null); setMessage('资料已删除。') }) }}><Trash2 size={13}/></button></div><p>{material.characters} 字符</p>{material.warnings.map(item => <p className="da-muted" key={item}>{item}</p>)}</article>)}
        <label>查找资料中的证据<textarea aria-label="资料问题" value={question} onChange={event => setQuestion(event.target.value)} rows={3} placeholder="例如：资料中如何定义认知负荷？"/></label><button className="da-primary" disabled={busy || question.trim().length < 2 || !state?.materials.length} onClick={() => { void perform('正在检索原文…', guard => assistantRequest<AssistantAnswer>(apiBase, '/documents/' + encodeURIComponent(guard.document_id) + '/questions', 'POST', { ...guard, question }), setAnswer) }}><Search size={13}/>检索原文</button>
        <p className="da-muted">当前采用确定性关键词检索，未调用模型，不生成没有来源的回答。</p>
        {answer && <div className="da-answer"><p>{answer.answer}</p>{answer.citations.map((citation, index) => <article className="da-item" key={index}><strong>{citation.source_name}</strong><p className="da-muted">{citation.page ? 'PDF 第 ' + citation.page + ' 页 · ' : ''}提取段 {citation.paragraph}</p><blockquote>{citation.quote}</blockquote></article>)}</div>}
      </div>}
      {tab === 'delivery' && <div className="da-body"><div className="da-actions"><button disabled={busy || !content.trim()} onClick={scan}><Search size={13}/>更新交付检查</button><button disabled={busy} onClick={() => download('report.md')}><Download size={13}/>检查报告</button></div><p className="da-muted">下载报告会基于服务端当前已保存正文重新检查。修改后请等待自动保存完成。</p>{readyAnalysis ? <>{analysis.submission_checklist.map(item => <article className="da-item" key={item.id}><div className="da-item-title"><strong>{item.title}</strong><span>{item.status === 'pass' ? '已检查' : item.status === 'manual' ? '人工核对' : '需关注'}</span></div><p>{item.detail}</p></article>)}<article className="da-item"><strong>导师意见闭环</strong><p>{pending} 条任务待处理或需要重新核对正文。</p></article><h4>{kind === 'thesis' ? '依据正文生成的答辩准备问题' : '依据正文生成的汇报准备问题'}</h4>{analysis.defense_questions.length ? analysis.defense_questions.map((item, index) => <article className="da-item" key={index}><p>{item.question}</p><p className="da-muted">{item.reason}</p>{item.quote && <button onClick={() => onLocate(item.quote)}>定位依据</button>}</article>) : <p className="da-muted">当前未识别到可生成准备问题的章节或未解决检查提示。</p>}</> : <p className="da-empty">运行“更新交付检查”后显示当前正文的清单和准备问题。</p>}<p className="da-note">检查不保证论文合格、查重率或 AI 检测结果。文献元数据、引用格式、原文支持论点分别核验。</p></div>}
    </>}
  </section>
}
