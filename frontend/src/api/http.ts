/**
 * axios 实例 + 统一错误处理。
 *
 * 这个文件解决两件事：
 * 1. 自动往请求头里塞 Token（DRF Token 认证）。
 * 2. 自动把后端的「响应信封」拆开：
 *      - 成功时，调用方直接拿到 data，不用写 res.data.data；
 *      - 失败时，抛出 ApiError（带中文提示），页面里 catch 一下就行。
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
} from 'axios'

import type { ApiEnvelope, ApiErrorBody } from '@/types/api'

/** localStorage 里保存 token 用的 key */
export const TOKEN_KEY = 'ai_content_agent_token'

/**
 * 默认的后端地址；可以在 .env 里用 VITE_API_BASE_URL 覆盖。
 *
 * ★ 生产用的是**相对路径** /api/v1，不是 http://localhost:8000。
 *
 * 部署时前端静态文件和 API 由同一个 nginx 提供（同源），
 * 浏览器根本不认为这是跨域 —— CORS、Cookie、预检请求的坑一次性全没了，
 * 换域名也不用重新构建前端。
 *
 * 绝对不能在这里写死 http://localhost:8000：
 * 那样每个访客打开网页后，请求都会发到**他自己的电脑**上。
 * 页面能正常打开（静态文件是好的），但所有接口都失败，
 * 而且报错完全指不出原因 —— 是最难查的一类部署问题。
 *
 * 本地开发不受影响：frontend/.env 里的 VITE_API_BASE_URL
 * （http://localhost:8000/api/v1）会覆盖掉这个默认值。
 */
const DEFAULT_BASE_URL = '/api/v1'

/**
 * 统一的错误对象。
 * 页面里可以这样用：
 *   catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '未知错误') }
 */
export class ApiError extends Error {
  /** 后端返回的业务错误码，例如 UNAUTHORIZED；网络层错误则是 HTTP_xxx / NETWORK_ERROR */
  readonly code: string
  /** 后端返回的 request_id，排查问题时把它给后端同学看 */
  readonly requestId: string
  /** HTTP 状态码，网络不通时为 null */
  readonly httpStatus: number | null

  constructor(code: string, message: string, requestId = '', httpStatus: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.message = message
    this.requestId = requestId
    this.httpStatus = httpStatus
  }
}

/** 读取本地保存的 token（没有就返回空字符串） */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

/** 保存 token 到本地 */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

/** 清除本地 token */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/**
 * 401 时统一跳回登录页。
 * 这里用 window.location 而不是 router，是为了避免
 * http.ts -> router -> store -> http.ts 的循环依赖。
 */
function redirectToLogin(): void {
  clearToken()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

/** 把后端返回的 error 字段转成 ApiError */
function toApiError(
  body: ApiErrorBody | undefined,
  httpStatus: number | null,
  fallbackMessage: string,
): ApiError {
  if (body && typeof body.message === 'string') {
    return new ApiError(body.code || 'UNKNOWN', body.message, body.request_id ?? '', httpStatus)
  }
  return new ApiError('UNKNOWN', fallbackMessage, '', httpStatus)
}

/** 把 axios 抛出的各种异常，翻译成中文的 ApiError */
function normalizeError(error: AxiosError<unknown>): ApiError {
  // 1. 请求超时
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return new ApiError('TIMEOUT', '请求超时，请稍后重试（Agent 生成较慢，请耐心等待）', '', null)
  }

  // 2. 压根没连上后端（后端没启动、跨域被拦等）
  if (!error.response) {
    return new ApiError('NETWORK_ERROR', '无法连接后端服务，请确认后端已启动、地址配置正确', '', null)
  }

  const status = error.response.status
  const data = error.response.data

  // 3. 后端返回了标准的失败信封
  if (data && typeof data === 'object' && 'error' in data) {
    const envelope = data as { error?: ApiErrorBody }
    if (status === 401) {
      redirectToLogin()
      return toApiError(envelope.error, status, '登录已过期，请重新登录')
    }
    return toApiError(envelope.error, status, `请求失败（HTTP ${status}）`)
  }

  // 4. 后端没有按约定返回（比如 Django 的调试页面），按状态码给中文提示
  if (status === 401) {
    redirectToLogin()
    return new ApiError('UNAUTHORIZED', '登录已过期，请重新登录', '', status)
  }
  if (status === 403) {
    return new ApiError('FORBIDDEN', '没有权限执行该操作', '', status)
  }
  if (status === 404) {
    return new ApiError('NOT_FOUND', '请求的资源不存在', '', status)
  }
  if (status === 400) {
    return new ApiError('BAD_REQUEST', '请求参数有误，请检查后重试', '', status)
  }
  if (status >= 500) {
    return new ApiError('SERVER_ERROR', '服务器内部错误，请稍后重试或联系管理员', '', status)
  }
  return new ApiError('HTTP_ERROR', `请求失败（HTTP ${status}）`, '', status)
}

/** 创建 axios 实例 */
export const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE_URL,
  // 普通接口 30 秒足够；Agent 生成那个接口会单独设置更长的 timeout
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

/**
 * 上传文件（multipart）时必须带上的请求头覆盖。
 *
 * ★★★ 不加这个，上传会**静默失败**，而且看起来完全不像 Content-Type 的问题：
 *
 *   上面那个 `'Content-Type': 'application/json'` 是实例级默认值，
 *   axios 的 transformRequest 见到 json 就会把请求体 JSON.stringify 一遍
 *   （见 axios 的 `hasJSONContentType ? JSON.stringify(formDataToJSON(data)) : data`）。
 *   于是 FormData 被序列化成 `{"caption":"x","file":{}}` ——
 *   **文件本身一个字节都没发出去**，后端只会说"没有收到图片文件"。
 *   实测过，就是上面这个结果。
 *
 * ★ 那写成 'multipart/form-data' 呢？**在 Chrome 上能work**，但这个写法
 *   建立在一个"浏览器会覆盖你手写的头"的行为上，不值得赌：
 *   boundary（各个部分的随机分隔符）是**浏览器自己生成**的，
 *   手写一个 multipart/form-data 等于抢在浏览器前面把这个头定死了，
 *   而 axios 交给浏览器时那个头的值里**是没有 boundary 的**
 *   （实测 axios 侧解析出来的就是 `multipart/form-data` 这九个字）。
 *   现代 Chrome 会认出"这是个 FormData，我重写这个头"并补上 boundary
 *   （实测拿到 `multipart/form-data; boundary=----WebKitFormBoundary…`），
 *   但这是在依赖某个具体实现的宽容，而不是规范给的保证 ——
 *   老一点的 WebView / 别的引擎上，报错会是一句"服务端解析不出请求"，
 *   和 Content-Type 看起来毫无关系。
 *
 * ★ 所以真正稳妥的做法是**把这个头彻底去掉**（值写 `false`），让浏览器从零自己填。
 *   这条路上没有任何"浏览器要不要覆盖"的余地 ——
 *   请求头里根本没有 Content-Type，浏览器**必须**按规范给 FormData
 *   生成一个带 boundary 的完整 multipart 头，这一点所有引擎一致。
 *   axios 的 AxiosHeaders 对 `false` 有特殊处理 —— 它不只是"值为假"，
 *   而是把这个头**锁住**（`self[key] !== false` 时才会被后续赋值覆盖），
 *   所以实例默认值不会再把它加回来。
 *
 * ★ 三组写法都是在**真的 headless Chrome + 真的 axios 1.20** 上跑过的，
 *   服务端看到的结果分别是：
 *     不覆盖（实例默认 application/json）→ 请求体只有 30 字节的
 *        `{"caption":"…","file":{}}`，**文件一个字节都没发出去**
 *       'multipart/form-data'          → 文件完整，boundary 由浏览器补上
 *       false（本常量）                 → 文件完整，boundary 由浏览器补上
 *   第一种是真正会让人查半天的那个坑：请求成功、状态码 200、
 *   后端只会说一句"没有收到图片文件"。
 *
 * ★ 只有 multipart 请求需要它；普通 JSON 请求照旧走实例默认值。
 */
export const MULTIPART_HEADERS = { 'Content-Type': false } as const

/* ------------------------- 请求拦截器 ------------------------- */

http.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      // DRF Token 认证要求的格式：Authorization: Token xxxxx
      config.headers.set('Authorization', `Token ${token}`)
    }
    return config
  },
  (error: AxiosError) => Promise.reject(normalizeError(error)),
)

/* ------------------------- 响应拦截器 ------------------------- */

http.interceptors.response.use(
  (response: AxiosResponse<ApiEnvelope<unknown>>) => {
    const body = response.data as unknown

    // 后端返回的是标准信封
    if (body && typeof body === 'object' && 'success' in body) {
      const envelope = body as ApiEnvelope<unknown> & { error?: ApiErrorBody }
      if (envelope.success) {
        // 拆开信封，直接把 data 交给调用方。
        // axios 的类型声明要求这里返回 AxiosResponse，所以做一次 unknown 转换。
        return envelope.data as unknown as AxiosResponse
      }
      // HTTP 200 但 success = false（少数后端会这样），同样当失败处理
      return Promise.reject(toApiError(envelope.error, response.status, '请求失败'))
    }

    // 不是信封格式（例如某些第三方接口），原样返回
    return body as unknown as AxiosResponse
  },
  (error: AxiosError<unknown>) => {
    return Promise.reject(normalizeError(error))
  },
)

/* ------------------------------------------------------------------ */
/* 带类型的快捷方法                                                     */
/*                                                                     */
/* 因为拦截器已经把信封拆掉了，http.get() 实际返回的就是 data，         */
/* 但 axios 自己的类型还以为是 AxiosResponse。所以这里包一层，          */
/* 让业务代码写起来更自然：const data = await api.get<Article>(url)    */
/* ------------------------------------------------------------------ */

export const api = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return http.get(url, config) as unknown as Promise<T>
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return http.post(url, data, config) as unknown as Promise<T>
  },
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return http.patch(url, data, config) as unknown as Promise<T>
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return http.put(url, data, config) as unknown as Promise<T>
  },
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return http.delete(url, config) as unknown as Promise<T>
  },
}
