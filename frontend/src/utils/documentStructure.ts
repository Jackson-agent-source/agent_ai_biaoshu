/** Conservative, local structure recognition. Never rewrites the user's sentences. */
export function recognizePlainText(text: string): string {
  const lines = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n')
  const first = lines.findIndex(line => line.trim())
  let last = lines.length - 1
  while (last >= 0 && !lines[last]?.trim()) last--
  return lines.map((raw, index) => {
    const line = raw.trim()
    if (!line) return ''
    // Escape Markdown syntax in prose so literal symbols remain visible.
    const escaped = line.replace(/([\\`*_{}\[\]<>#|])/g, '\\$1')
    if (/^[一二三四五六七八九十百]+[、．.]/.test(line) && line.length <= 60) return `## ${escaped}`
    if (/^[（(][一二三四五六七八九十百]+[）)]/.test(line) && line.length <= 60) return `### ${escaped}`
    if (/^[•●▪]\s*/.test(line)) return `- ${line.replace(/^[•●▪]\s*/, '').replace(/([\\`*_{}\[\]<>#|])/g, '\\$1')}`
    if (/^\d+[、）)]\s*/.test(line)) return line.replace(/^(\d+)[、）)]\s*/, '$1. ').replace(/([\\`*_{}\[\]<>#|])/g, '\\$1')
    if (/^\d+\.\s+/.test(line)) return escaped
    if (index === first && line.length <= 40 && !/[。！？.!?；;：:]$/.test(line) && index < last) return `# ${escaped}`
    if (line.length >= 6 && line.length <= 40 && !/[。！？.!?；;：:”"」』）)]$/.test(line)
      && index > first && !lines[index - 1]?.trim() && !lines[index + 1]?.trim()
      && index < last) return `## ${escaped}`
    return escaped.replace(/^(\d+)\./, '$1\\.')
  }).reduce<string[]>((blocks, line) => {
    const previous = blocks[blocks.length - 1] || ''
    const list = /^(?:- |\d+\. )/
    if (line && previous && !(list.test(line) && list.test(previous))) blocks.push('')
    blocks.push(line)
    return blocks
  }, []).join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
