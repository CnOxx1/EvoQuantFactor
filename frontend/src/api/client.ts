import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 60000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('api_token') || import.meta.env.VITE_API_TOKEN || ''
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export default api

export type JobSummary = {
  job_id: string
  report_id?: string
  batch_id?: string
  status: string
  created_at: string
  updated_at: string
  progress: { phase: string; round: number; message: string; percent: number }
  error?: string
  rounds_used: number
  saved_count: number
  dropped_count: number
  title?: string
}

export type BatchSummary = {
  batch_id: string
  title?: string
  status: string
  total: number
  counts: Record<string, number>
  created_at: string
  updated_at: string
  jobs: JobSummary[]
  percent: number
  message: string
}

export type FactorFormula = {
  factor_id: string
  name_zh: string
  name_en?: string
  category?: string
  formula_or_rule: string
  final_score?: number
  median_score?: number
  status: string
}

export type StepSummary = {
  step_id: string
  seq: number
  step_type: string
  title: string
  round: number
  role_code?: string
  status: string
  created_at: string
  summary: string
  factor_ids?: string[]
  role_name?: string
  factor_count?: number
  payload?: Record<string, unknown>
}

export type StepDetail = StepSummary & {
  payload: Record<string, unknown>
}

export const healthApi = () => api.get('/health')
export const metaApi = () => api.get('/api/v1/meta')
export const listJobs = (limit = 50) => api.get<JobSummary[]>('/api/v1/jobs', { params: { limit } })
export const getJob = (id: string) => api.get<JobSummary>(`/api/v1/jobs/${id}`)
export const getFactors = (id: string) => api.get<FactorFormula[]>(`/api/v1/jobs/${id}/factors`)
export const getSteps = (id: string) => api.get<StepDetail[]>(`/api/v1/jobs/${id}/steps`)
export const getStepDetail = (jobId: string, stepId: string) =>
  api.get<StepDetail>(`/api/v1/jobs/${jobId}/steps/${stepId}`)
export const createJobText = (payload: { title?: string; content: string; max_round?: number }) =>
  api.post<JobSummary>('/api/v1/jobs', payload)
export const createJobFromUpload = (file: File, opts?: { title?: string; max_round?: number }) => {
  const form = new FormData()
  form.append('file', file)
  if (opts?.title) form.append('title', opts.title)
  if (opts?.max_round != null) form.append('max_round', String(opts.max_round))
  return api.post<JobSummary>('/api/v1/jobs/from-upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}
export const cancelJob = (id: string) => api.post<JobSummary>(`/api/v1/jobs/${id}/cancel`)
export const rerunJob = (id: string) => api.post<JobSummary>(`/api/v1/jobs/${id}/rerun`)

export type SeedFactorIn = {
  factor_id: string
  name_zh: string
  name_en?: string
  category?: string
  formula_or_rule: string
  inputs?: string[]
  economic_logic?: string
  signal_direction?: string
  source?: string
  frequency?: string
}

export const createJobEvaluate = (payload: {
  factors: SeedFactorIn[]
  title?: string
  max_round?: number
}) =>
  api.post<JobSummary>('/api/v1/jobs', {
    mode: 'evaluate',
    factors: payload.factors,
    title: payload.title,
    max_round: payload.max_round,
  })

export const listBatches = (limit = 50) => api.get<BatchSummary[]>('/api/v1/batches', { params: { limit } })
export const getBatch = (id: string) => api.get<BatchSummary>(`/api/v1/batches/${id}`)
export const createBatch = (payload: unknown) => api.post<BatchSummary>('/api/v1/batches', payload)
export const cancelBatch = (id: string) => api.post<BatchSummary>(`/api/v1/batches/${id}/cancel`)

export const getLlmConfig = () => api.get('/api/v1/llm/config')
export const putLlmConfig = (payload: unknown) => api.put('/api/v1/llm/config', payload)
export const testLlm = (payload?: unknown) =>
  api.post('/api/v1/llm/test', payload ?? {}, { timeout: 600000 })

export const listPrompts = () => api.get('/api/v1/prompts')
export const getPrompt = (key: string) => api.get(`/api/v1/prompts/${key}`)
export const putPrompt = (key: string, payload: unknown) => api.put(`/api/v1/prompts/${key}`, payload)
export const resetPrompt = (key: string) => api.post(`/api/v1/prompts/${key}/reset`)

export type LibraryPack = {
  id: string
  name: string
  version?: string
  count: number
  paper?: string
  description?: string
}

export type LibraryFactor = {
  factor_id: string
  name_zh: string
  name_en?: string
  category?: string
  source?: string
  formula_or_rule: string
  inputs: string[]
  status: string
  tags: string[]
}

export type LibraryFactorsResponse = {
  pack_id: string
  name?: string
  version?: string
  paper?: string
  total: number
  offset: number
  limit: number
  factors: LibraryFactor[]
}

export const listLibraryPacks = () => api.get<LibraryPack[]>('/api/v1/factor-library/packs')
export const getLibraryFactors = (
  packId: string,
  params?: { q?: string; limit?: number; offset?: number },
) => api.get<LibraryFactorsResponse>(`/api/v1/factor-library/${packId}/factors`, { params })
