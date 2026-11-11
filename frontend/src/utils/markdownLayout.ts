import MarkdownIt from 'markdown-it'
import mark from 'markdown-it-mark'
import DOMPurify from 'dompurify'

export const layoutThemes = [
  { id: 'tech', name: '清透科技', description: '蓝紫点缀 · 舒展标题 · 流畅长文', color: '#6266ac', background: '#f5f5fb', serif: false },
  { id: 'blue', name: '清爽蓝', description: '观点、研究与日常分享', color: '#2563a6', background: '#edf5fc', serif: false },
  { id: 'green', name: '公众号绿', description: '公众号长文与行业观察', color: '#187554', background: '#edf7f1', serif: false },
  { id: 'editorial', name: '杂志长文', description: '大标题 · 导语 · 从容留白', color: '#71364c', background: '#faf6f7', serif: true },
  { id: 'technical', name: '技术文档', description: '教程、代码与产品说明', color: '#334155', background: '#f1f5f9', serif: false },
] as const
export type LayoutTheme = typeof layoutThemes[number]['id']
const parser = new MarkdownIt({ html: false, linkify: true, typographer: true, breaks: false }).use(mark)

export const paperBackgrounds = [
  { id: 'mist', name: '雾紫浅底', color: '#eef0f8' },
  { id: 'ice', name: '冰蓝纸面', color: '#edf5f9' },
  { id: 'none', name: '纯白', color: '#ffffff' },
  { id: 'grid', name: '科技网格', color: '#eaf0f7' },
  { id: 'aurora', name: '蓝紫光晕', color: '#eceafa' },
  { id: 'journal', name: '纸感杂志', color: '#f1eee8' },
] as const
export type PaperBackground = typeof paperBackgrounds[number]['id']
export const paperScenes = [
  { id: 'grid', name: '科技网格', description: '深蓝封面 · 精密网格 · 蓝色章节', preview: 'background-color:#172b4b;background-image:linear-gradient(#8cb7eb20 1px,transparent 1px),linear-gradient(90deg,#8cb7eb20 1px,transparent 1px);background-size:18px 18px;color:#ecf5ff;' },
  { id: 'aurora', name: '蓝紫光晕', description: '流动光色 · 柔和封面 · 通透正文', preview: 'background-color:#ede9fc;background-image:radial-gradient(ellipse at 95% 10%,#99e9eb 0,transparent 65%),radial-gradient(ellipse at 0 90%,#a7a4ed 0,transparent 70%);color:#393764;' },
  { id: 'journal', name: '纸感杂志', description: '细点纸纹 · 墨色标题 · 米白留白', preview: 'background-color:#eee9de;background-image:radial-gradient(#b8ae9638 .7px,transparent .7px);background-size:5px 5px;color:#3f403b;' },
] as const
export interface LayoutOptions { lead?: boolean; paragraphGap?: number; displayTitle?: string; outline?: boolean; numberedSections?: boolean; paperBackground?: PaperBackground; contentWidth?: number; chapterPanels?: boolean; decorativeCover?: boolean }
export function renderMarkdownLayout(text: string, themeId: LayoutTheme, fontSize: number, lineHeight: number, options: LayoutOptions = {}): string {
  const theme = layoutThemes.find(item => item.id === themeId) || layoutThemes[0]
  const doc = new DOMParser().parseFromString(DOMPurify.sanitize(parser.render(text)), 'text/html')
  const serif = "Georgia,'Noto Serif SC','Source Han Serif SC','Songti SC',SimSun,serif"
  const sans = "-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif"
  const gap = Math.max(12, Math.min(36, options.paragraphGap ?? 22))
  if (options.displayTitle?.trim() && !doc.body.querySelector('h1')) {
    const heading = doc.createElement('h1'); heading.textContent = options.displayTitle.trim()
    doc.body.prepend(heading)
  }
  const rules: Record<string, string> = {
    h1: `font-size:1.9em;line-height:1.4;margin:0 0 28px;font-weight:750;color:${theme.color};`,
    h2: `font-size:1.35em;line-height:1.5;margin:36px 0 18px;padding:0 0 10px;border-bottom:2px solid ${theme.color};color:${theme.color};`,
    h3: `font-size:1.12em;margin:26px 0 12px;color:${theme.color};`,
    p: `margin:0 0 ${gap}px;overflow-wrap:anywhere;text-align:left;`,
    blockquote: `margin:24px 0;padding:16px 20px;border-left:4px solid ${theme.color};background:${theme.background};color:#475569;`,
    ul: 'margin:16px 0 24px;padding-left:26px;', ol: 'margin:16px 0 24px;padding-left:26px;',
    li: 'margin:8px 0;', strong: `font-weight:700;color:${theme.color};`,
    mark: 'background:#fff0b8;color:#423d30;padding:1px 3px;border-radius:2px;',
    em: 'font-style:italic;',
    s: 'text-decoration:line-through;color:#858897;',
    del: 'text-decoration:line-through;color:#858897;',
    a: `color:${theme.color};text-decoration:underline;overflow-wrap:anywhere;`,
    pre: 'background:#f1f5f9;color:#243244;padding:18px;border-radius:6px;overflow-x:auto;white-space:pre;font-size:13px;line-height:1.7;margin:24px 0;',
    code: 'font-family:Consolas,Menlo,monospace;background:#f1f5f9;padding:2px 5px;border-radius:3px;font-size:.9em;',
    table: 'width:100%;border-collapse:collapse;margin:24px 0;font-size:.9em;word-break:break-word;',
    th: `padding:10px;border:1px solid #d7e0e8;background:${theme.background};text-align:left;`,
    td: 'padding:10px;border:1px solid #d7e0e8;text-align:left;',
    img: 'max-width:100%;height:auto;display:block;margin:24px auto;',
    hr: 'border:0;border-top:1px solid #d7e0e8;margin:32px 0;',
  }
  if (themeId === 'editorial') Object.assign(rules, {
    h1: `font-family:${serif};font-size:2.1em;font-weight:700;line-height:1.45;letter-spacing:-.02em;color:#25272b;margin:8px 0 28px;`,
    h2: `font-family:${serif};font-size:1.5em;font-weight:700;line-height:1.55;color:#25272b;margin:48px 0 22px;padding:0;border:0;`,
    h3: `font-size:1.08em;font-weight:700;line-height:1.6;color:${theme.color};margin:32px 0 16px;`,
    blockquote: `font-family:${serif};font-size:1.18em;line-height:1.85;color:${theme.color};margin:30px 0;padding:6px 0 6px 20px;border-left:2px solid #d8c4cb;`,
    strong: 'font-weight:700;color:#25272b;',
    hr: 'border:0;border-top:1px solid #d8c4cb;width:48px;margin:40px 0;',
  })
  if (themeId === 'green') Object.assign(rules, {
    h2: `font-size:1.25em;line-height:1.6;margin:36px 0 20px;padding:8px 14px;border-left:4px solid ${theme.color};background:${theme.background};color:${theme.color};`,
    strong: `color:${theme.color};font-weight:700;background:${theme.background};`,
  })
  if (themeId === 'technical') Object.assign(rules, {
    h1: 'font-size:1.8em;line-height:1.4;margin:0 0 24px;color:#1e293b;',
    h2: 'font-size:1.25em;line-height:1.6;margin:32px 0 16px;padding-bottom:8px;border-bottom:1px solid #cbd5e1;color:#1e293b;',
  })
  if (themeId === 'tech') Object.assign(rules, {
    h1: 'font-size:2em;line-height:1.5;font-weight:750;letter-spacing:-.02em;text-align:center;text-wrap:balance;margin:10px 0 38px;padding:0;color:#272c41;',
    h2: 'font-size:1.32em;line-height:1.65;font-weight:700;letter-spacing:.01em;margin:44px 0 20px;padding:0;color:#454d81;',
    h3: 'font-size:1.08em;line-height:1.7;font-weight:650;margin:28px 0 14px;padding:0;color:#6266ac;',
    p: `font-size:1em;line-height:inherit;margin:0 0 ${gap}px;color:#3f4351;overflow-wrap:anywhere;text-align:left;`,
    blockquote: 'margin:22px 0 26px 8px;padding:2px 0 2px 18px;border-left:2px solid #e0e0f0;color:#6c7085;font-size:1em;line-height:1.95;',
    strong: 'font-weight:650;color:#4e5290;background:#f0eefb;padding:0 2px;',
    em: 'font-style:italic;color:#73738e;',
    ul: 'margin:18px 0 28px;padding-left:24px;',
    ol: 'margin:18px 0 28px;padding-left:26px;',
    li: 'margin:10px 0;padding-left:4px;color:#3f4351;',
    a: 'color:#6266ac;text-decoration:underline;text-decoration-color:#d3d2ec;text-underline-offset:4px;overflow-wrap:anywhere;',
    pre: 'background:#f5f5fa;color:#535875;padding:20px;border-radius:6px;overflow-x:auto;white-space:pre;font-size:13px;line-height:1.8;margin:28px 0;',
    code: 'font-family:Consolas,Menlo,monospace;background:#f1f1f8;color:#6266ac;padding:2px 5px;border-radius:3px;font-size:.9em;',
    th: 'padding:12px;border-bottom:1px solid #d8daeb;background:#f1f2fa;color:#535b8d;font-weight:650;text-align:left;',
    td: 'padding:12px;border-bottom:1px solid #eaebf3;color:#3f4351;text-align:left;',
    hr: 'border:0;border-top:1px solid #d4d5e8;width:48px;margin:38px auto;',
    img: 'max-width:100%;height:auto;display:block;margin:30px auto;border-radius:4px;',
  })
  doc.body.querySelectorAll('*').forEach(element => {
    if (rules[element.tagName.toLowerCase()]) element.setAttribute('style', rules[element.tagName.toLowerCase()]!)
    if (element.tagName === 'A') { element.setAttribute('target', '_blank'); element.setAttribute('rel', 'noopener noreferrer') }
    if (element.tagName === 'IMG') element.setAttribute('referrerpolicy', 'no-referrer')
    if (element.tagName === 'CODE' && element.parentElement?.tagName === 'PRE') element.setAttribute('style', 'font-family:Consolas,Menlo,monospace;font-size:inherit;background:transparent;color:inherit;padding:0;')
  })
  if (themeId === 'tech') {
    doc.body.querySelectorAll('h1').forEach(heading => {
      const accent = doc.createElement('span')
      accent.setAttribute('aria-hidden', 'true')
      accent.setAttribute('style', 'display:block;width:28px;height:3px;border-radius:3px;background:#a5a5d4;margin:0 auto 24px;')
      heading.prepend(accent)
    })
    doc.body.querySelectorAll('blockquote').forEach(quote => {
      const paragraph = quote.firstElementChild
      if (quote.children.length === 1 && paragraph?.tagName === 'P'
        && paragraph.children.length === 1 && paragraph.firstElementChild?.tagName === 'STRONG'
        && paragraph.textContent === paragraph.firstElementChild.textContent) {
        quote.setAttribute('style', 'margin:34px 0;padding:14px 8px;color:#6266ac;text-align:center;font-size:1.15em;line-height:1.9;')
        paragraph.setAttribute('style', 'text-align:center;margin:0;')
        paragraph.firstElementChild.setAttribute('style', 'font-weight:500;color:inherit;background:transparent;')
      }
    })
    doc.body.querySelectorAll('h1 strong, h1 em, th *, blockquote p').forEach(node => {
      const element = node as HTMLElement
      element.style.color = 'inherit'; element.style.background = 'transparent'
    })
  }
  doc.body.querySelectorAll('blockquote > p:last-child, li > p:last-child').forEach(node => {
    (node as HTMLElement).style.marginBottom = '0'
  })
  if (options.lead) {
    const first = Array.from(doc.body.children).find(node => node.tagName !== 'H1')
    if (first?.tagName === 'P') first.setAttribute('style', themeId === 'tech'
      ? 'font-size:1.06em;line-height:1.95;color:#6f7382;margin:0 0 32px;padding:0;'
      : 'font-size:1.08em;line-height:1.9;color:#65646b;margin:0 0 38px;padding-bottom:26px;border-bottom:1px solid #e8e4e6;')
  }
  if (options.numberedSections) {
    Array.from(doc.body.children).filter(node => node.tagName === 'H2').forEach((heading, index) => {
      if (/^(?:\d+[.、．\s]|[一二三四五六七八九十]+[、．.]|第.+[章节])/.test(heading.textContent?.trim() || '')) return
      const number = doc.createElement('span')
      number.textContent = `${String(index + 1).padStart(2, '0')} `
      number.setAttribute('style', `display:inline-block;margin-right:10px;font-family:Consolas,Menlo,monospace;font-size:.78em;font-weight:500;color:${theme.color};vertical-align:baseline;`)
      heading.prepend(number)
    })
  }
  if (options.outline) {
    const headings = Array.from(doc.body.children).filter(node => node.tagName === 'H2')
    if (headings.length >= 2) {
      const outline = doc.createElement('section')
      outline.setAttribute('style', 'margin:28px 0 36px;padding:16px 0;border-top:1px solid #e8e8f2;border-bottom:1px solid #e8e8f2;')
      const label = doc.createElement('p'); label.textContent = '本文脉络'
      label.setAttribute('style', 'margin:0 0 12px;font-size:13px;font-weight:650;color:#6266ac;')
      outline.append(label)
      headings.forEach(heading => {
        const item = doc.createElement('p'); item.textContent = heading.textContent
        item.setAttribute('style', 'margin:8px 0;font-size:14px;line-height:1.7;color:#6c7085;')
        outline.append(item)
      })
      const title = doc.body.querySelector(':scope > h1')
      const intro = title?.nextElementSibling
      if (intro?.tagName === 'P' && options.lead) intro.after(outline)
      else if (title) title.after(outline)
      else doc.body.prepend(outline)
    }
  }
  const background = paperBackgrounds.find(item => item.id === options.paperBackground) || paperBackgrounds[2]
  const scene = paperScenes.find(item => item.id === background.id)
  const panelColor = background.id === 'ice' ? '#edf5fa' : theme.background
  if (options.chapterPanels) {
    doc.body.querySelectorAll(':scope > h2').forEach(node => {
      const heading = node as HTMLElement
      heading.style.backgroundColor = panelColor
      heading.style.padding = '12px 16px'
      heading.style.border = '0'
      heading.style.borderLeft = `3px solid ${theme.color}`
      heading.style.borderRadius = '0 6px 6px 0'
    })
    doc.body.querySelectorAll('blockquote').forEach(node => {
      const quote = node as HTMLElement
      quote.style.backgroundColor = panelColor
      quote.style.padding = '16px 18px'
      quote.style.marginLeft = '0'
      quote.style.marginRight = '0'
      quote.style.borderRadius = '6px'
    })
  }
  if (scene) {
    const ink = scene.id === 'grid' ? '#245b8c' : scene.id === 'aurora' ? '#61549a' : '#52544a'
    const tint = scene.id === 'grid' ? '#eef5fb' : scene.id === 'aurora' ? '#f2effa' : '#f5f1e9'
    if (options.chapterPanels) {
      doc.body.querySelectorAll(':scope > h2').forEach((node, index) => {
        const heading = node as HTMLElement
        heading.style.color = ink
        heading.style.backgroundColor = tint
        heading.style.borderLeftColor = ink
        heading.querySelectorAll('span').forEach(span => { span.style.color = ink })
        if (scene.id === 'journal') {
          heading.style.backgroundColor = 'transparent'
          heading.style.borderLeft = '0'
          heading.style.borderTop = '1px solid #c9c3b5'
          heading.style.borderBottom = '1px solid #c9c3b5'
          heading.style.padding = '18px 0'
          heading.style.fontFamily = serif
        } else if (scene.id === 'aurora') {
          heading.style.borderLeft = '0'
          heading.style.borderRadius = '12px'
          heading.style.backgroundImage = `linear-gradient(105deg,${index % 2 ? '#e9f5f7' : '#ebe7fa'},#f8f7fc)`
        }
      })
      doc.body.querySelectorAll('blockquote').forEach(node => {
        const quote = node as HTMLElement
        quote.style.backgroundColor = tint; quote.style.borderLeftColor = ink
        quote.style.color = ink
      })
    }
    if (options.decorativeCover !== false) {
      const heading = doc.body.querySelector(':scope > h1') as HTMLElement | null
      if (heading) {
        const intro = options.lead && heading.nextElementSibling?.tagName === 'P' ? heading.nextElementSibling as HTMLElement : null
        const cover = doc.createElement('section')
        cover.setAttribute('style', `box-sizing:border-box;margin:-30px -20px 32px;padding:26px 24px 30px;border-radius:10px 10px 0 0;${scene.preview}`)
        const art = doc.createElement('section')
        art.setAttribute('aria-hidden', 'true')
        art.setAttribute('style', scene.id === 'grid'
          ? 'height:88px;margin:0 0 24px;border:1px solid #6b91bc66;border-radius:50% 50% 8px 8px;background-image:radial-gradient(ellipse at 50% 100%,#8ed9ef55,transparent 65%);'
          : scene.id === 'aurora'
            ? 'height:100px;margin:0 0 20px;border:1px solid #ffffffaa;border-radius:90px;background-image:radial-gradient(ellipse at 30% 90%,#ffffffb0,transparent 70%);'
            : 'height:36px;margin:0 0 26px;border-top:4px solid #55594c;border-bottom:1px solid #a7a494;')
        heading.querySelector('[aria-hidden="true"]')?.remove()
        heading.style.cssText = `font-size:1.9em;line-height:1.55;font-weight:750;text-align:left;margin:0;color:inherit;${scene.id === 'journal' ? `font-family:${serif};` : ''}`
        // Keep title and introduction as real text; decorative layers contain no article content.
        heading.before(cover); cover.append(art, heading)
        if (intro) {
          intro.style.cssText = `font-size:.95em;line-height:1.9;margin:20px 0 0;color:${scene.id === 'grid' ? '#c0d4ec' : '#656078'};`
          cover.append(intro)
        }
      }
    }
  }
  const measure = Number.isFinite(options.contentWidth) ? Math.max(480, Math.min(760, options.contentWidth!)) : 680
  const paperStyle = themeId === 'tech' ? 'padding:40px 24px 32px;background:#ffffff;' : 'padding:36px 24px;background:white;'
  const typography = `font-family:${sans};font-size:${Math.max(12, Math.min(22, fontSize))}px;line-height:${Math.max(1.4, Math.min(2.4, lineHeight))};color:#353b42;letter-spacing:.025em;overflow-wrap:anywhere;`
  if (background.id !== 'none') {
    if (scene) {
      const texture = scene.id === 'grid'
        ? 'background-image:linear-gradient(#a6b9d22e 1px,transparent 1px),linear-gradient(90deg,#a6b9d22e 1px,transparent 1px);background-size:22px 22px;'
        : scene.id === 'aurora'
          ? 'background-image:linear-gradient(155deg,#e4f4f6,#e9e5f7 48%,#e8effa);'
          : 'background-image:radial-gradient(#b8ae9640 .7px,transparent .7px);background-size:5px 5px;'
      return `<section style="box-sizing:border-box;width:100%;max-width:${measure}px;margin:0 auto;padding:18px 14px;background-color:${background.color};${texture}${typography}"><section style="box-sizing:border-box;padding:30px 20px 24px;background-color:${scene.id === 'journal' ? '#fffdf8' : '#ffffff'};border-radius:10px;box-shadow:0 3px 18px #233c5b0d;">${doc.body.innerHTML}</section></section>`
    }
    // Inline styles travel with the article through rich-text copy and HTML export.
    return `<section style="box-sizing:border-box;width:100%;max-width:${measure}px;margin:0 auto;padding:14px;background-color:${background.color};${typography}"><section style="box-sizing:border-box;width:100%;padding:32px 20px 24px;background-color:#ffffff;border:1px solid #e4e7f0;border-radius:8px;">${doc.body.innerHTML}</section></section>`
  }
  return `<section style="box-sizing:border-box;width:100%;max-width:${measure}px;margin:auto;${typography}${paperStyle}">${doc.body.innerHTML}</section>`
}

export function layoutDocument(title: string, html: string): string {
  const node = document.createElement('span'); node.textContent = title
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${node.innerHTML}</title></head><body style="margin:0;background:#fff">${html}</body></html>`
}
