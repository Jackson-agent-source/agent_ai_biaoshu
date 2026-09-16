/**
 * 把富文本（text/html）写进剪贴板，供「复制到公众号」用。
 *
 * ★★ 为什么两条路都必须留着：★★
 *
 *   navigator.clipboard 是**安全上下文（secure context）专属**的 API。
 *   https 和 localhost 算安全上下文，http://<公网IP> 不算。
 *
 *   ★ 最坑的是本地开发跑在 localhost:5173 上（算安全上下文），
 *     navigator.clipboard 一调就通 —— 很容易得出"我这儿是好的"，
 *     然后线上翻车。所以两条路都必须实现，而且必须都测。
 *
 *   ★ 域名 + HTTPS 上线之后，线上也变成安全上下文了：
 *     主路径（navigator.clipboard）会**真的被走到**，execCommand 退化成兜底。
 *     这是一次**真实的行为切换**，不是"注释过时了"而已 ——
 *     上线后必须重新点一次按钮实测（见 README 12.10 的验证清单）。
 *
 *   非安全上下文下唯一能用的办法是老的 document.execCommand('copy')，
 *   它不需要任何权限，也不要求安全上下文。它同时也是主路径失败时的兜底：
 *   navigator.clipboard.write 要求文档处于聚焦状态、且要消耗一次用户手势 ——
 *   而我们是先 await 一次取正文的 HTTP 请求再复制的，
 *   手势可能在请求返回前就过期了（Safari 尤其严）。
 */

/**
 * 屏幕外容器。
 *
 * ★ 不要改成 display:none —— execCommand('copy') 复制的是**当前选区**，
 *   而选区依赖渲染（layout）。display:none 或者游离（detached）的节点
 *   都取不到内容，复制出来是**空的**，而且不报任何错。
 *   所以这里是"固定定位到屏幕外"，不是"隐藏"。
 */
function createOffscreenHost(): HTMLDivElement {
  const host = document.createElement('div')
  host.contentEditable = 'true'
  host.setAttribute('aria-hidden', 'true')
  host.tabIndex = -1
  host.style.position = 'fixed'
  host.style.left = '-99999px'
  host.style.top = '0'
  host.style.width = '1px'
  host.style.height = '1px'
  host.style.overflow = 'hidden'
  host.style.opacity = '0'
  // 防止页面上有全局 CSS（例如 * { user-select: none }）把选区弄没
  host.style.userSelect = 'text'
  // ★ 挂在 body 上而不是 #app 里：Vue 的补丁不该有机会碰它
  document.body.appendChild(host)
  return host
}

/** 把整个节点的内容设为选区 */
function selectContents(node: HTMLElement): void {
  const range = document.createRange()
  range.selectNodeContents(node)
  const selection = window.getSelection()
  if (!selection) {
    return
  }
  selection.removeAllRanges()
  selection.addRange(range)
}

/** 老路子：离屏节点 + 选区 + execCommand。非安全上下文下唯一能用的办法，也是主路径失败时的兜底 */
function copyViaExecCommand(html: string): boolean {
  const host = createOffscreenHost()
  try {
    host.innerHTML = html
    selectContents(host)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    // ★ 必须放 finally：execCommand 抛异常时不能把一个 contenteditable
    //   留在 DOM 里（它会跟着页面走，还可能抢走焦点）。
    window.getSelection()?.removeAllRanges()
    host.remove()
  }
}

/**
 * 复制一段 HTML 到剪贴板。返回是否成功。
 *
 * ★ 空内容直接返回 false，**绝不往剪贴板里写空串** ——
 *   那会把用户原来复制的东西抹掉，比"复制失败"更让人恼火。
 *   调用方也要在复制前判一次空（后端对空正文返回的就是空 html）。
 */
export async function copyRichText(html: string): Promise<boolean> {
  if (!html.trim()) {
    return false
  }

  // text/plain 那一份从同一份 HTML 的文本内容里取。
  // ★ 必须挂进 body 才有 innerText —— 游离节点的 innerText 拿不到块级换行，
  //   粘到纯文本框里会变成挤成一坨的一整段。
  const host = createOffscreenHost()
  let plain = ''
  try {
    host.innerHTML = html
    plain = host.innerText || ''
  } finally {
    host.remove()
  }

  // 1) 优先走标准 API（本地开发、以及上 HTTPS 之后的线上）
  if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([plain], { type: 'text/plain' }),
        }),
      ])
      return true
    } catch {
      // Firefox 早期版本不支持写 text/html、权限被拒、文档失焦、
      // 用户手势在 await 取正文期间过期…… 一律往下落到 execCommand，
      // 不要因为主路径失败就直接判定"复制失败"
    }
  }

  // 2) 兜底：非安全上下文下这是唯一能用的路（http://<公网IP> 的部署）
  return copyViaExecCommand(html)
}
