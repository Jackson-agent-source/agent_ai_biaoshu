/**
 * 与后端约定的「统一响应信封」相关的类型。
 *
 * 后端所有接口都返回下面两种结构之一：
 *   成功：{ "success": true,  "data": <T>, "message": "" }
 *   失败：{ "success": false, "error": { "code": "XXX", "message": "中文提示", "request_id": "..." } }
 *
 * 注意：这些「信封」在 src/api/http.ts 的响应拦截器里就已经被拆开了，
 * 页面里拿到的直接就是 data 本身，不需要再写 res.data.data。
 */

/** 成功响应信封 */
export interface ApiEnvelope<T> {
  success: boolean
  data: T
  message?: string
}

/** 失败响应中的 error 字段 */
export interface ApiErrorBody {
  code: string
  message: string
  request_id?: string
}

/** 失败响应信封 */
export interface ApiErrorEnvelope {
  success: false
  error: ApiErrorBody
}

/** 分页响应的 data 形状 */
export interface Paginated<T> {
  /** 总条数 */
  count: number
  /** 当前页码，从 1 开始 */
  page: number
  /** 每页条数 */
  page_size: number
  /** 总页数 */
  total_pages: number
  /** 当前页的数据列表 */
  results: T[]
}

/** 分页请求的通用查询参数 */
export interface PageQuery {
  page?: number
  page_size?: number
}

/* ------------------------------------------------------------------ */
/* 认证相关类型（放在这里是因为它们同样属于「接口数据结构」）            */
/* ------------------------------------------------------------------ */

/** 登录用户信息 */
export interface User {
  id: number
  username: string
  email: string
  is_staff: boolean
}

/** POST /auth/login 的请求体 */
export interface LoginRequest {
  username: string
  password: string
}

/** POST /auth/register 的请求体，email 可选 */
export interface RegisterRequest {
  username: string
  password: string
  email?: string
}

/** 登录 / 注册成功后返回的数据 */
export interface LoginResponse {
  token: string
  user: User
}
