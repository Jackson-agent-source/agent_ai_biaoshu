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
