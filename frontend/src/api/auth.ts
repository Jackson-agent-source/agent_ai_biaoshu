/**
 * 认证相关接口。
 * 对应后端 /auth/... 一组接口，使用 DRF Token 认证。
 */

import { api } from './http'

import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/api'

/** 登录，成功后拿到 token 和用户信息 */
export function login(payload: LoginRequest): Promise<LoginResponse> {
  return api.post<LoginResponse>('/auth/login', payload)
}

/** 注册（后端会直接返回 token，等同于登录） */
export function register(payload: RegisterRequest): Promise<LoginResponse> {
  return api.post<LoginResponse>('/auth/register', payload)
}

/** 退出登录 */
export function logout(): Promise<null> {
  return api.post<null>('/auth/logout')
}

/** 获取当前登录用户信息（用于刷新页面后恢复登录态） */
export function fetchMe(): Promise<User> {
  return api.get<User>('/auth/me')
}
