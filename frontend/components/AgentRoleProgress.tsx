'use client'

import { useEffect, useState } from 'react'
import { progressPhaseLabel, progressUnitLabel, type AgentProgress, type ProgressEvent } from '../lib/agentProgress'

type Props = { name: string; status: string; progress?: AgentProgress | null; startedAt?: string | null; completedAt?: string | null; events: ProgressEvent[] }
const eventTitles: Record<string, string> = { role_started: '开始检查', role_checkpoint: '检查进度已保存', role_completed: '本阶段完成', role_failed: '本阶段未完整完成', challenge_started: '开始质疑复核' }
function timestamp(value?: string | null) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—' }

export default function AgentRoleProgress({ name, status, progress, startedAt, completedAt, events }: Props) {
  const [now, setNow] = useState<number | null>(null)
  useEffect(() => {
    if (status !== 'running') return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [status])
  const start = startedAt ? new Date(startedAt).getTime() : NaN
  const end = completedAt ? new Date(completedAt).getTime() : now ?? start
  const elapsed = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.floor((end - start) / 1000)) : null
  const total = progress?.total_units
  const count = progress && total !== null && total !== undefined ? total === 0 ? '无需执行检查单元' : progress.completed_units + '/' + total + ' ' + progressUnitLabel(progress.unit) : status === 'running' ? '正在检查' : '等待阶段记录'
  return <details className="agent-role-progress agent-coverage" aria-label={name + '审查进度'} data-testid="agent-role-progress">
    <summary>审查进度 · {count}</summary>
    <div style={{ paddingTop: 8 }}>
      <p>当前阶段：{progressPhaseLabel(progress?.phase)}{progress ? ' · 第 ' + progress.round + ' 轮' : ''}</p>
      <p>开始：{timestamp(startedAt)} · 耗时：{elapsed === null ? '待开始' : elapsed + ' 秒'}</p>
      {progress && <p>已完成 {progress.completed_units} {progressUnitLabel(progress.unit)}{total === null ? '' : ' / 共 ' + total + ' ' + progressUnitLabel(progress.unit)}{progress.reused_units > 0 ? ' · 复用 ' + progress.reused_units + ' 项' : ''} · 更新 {timestamp(progress.updated_at)}</p>}
      {progress && typeof total === 'number' && total > 0 && <progress value={progress.completed_units} max={total} aria-label={name + '已完成检查单元'} style={{ width: '100%' }}/>}
      <ol style={{ paddingLeft: 20, maxHeight: 240, overflowY: 'auto' }}>{events.map(event => <li key={event.id} style={{ marginBottom: 6 }}>
        <small>{timestamp(event.created_at)} · {eventTitles[event.type] ?? '执行记录'}{event.payload?.round ? ' · 第 ' + String(event.payload.round) + ' 轮' : ''}{event.payload?.window_index !== undefined ? ' · 第 ' + (Number(event.payload.window_index) + 1) + ' 段' : ''}{event.payload?.batch_index !== undefined ? ' · 第 ' + (Number(event.payload.batch_index) + 1) + ' 批' : ''}{event.payload?.reused ? ' · 复用已有结果' : ''}</small>
      </li>)}</ol>
      {!events.length && <p>尚无执行事件；进度以实际检查回执更新。</p>}
    </div>
  </details>
}
