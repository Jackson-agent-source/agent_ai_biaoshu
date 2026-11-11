export interface TrendItem {
  id: string
  platform: string
  title: string
  category: string
  research_topic: string
  url: string
  region: string
  rank: number
  metric: number | null
  published_at: string | null
  last_seen_at: string
}

export interface TopicCandidate {
  id: string
  topic: string
  category: string
  angle: string
  reason: string
  score: number
  status: 'ready' | 'generating' | 'generated' | 'failed' | 'skipped'
  error_message: string
  article_id: string | null
  trends: TrendItem[]
  created_at: string
}

export interface TrendSourceRun {
  platform: string
  status: 'success' | 'skipped' | 'failed'
  item_count: number
  message: string
  created_at: string
}

export interface TrendOverview {
  candidates: TopicCandidate[]
  sources: TrendSourceRun[]
  preferences: TrendPreferences
  automation: { enabled: boolean; daily_article_limit: number }
  max_focus_terms: number
  tracking: { active: boolean; state: string; interval_seconds?: number; next_run_at?: string | null; last_success_at?: string; message?: string }
  research_presets: { label: string; group: string; description: string; terms: string[] }[]
  categories: TrendCategory[]
  source_catalog: { key: string; label: string }[]
}

export interface TrendPreferences {
  focus_terms: string[]
  auto_categories: string[]
}

export interface TrendCategory {
  key: string
  label: string
}
