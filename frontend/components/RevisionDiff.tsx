'use client'

import { createElement, useMemo } from 'react'
import { computeTextDiff } from '../lib/revisionDiff'

export type TextDiffProps = {
  before: string | null
  after: string | null
  side: 'before' | 'after'
}

/** Keep the common text plain and mark only insertions or deletions. */
export function TextDiff({ before, after, side }: TextDiffProps) {
  const segments = useMemo(() => computeTextDiff(before, after), [before, after])
  return createElement('span', { className: 'revision-text', 'data-diff-side': side },
    segments.map((segment, index) => {
      if (segment.kind === 'equal') return segment.value
      if (segment.kind === 'removed' && side === 'before') {
        return createElement('del', { className: 'revision-removed diff-token-delete', 'data-diff-kind': 'removed', key: index }, segment.value)
      }
      if (segment.kind === 'added' && side === 'after') {
        return createElement('ins', { className: 'revision-added diff-token-insert', 'data-diff-kind': 'added', key: index }, segment.value)
      }
      return null
    }),
  )
}
