import { decodeHtmlEntities, Extension, generateHTML, getSchema, type Extensions, type JSONContent } from '@tiptap/core'
import { CodeBlock } from '@tiptap/extension-code-block'
import { Image } from '@tiptap/extension-image'
import { Link } from '@tiptap/extension-link'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import { BlockMath, InlineMath } from '@tiptap/extension-mathematics'
import { TableKit } from '@tiptap/extension-table'
import { MarkdownManager } from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'
import { Marked, Tokenizer, type marked } from 'marked'

export const MARKDOWN_SCHEMA_VERSION = 1
const backtick = String.fromCharCode(96)
const maxEmbeddedImageLength = 2 * 1024 * 1024

/** URLs are retained as data; importing an image never fetches its source. */
export function isSafeMarkdownUrl(value: unknown, image = false): value is string {
  if (typeof value !== 'string' || !value.trim()) return false
  const compact = decodeHtmlEntities(value).replace(/[\u0000-\u0020\u007f-\u009f]/g, '')
  if (compact.startsWith('data:')) {
    return image && compact.length <= maxEmbeddedImageLength && /^data:image\/(?:png|gif|jpe?g|webp);base64,[a-z0-9+/]+=*$/i.test(compact)
  }
  const scheme = compact.match(/^([a-z][a-z0-9+.-]*):/i)
  if (scheme) return (image ? ['http', 'https'] : ['http', 'https', 'mailto', 'tel']).includes(scheme[1].toLowerCase())
  return !/^[^/?#]*:/.test(compact) && !compact.startsWith('\\')
}

const destination = (value: unknown) => String(value || '').replace(/[\s<>()[\]\\]/g, character => '%' + character.charCodeAt(0).toString(16).toUpperCase())
const titleSuffix = (value: unknown) => value ? ' "' + String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\r\n]/g, ' ') + '"' : ''
const literalLabel = (value: unknown) => String(value || '').replace(/[\\[\]]/g, '\\$&')

const SafeLink = Link.extend({
  renderMarkdown(node, helpers) {
    const label = helpers.renderChildren(node)
    return isSafeMarkdownUrl(node.attrs?.href) ? '[' + label + '](' + destination(node.attrs?.href) + titleSuffix(node.attrs?.title) + ')' : label
  },
}).configure({ openOnClick: false, autolink: false, linkOnPaste: false, HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' }, isAllowedUri: value => isSafeMarkdownUrl(value) })

const OfflineImage = Image.extend({
  renderHTML({ node }) {
    return ['span', { 'data-type': 'offline-image', 'data-src': node.attrs.src || '', 'data-alt': node.attrs.alt || '', 'data-title': node.attrs.title || '', role: 'img', 'aria-label': node.attrs.alt || '图片', title: node.attrs.title || node.attrs.src || '' }, node.attrs.alt || '图片']
  },
  parseHTML() {
    return [{ tag: 'span[data-type="offline-image"]', getAttrs: element => ({ src: element.getAttribute('data-src'), alt: element.getAttribute('data-alt'), title: element.getAttribute('data-title') }) }]
  },
  renderMarkdown(node) {
    const alt = literalLabel(node.attrs?.alt)
    return isSafeMarkdownUrl(node.attrs?.src, true) ? '![' + alt + '](' + destination(node.attrs?.src) + titleSuffix(node.attrs?.title) + ')' : alt
  },
}).configure({ inline: true, allowBase64: true })

// The official extension renders three fixed backticks; choose a longer fence
// when a code sample itself contains a fence, so the sample stays one node.
const FencedCode = CodeBlock.extend({
  renderMarkdown(node) {
    const text = (node.content || []).map((child: JSONContent) => child.text || '').join('')
    const runs = text.match(new RegExp(backtick + '+', 'g')) || []
    const fence = backtick.repeat(Math.max(3, ...runs.map((run: string) => run.length + 1)))
    const language = String(node.attrs?.language || '').replace(/[\r\n]/g, '').split(backtick).join('')
    return fence + language + '\n' + text + '\n' + fence
  },
})

const CompatibleInlineMath = InlineMath.extend({
  renderMarkdown: node => '$$' + String(node.attrs?.latex || '') + '$$',
  markdownTokenizer: {
    name: 'inlineMath', level: 'inline', start: source => source.indexOf('$'),
    tokenize(source) {
      if (source.startsWith('$$$')) return undefined
      const match = source.startsWith('$$') ? source.match(/^\$\$([^\n]+?)\$\$(?!\$)/) : source.match(/^\$([^$\n]+?)\$(?!\$)/)
      return match ? { type: 'inlineMath', raw: match[0], latex: match[1] } : undefined
    },
  },
}).configure({ katexOptions: { throwOnError: false, trust: false } })

const CompatibleBlockMath = BlockMath.extend({
  renderMarkdown: node => '$$$\n' + String(node.attrs?.latex || '') + '\n$$$',
  markdownTokenizer: {
    name: 'blockMath', level: 'block', start: source => source.search(/^ {0,3}(?:\$\$\$[^\n]*?\$\$\$[ \t]*(?:\n|$)|\${2,3}[ \t]*(?:\n|$))/m),
    tokenize(source) {
      const triple = source.match(/^ {0,3}\$\$\$([^\n]*?)\$\$\$[ \t]*(?:\n|$)/)
      if (triple) return { type: 'blockMath', raw: triple[0], latex: triple[1] }
      const opening = source.match(/^ {0,3}(\${2,3})[ \t]*\n/)
      if (!opening) return undefined
      const remaining = source.slice(opening[0].length)
      const closing = new RegExp('^ {0,3}\\' + opening[1].split('').join('\\') + '[ \\t]*(?:\\n|$)', 'm').exec(remaining)
      if (!closing) return undefined
      return { type: 'blockMath', raw: source.slice(0, opening[0].length + closing.index + closing[0].length), latex: remaining.slice(0, closing.index).replace(/\n$/, '') }
    },
  },
}).configure({ katexOptions: { throwOnError: false, trust: false } })

// HTML stays literal text instead of entering a DOM parser. This also makes
// parsing identical in the browser and during static rendering/testing.
const LiteralHtml = Extension.create({
  name: 'literalMarkdownHtml', priority: 2000, markdownTokenName: 'literalMarkdownHtml',
  parseMarkdown(token, helpers) {
    if (/^\s*<br\s*\/?\s*>\s*$/i.test(String(token.raw || token.text || ''))) return helpers.createNode('hardBreak')
    const text = helpers.createTextNode(String(token.raw || token.text || ''))
    return token.block ? helpers.createNode('paragraph', undefined, [text]) : text
  },
})

export const editorExtensions: Extensions = [
  StarterKit.configure({ codeBlock: false, link: false }), FencedCode, SafeLink,
  TaskList, TaskItem.configure({ nested: true }), TableKit,
  OfflineImage, CompatibleInlineMath, CompatibleBlockMath, LiteralHtml,
]

let manager: MarkdownManager | undefined
function markdownManager() {
  if (!manager) {
    const lexer = new Marked({ gfm: true, breaks: false, tokenizer: {
      url: () => undefined,
      html(source) { const token = Tokenizer.prototype.html.call(this, source); return token ? { ...token, type: 'literalMarkdownHtml' } as unknown as typeof token : undefined },
      tag(source) { const token = Tokenizer.prototype.tag.call(this, source); return token ? { ...token, type: 'literalMarkdownHtml' } as unknown as typeof token : undefined },
    } })
    manager = new MarkdownManager({ extensions: editorExtensions, marked: lexer as unknown as typeof marked, indentation: { style: 'space', size: 2 } })
  }
  return manager
}

/** Only repair the screenshot's standalone '**question? **' form. */
export function normalizeLegacyMarkdown(markdown: string): string {
  let fence = '', fenceLength = 0, mathFence = ''
  return markdown.split('\n').map(line => {
    const codeFence = line.match(/^ {0,3}([\x60~]{3,})(.*)$/)
    if (fence) {
      if (codeFence && [...codeFence[1]].every(character => character === fence) && codeFence[1].length >= fenceLength && !codeFence[2].trim()) fence = ''
      return line
    }
    if (mathFence) { if (line.trim() === mathFence) mathFence = ''; return line }
    if (codeFence && [...codeFence[1]].every(character => character === codeFence[1][0])) { fence = codeFence[1][0]; fenceLength = codeFence[1].length; return line }
    if (/^ {0,3}\${2,3}[ \t]*\r?$/.test(line)) { mathFence = line.trim(); return line }
    if (line.includes(backtick) || line.includes('$') || /^ {4}|^\t/.test(line)) return line
    const candidate = line.match(/^( {0,3})(\*\*|__)(\S.*?)[ \t]+\2([ \t]*\r?)$/)
    if (!candidate || candidate[3].includes(candidate[2]) || /\\$/.test(candidate[3])) return line
    return candidate[1] + candidate[2] + candidate[3].trimEnd() + candidate[2] + candidate[4]
  }).join('\n')
}

const supportedMarks = new Set(['bold', 'italic', 'strike', 'code', 'underline', 'link'])
/** Safe for persisted JSON too, not only Markdown-created nodes. */
export function sanitizeEditorDocument(document: JSONContent): JSONContent {
  function visit(node: JSONContent): JSONContent {
    const clean: JSONContent = { type: node.type }
    if (node.text !== undefined) clean.text = node.text
    if (node.attrs) clean.attrs = { ...node.attrs }
    if (node.type === 'image' && !isSafeMarkdownUrl(node.attrs?.src, true)) return { type: 'text', text: String(node.attrs?.alt || '[图片地址已阻止]') }
    if (node.type === 'image') clean.attrs = { src: node.attrs?.src, alt: node.attrs?.alt || null, title: node.attrs?.title || null }
    if (node.marks) clean.marks = node.marks.filter(mark => supportedMarks.has(mark.type) && (mark.type !== 'link' || isSafeMarkdownUrl(mark.attrs?.href))).map(mark => mark.type === 'link' ? { type: 'link', attrs: { href: mark.attrs?.href, title: mark.attrs?.title || null, target: '_blank', rel: 'noopener noreferrer' } } : { ...mark })
    if (node.content) {
      const children = node.content.map(visit)
      if (['doc', 'blockquote', 'listItem', 'taskItem', 'tableCell', 'tableHeader'].includes(node.type || '')) {
        clean.content = []
        let inlineRun: JSONContent[] = []
        const flushInline = () => { if (inlineRun.length) { clean.content!.push({ type: 'paragraph', content: inlineRun }); inlineRun = [] } }
        for (const child of children) {
          if (['text', 'image', 'inlineMath', 'hardBreak'].includes(child.type || '')) inlineRun.push(child)
          else { flushInline(); clean.content.push(child) }
        }
        flushInline()
      } else clean.content = children
    }
    // List items require a leading paragraph, including empty imported items
    // and persisted items whose only content is a nested list.
    if (['listItem', 'taskItem'].includes(node.type || '') && clean.content?.[0]?.type !== 'paragraph') {
      clean.content = [{ type: 'paragraph' }, ...(clean.content || [])]
    }
    return clean
  }
  const safe = visit(document)
  if (safe.type === 'doc' && !safe.content?.length) safe.content = [{ type: 'paragraph' }]
  return safe
}

export function parseMarkdown(markdown: string): JSONContent {
  const parsed = sanitizeEditorDocument(markdownManager().parse(normalizeLegacyMarkdown(markdown)))
  const node = getSchema(editorExtensions).nodeFromJSON(parsed)
  node.check()
  return node.toJSON()
}

export function serializeMarkdown(document: JSONContent): string {
  return markdownManager().serialize(sanitizeEditorDocument(document))
}

/** Browser-only compatibility API; prefer parseMarkdown and native JSON. */
export function markdownToHtml(markdown: string): string {
  return generateHTML(parseMarkdown(markdown), editorExtensions)
}

export type StoredEditorDocument = JSONContent & { markdownSchema?: number }
/** Reparse only the old lossy converter's plain schema; keep rich/manual data. */
export function migrateLegacyDocument(markdown: string, stored?: StoredEditorDocument | null): JSONContent {
  if (!stored) return parseMarkdown(markdown)
  if (stored.markdownSchema === MARKDOWN_SCHEMA_VERSION) return sanitizeEditorDocument(stored)
  const onlyLegacyPlain = (node: JSONContent): boolean => ['doc', 'paragraph', 'heading', 'text'].includes(node.type || '') && !node.marks?.length && (!node.attrs || Object.keys(node.attrs).every(key => node.type === 'heading' && key === 'level')) && (!node.content || node.content.every(onlyLegacyPlain))
  if (onlyLegacyPlain(stored)) return parseMarkdown(markdown)
  function migratePlainInline(node: JSONContent): JSONContent {
    if (['paragraph', 'heading'].includes(node.type || '') && node.content?.length && node.content.every(child => child.type === 'text' && !child.marks?.length)) {
      const text = node.content.map(child => child.text || '').join('')
      if (text.includes('*') || text.includes('_') || text.includes('~') || text.includes('[') || text.includes(backtick)) {
        const parsed = parseMarkdown(text)
        if (parsed.content?.length === 1 && parsed.content[0].type === 'paragraph') return { ...node, content: parsed.content[0].content }
      }
    }
    return node.content ? { ...node, content: node.content.map(migratePlainInline) } : { ...node }
  }
  return sanitizeEditorDocument(migratePlainInline(stored))
}
