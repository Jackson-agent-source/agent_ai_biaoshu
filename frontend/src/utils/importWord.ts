import mammoth from 'mammoth'
import TurndownService from 'turndown'
import { gfm } from 'turndown-plugin-gfm'
import DOMPurify from 'dompurify'

export async function importWord(file: File): Promise<{ markdown: string; warning: string }> {
  const result = await mammoth.convertToHtml({ arrayBuffer: await file.arrayBuffer() }, {
    styleMap: ["p[style-name='Title'] => h1:fresh", "p[style-name='标题'] => h1:fresh", "p[style-name='标题 1'] => h1:fresh", "p[style-name='标题 2'] => h2:fresh", "p[style-name='标题 3'] => h3:fresh", 'highlight => mark'],
    convertImage: mammoth.images.imgElement(async () => ({ src: '', alt: '图片未导入，请手动补充' })),
  })
  const doc = new DOMParser().parseFromString(DOMPurify.sanitize(result.value), 'text/html')
  doc.querySelectorAll('img').forEach(img => img.replaceWith(doc.createTextNode('[图片未导入，请手动补充]')))
  // Word tables often have no semantic header. Preserve the first row as the Markdown header.
  doc.querySelectorAll('table').forEach(table => {
    table.querySelector('tr')?.querySelectorAll('td').forEach(cell => {
      const header = doc.createElement('th'); header.innerHTML = cell.innerHTML; cell.replaceWith(header)
    })
  })
  const converter = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-' })
  converter.use(gfm)
  converter.addRule('highlight', { filter: 'mark', replacement: content => '==' + content + '==' })
  converter.addRule('strike', { filter: ['s', 'del'], replacement: content => '~~' + content + '~~' })
  converter.addRule('wordTableCell', {
    filter: ['th', 'td'],
    replacement: (content, node) => {
      const prefix = node.previousSibling ? ' ' : '| '
      return `${prefix}${content.trim().replace(/\n+/g, ' ').replace(/\|/g, '\\|')} |`
    },
  })
  return {
    markdown: converter.turndown(doc.body.innerHTML),
    warning: '已提取正文结构；图片、页眉页脚和复杂版式需手动核对。' + (result.messages.length ? ' 文档中有部分格式无法完整识别。' : ''),
  }
}
