import { api } from './http'
import type { TopicCandidate, TrendOverview, TrendPreferences } from '@/types/trends'

export function restoreTopic(id: string): Promise<TopicCandidate> {
  return api.post<TopicCandidate>(`/trends/topics/${id}/restore`, {})
}

export function getTrendOverview(): Promise<TrendOverview> {
  return api.get<TrendOverview>('/trends/overview')
}

export function refreshTrends(): Promise<TrendOverview> {
  return api.post<TrendOverview>('/trends/refresh', {}, { timeout: 300000 })
}

export function saveTrendPreferences(value: TrendPreferences): Promise<TrendPreferences> {
  return api.put<TrendPreferences>('/trends/preferences', value)
}

export function generateFromTopic(id: string): Promise<{ article_id: string; candidate_id: string }> {
  return api.post<{ article_id: string; candidate_id: string }>(`/trends/topics/${id}/generate`, {}, { timeout: 300000 })
}
