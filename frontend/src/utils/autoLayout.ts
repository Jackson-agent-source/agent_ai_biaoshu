import MarkdownIt from 'markdown-it'

const parser = new MarkdownIt({ html: false })
export interface LayoutBlock { id: number; text: string; kind: 'paragraph' | 'heading'; start: number; end: number; plain: boolean }
export interface LayoutPlan {
  sections?: { before: number; title: string }[]
  lists?: { ids: number[]; ordered: boolean }[]
  splits?: { id: number; items: string[] }[]
  emphasis?: { id: number; text: string; style: 'bold' | 'highlight' | 'italic' }[]
}
export function layoutBlocks(text: string): LayoutBlock[] {
  const lines = text.split('\n')
  const tokens = parser.parse(text, {})
  return tokens.flatMap((token, index) => {
    if (token.level !== 0 || !token.map || !['paragraph_open', 'heading_open'].includes(token.type)) return []
    const raw = lines.slice(...token.map).join('\n')
    return [{ id: token.map[0], text: token.type === 'heading_open' ? tokens[index + 1]!.content : raw,
      kind: token.type === 'heading_open' ? 'heading' as const : 'paragraph' as const,
      start: token.map[0], end: token.map[1],
      plain: tokens[index + 1]?.children?.every(child => ['text', 'softbreak'].includes(child.type)) === true
        && !/[\\*_~=`\[\]<>]/.test(raw),
    }]
  })
}
const escapeMarkdown = (text: string) => text.replace(/[\\`*_{}\[\]()#+.!|>~=\-]/g, '\\$&')

/** Apply only edits anchored to original blocks. Invalid or overlapping suggestions are ignored. */
export function applyLayoutPlan(text: string, plan: LayoutPlan): { text: string; changes: number } {
  const blocks = layoutBlocks(text)
  const byId = new Map(blocks.map(block => [block.id, block]))
  const lines = text.split('\n')
  const replacements = new Map<number, string>()
  const headings = new Map<number, string>()
  const listPrefixes = new Map<number, string>()
  let changes = 0
  const existingTitles = new Set(blocks.filter(block => block.kind === 'heading').map(block => block.text))
  for (const section of (plan.sections || []).slice(0, 12)) {
    const block = byId.get(section.before)
    if (!block || block.kind !== 'paragraph' || headings.has(block.id) || typeof section.title !== 'string') continue
    const title = section.title.trim()
    if (!title || title.length > 40 || /[\r\n]/.test(title) || existingTitles.has(title)) continue
    headings.set(block.id, '## ' + escapeMarkdown(title) + '\n\n'); existingTitles.add(title); changes++
  }
  for (const group of (plan.lists || []).slice(0, 20)) {
    if (!Array.isArray(group.ids) || group.ids.length < 2 || group.ids.length > 20 || new Set(group.ids).size !== group.ids.length) continue
    const items = group.ids.map(id => byId.get(id))
    if (items.some((block, index) => !block || block.kind !== 'paragraph' || listPrefixes.has(block.id)
      || (index > 0 && (headings.has(block.id) || block.start <= items[index - 1]!.start
        || lines.slice(items[index - 1]!.end, block.start).some(line => line.trim()))))) continue
    const first = items[0]!
    const previous = blocks[blocks.findIndex(block => block.id === first.id) - 1]
    const hasCue = previous?.kind === 'paragraph' && !lines.slice(previous.end, first.start).some(line => line.trim())
      && /(?:[：:]$|(?:以下|清单|步骤|要点|[两二三四五六几\d]+个方面)[^。！？\n]{0,24}[。！？]?$)/.test(previous.text.trim())
    const explicitPoints = items.every(block => /^(?:首先|其次|最后|第[一二三四五六]|[一二三四五六]、)/.test(block!.text)
      || /[？?]$/.test(block!.text))
    // Consecutive paragraphs are often a narrative, not a list; require a visible cue.
    if (!hasCue && !explicitPoints) continue
    if (items.some(block => /^(?:当然|但是|不过|因此|比如|例如|假设|如果)/.test(block!.text))) continue
    items.forEach((block, index) => listPrefixes.set(block!.id, group.ordered ? `${index + 1}. ` : '- ')); changes++
  }
  for (const block of blocks) {
    if (block.kind !== 'paragraph') continue
    let content = block.text
    const edits: { start: number; end: number; marker: string }[] = []
    if (block.plain) for (const item of (plan.emphasis || []).filter(item => item.id === block.id).slice(0, 2)) {
      if (typeof item.text !== 'string' || !item.text.trim() || item.text !== item.text.trim() || item.text.length > 120 || /\n/.test(item.text)) continue
      const start = content.indexOf(item.text)
      const marker = { bold: '**', highlight: '==', italic: '*' }[item.style]
      if (!marker || start < 0 || content.lastIndexOf(item.text) !== start) continue
      const end = start + item.text.length
      if (edits.some(edit => start < edit.end && end > edit.start)) continue
      edits.push({ start, end, marker })
    }
    const split = (plan.splits || []).find(item => item.id === block.id)
    const validSplit = block.plain && !listPrefixes.has(block.id) && split && Array.isArray(split.items)
      && split.items.length >= 2 && split.items.length <= 12
      && split.items.every(item => typeof item === 'string' && item.trim()) && split.items.join('') === content
    // Apply formatting within each fragment, never across list-item boundaries.
    const fragments = validSplit ? split.items : [content]
    let offset = 0
    content = fragments.map(fragment => {
      const from = offset; offset += fragment.length
      let formatted = fragment
      for (const edit of edits.filter(edit => edit.start >= from && edit.end <= offset).sort((a, b) => b.start - a.start)) {
        formatted = formatted.slice(0, edit.start - from) + edit.marker + formatted.slice(edit.start - from, edit.end - from) + edit.marker + formatted.slice(edit.end - from)
        changes++
      }
      return validSplit ? '- ' + formatted.trim().replace(/\n/g, '\n  ') : formatted
    }).join('\n')
    if (validSplit) changes++
    const prefix = listPrefixes.get(block.id)
    if (prefix) content = prefix + content.replace(/\n/g, '\n' + ' '.repeat(prefix.length))
    replacements.set(block.id, (headings.get(block.id) || '') + content)
  }
  for (const block of [...blocks].reverse()) {
    if (replacements.has(block.id)) lines.splice(block.start, block.end - block.start, replacements.get(block.id)!)
  }
  return { text: lines.join('\n'), changes }
}
