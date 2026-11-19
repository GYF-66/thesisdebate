import { diffArrays } from 'diff'

export type TextDiffSegment = {
  kind: 'equal' | 'removed' | 'added'
  value: string
}

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })
const MAX_FINE_LENGTH = 16000
const MAX_LINES = 20000
const DIFF_BUDGET_MS = 60

function append(result: TextDiffSegment[], kind: TextDiffSegment['kind'], value: string) {
  if (!value) return
  const last = result[result.length - 1]
  if (last?.kind === kind) last.value += value
  else result.push({ kind, value })
}

// Shared UTF-16 text must end at a complete grapheme in both versions. This
// avoids highlighting half an emoji, an accent, or part of a joined emoji.
function prefixBoundary(text: string, index: number) {
  if (!index || index === text.length) return index
  return segmenter.segment(text).containing(index)?.index ?? index
}

function suffixBoundary(text: string, index: number) {
  if (!index || index === text.length) return index
  const part = segmenter.segment(text).containing(index)
  return !part || part.index === index ? index : part.index + part.segment.length
}

function sharedEdges(before: string, after: string) {
  const limit = Math.min(before.length, after.length)
  let prefix = 0
  while (prefix < limit && before.charCodeAt(prefix) === after.charCodeAt(prefix)) prefix++
  prefix = Math.min(prefixBoundary(before, prefix), prefixBoundary(after, prefix))
  let suffix = 0
  while (suffix < limit - prefix && before.charCodeAt(before.length - suffix - 1) === after.charCodeAt(after.length - suffix - 1)) suffix++
  while (suffix) {
    const adjustment = Math.max(
      suffixBoundary(before, before.length - suffix) - (before.length - suffix),
      suffixBoundary(after, after.length - suffix) - (after.length - suffix),
    )
    if (!adjustment) break
    suffix -= adjustment
  }
  return { prefix, suffix }
}

function graphemes(text: string) {
  return Array.from(segmenter.segment(text), part => part.segment)
}

function lines(text: string) {
  return text.match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g) ?? []
}

function compare(before: string, after: string, result: TextDiffSegment[], deadline: number, refineLines: boolean) {
  if (before === after) { append(result, 'equal', before); return }
  const { prefix, suffix } = sharedEdges(before, after)
  append(result, 'equal', before.slice(0, prefix))
  const oldMiddle = before.slice(prefix, before.length - suffix)
  const newMiddle = after.slice(prefix, after.length - suffix)
  const remaining = () => Math.max(0, deadline - performance.now())

  if (!oldMiddle || !newMiddle) {
    append(result, 'removed', oldMiddle)
    append(result, 'added', newMiddle)
  } else if (oldMiddle.length + newMiddle.length <= MAX_FINE_LENGTH && remaining() > 0) {
    const changes = diffArrays(graphemes(oldMiddle), graphemes(newMiddle), {
      timeout: Math.max(1, remaining()), maxEditLength: 2048,
    })
    if (changes) {
      for (const change of changes) append(result, change.added ? 'added' : change.removed ? 'removed' : 'equal', change.value.join(''))
    } else {
      append(result, 'removed', oldMiddle)
      append(result, 'added', newMiddle)
    }
  } else if (refineLines && remaining() > 0) {
    const oldLines = lines(oldMiddle)
    const newLines = lines(newMiddle)
    const changes = oldLines.length + newLines.length <= MAX_LINES ? diffArrays(oldLines, newLines, {
      timeout: Math.max(1, remaining()), maxEditLength: 1024,
    }) : undefined
    if (changes) {
      for (let index = 0; index < changes.length; index++) {
        const change = changes[index]
        const next = changes[index + 1]
        if (change.removed && next?.added) {
          compare(change.value.join(''), next.value.join(''), result, deadline, false)
          index++
        } else append(result, change.added ? 'added' : change.removed ? 'removed' : 'equal', change.value.join(''))
      }
    } else {
      append(result, 'removed', oldMiddle)
      append(result, 'added', newMiddle)
    }
  } else {
    append(result, 'removed', oldMiddle)
    append(result, 'added', newMiddle)
  }
  append(result, 'equal', suffix ? before.slice(-suffix) : '')
}

/** Exact text fragments for a paired view; null represents an empty version. */
export function computeTextDiff(before: string | null, after: string | null): TextDiffSegment[] {
  const result: TextDiffSegment[] = []
  compare(before ?? '', after ?? '', result, performance.now() + DIFF_BUDGET_MS, true)
  return result
}
