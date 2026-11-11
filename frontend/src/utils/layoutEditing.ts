import { recognizePlainText } from './documentStructure'
import MarkdownIt from 'markdown-it'

const structureParser = new MarkdownIt({ html: false })

/** Only list questions explicitly introduced as a set; never touch nested blocks or code. */
export function arrangePointLists(text: string): { text: string; groups: number } {
  const lines = text.split('\n')
  const tokens = structureParser.parse(text, {})
  const paragraphs = tokens.filter(token => token.type === 'paragraph_open' && token.level === 0 && token.map)
    .map(token => ({ start: token.map![0], end: token.map![1] }))
  let groups = 0
  for (let i = 0; i < paragraphs.length; i++) {
    const cue = paragraphs[i]!
    const cueText = lines.slice(cue.start, cue.end).join('\n').trim()
    if (!/(?:以下|这些|几个|几项|几点|清单|检查项|问题|步骤|要点)[^。！？\n]{0,24}[：:]$/.test(cueText)) continue
    const items: typeof paragraphs = []
    let previousEnd = cue.end
    for (let j = i + 1; j < paragraphs.length; j++) {
      const item = paragraphs[j]!
      if (lines.slice(previousEnd, item.start).some(line => line.trim())) break
      const content = lines.slice(item.start, item.end).join('\n').trim()
      if (item.end - item.start !== 1 || content.length > 140 || !/[？?]$/.test(content)) break
      items.push(item); previousEnd = item.end
    }
    if (items.length >= 2) {
      items.forEach(item => { lines[item.start] = '- ' + lines[item.start] })
      groups++; i += items.length
    }
  }
  return { text: lines.join('\n'), groups }
}

export function prepareArticleLayout(text: string): string {
  const hasMarkdown = /^(?:#{1,6}\s|```|~~~|>\s|[-*+]\s|\d+\.\s|\|)/m.test(text)
    || /\*[^\n]+\*|_[^\n]+_|~~[^\n]+~~|==[^\n]+==|\[[^\]\n]+\]\([^)]+\)/.test(text)
  let prepared = hasMarkdown ? text : recognizePlainText(text)
  // A Word document may have an unstyled title alongside styled inline text.
  const first = prepared.split('\n')[0] || ''
  if (hasMarkdown && !/^#{1,6}\s/m.test(prepared) && /^[\p{L}\p{N}]/u.test(first)
    && first.length >= 6 && first.length <= 40 && !/[。！？.!?：:]$/.test(first)
    && prepared.startsWith(first + '\n\n') && !/[\\*_~=`\[\]<>]/.test(first)) prepared = '# ' + prepared
  return arrangeQuotes(prepared)
}

export type SelectionFormat = 'heading' | 'quote' | 'pullquote' | 'bold' | 'italic' | 'highlight' | 'strike' | 'paragraph' | 'bullets' | 'numbered'
export function formatSelection(text: string, start: number, end: number, format: SelectionFormat): string {
  if (format === 'bold' || format === 'italic' || format === 'highlight' || format === 'strike') {
    const marker = { bold: '**', italic: '*', highlight: '==', strike: '~~' }[format]
    const selected = text.slice(start, end)
    const value = selected.startsWith(marker) && selected.endsWith(marker) && selected.length > marker.length * 2
      ? selected.slice(marker.length, -marker.length) : marker + selected + marker
    return text.slice(0, start) + value + text.slice(end)
  }
  const from = text.lastIndexOf('\n', Math.max(0, start - 1)) + 1
  const next = text.indexOf('\n', Math.max(start, end - 1))
  const to = next < 0 ? text.length : next
  let number = 0
  const selected = text.slice(from, to).split('\n').map(line => {
    const clean = line.replace(/^(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/, '')
    if (!clean.trim()) return ''
    if (format === 'bullets') return '- ' + clean
    if (format === 'numbered') return `${++number}. ${clean}`
    if (format === 'paragraph') return clean.replace(/^\*\*(.*)\*\*$/, '$1')
    if (format === 'pullquote') return '> **' + clean.replace(/^\*\*|\*\*$/g, '') + '**'
    return (format === 'heading' ? '## ' : format === 'quote' ? '> ' : '') + clean
  }).join('\n')
  return text.slice(0, from) + selected + text.slice(to)
}

/** Only complete, standalone quotations; leave code and existing structures intact. */
export function arrangeQuotes(text: string): string {
  let fence = ''
  return text.split('\n').map(line => {
    const marker = line.match(/^\s*(`{3,}|~{3,})/)
    if (marker) {
      if (!fence) fence = marker[1]!
      else if (marker[1]![0] === fence[0] && marker[1]!.length >= fence.length) fence = ''
      return line
    }
    if (!fence && /^(?:“[^\n]+”|「[^\n]+」)[。！？]?$/.test(line.trim())) return `> ${line.trim()}`
    return line
  }).join('\n')
}
