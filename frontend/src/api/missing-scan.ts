/** 缺片核对接口：批次缺片批量检查、领取、到片上报、确认采纳与回执查询。 */
import { request } from './client'

export type ArrivalCandidate = {
  source: string
  byte_size: number
  digest: string
  range_start: number
  range_end: number
  qualified: boolean
  reported_at: string
  provenance?: string
}

export type MissingItem = {
  seq: number
  status: string
  order_id: string | null
  candidate_count: number
  candidates: ArrivalCandidate[]
  receipt_id: string | null
}

export type BatchCheckResult = {
  batch_id: string
  expected: number[]
  check_count: number
  arrived: number[]
  missing: number[]
  items: MissingItem[]
}

export type AdoptionReceipt = {
  receipt_id: string
  batch_id: string
  seq: number
  task_id: string | null
  order_id: string | null
  adopted: ArrivalCandidate
  adopted_bytes: number
  others: ArrivalCandidate[]
  confirmed_at: string
}

export type CheckResponse = { ok: boolean; message: string; result: BatchCheckResult | null }
export type ItemResponse = { ok: boolean; message: string; item: MissingItem | null }
export type ConfirmResponse = {
  ok: boolean
  message: string
  receipt: AdoptionReceipt | null
  created: boolean
}
export type ReceiptResponse = { ok: boolean; message: string; receipt: AdoptionReceipt | null }

export async function checkBatch(
  batchId: string,
  expected: number[] | null,
  arrived: number[],
): Promise<CheckResponse> {
  const response = await request('/api/missing-scan/check', {
    method: 'POST',
    body: JSON.stringify({ batch_id: batchId, expected, arrived }),
  })
  return await response.json()
}

export async function loadBatch(batchId: string): Promise<CheckResponse> {
  const response = await request(`/api/missing-scan/batches/${encodeURIComponent(batchId)}`)
  return await response.json()
}

export async function claimItem(batchId: string, seq: number): Promise<ItemResponse> {
  const response = await request(
    `/api/missing-scan/batches/${encodeURIComponent(batchId)}/items/${seq}/claim`,
    { method: 'POST', body: JSON.stringify({}) },
  )
  return await response.json()
}

export type ArrivalPayload = {
  source: string
  byte_size: number
  digest: string
  range_start: number
  range_end: number
  qualified: boolean
}

export async function reportArrival(
  batchId: string,
  seq: number,
  payload: ArrivalPayload,
): Promise<ItemResponse> {
  const response = await request(
    `/api/missing-scan/batches/${encodeURIComponent(batchId)}/items/${seq}/arrivals`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  return await response.json()
}

export async function confirmAdoption(
  batchId: string,
  seq: number,
  adoptSource: string | null,
  taskId: string | null,
): Promise<ConfirmResponse> {
  const response = await request(
    `/api/missing-scan/batches/${encodeURIComponent(batchId)}/items/${seq}/confirm`,
    { method: 'POST', body: JSON.stringify({ adopt_source: adoptSource, task_id: taskId }) },
  )
  return await response.json()
}

export async function loadReceipt(batchId: string, seq: number): Promise<ReceiptResponse> {
  const response = await request(
    `/api/missing-scan/batches/${encodeURIComponent(batchId)}/items/${seq}/receipt`,
  )
  return await response.json()
}

export async function loadReceiptByTask(taskId: string): Promise<ReceiptResponse> {
  const response = await request(
    `/api/missing-scan/receipts/by-task/${encodeURIComponent(taskId)}`,
  )
  return await response.json()
}
