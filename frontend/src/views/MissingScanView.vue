<template>
  <section class="page" data-module="missing-scan">
    <header class="page-head">
      <div>
        <h2>缺片核对</h2>
        <p class="page-desc">
          按批次批量检查片号并逐条关联补采单，批次独立核对已到片；多份到片冲突时确认采纳，
          采纳回执供转写任务查询实际所用字节与落选录音出处。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/fail_redeliver">到重投页核对结算</RouterLink>
        <RouterLink class="btn" to="/transcribe_task">到转写任务页</RouterLink>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前批次</span>
        <strong class="stat-value">{{ result?.batch_id || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">应收片数</span>
        <strong class="stat-value">{{ result?.expected.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">仍缺片数</span>
        <strong class="stat-value">{{ result?.missing.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">检查次数</span>
        <strong class="stat-value">{{ result?.check_count ?? 0 }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="runCheck">
      <label class="filter-item">
        <span>批次号</span>
        <input v-model="batchId" data-testid="batch-id" placeholder="如 B" />
      </label>
      <label class="filter-item">
        <span>应收片号</span>
        <input v-model="expectedText" data-testid="expected-seqs" placeholder="如 1-5 或 1,2,3" />
      </label>
      <label class="filter-item">
        <span>本次到片</span>
        <input v-model="arrivedText" data-testid="arrived-seqs" placeholder="如 2,4（可空）" />
      </label>
      <button class="btn primary" data-testid="run-check" type="submit" :disabled="checking">
        {{ checking ? '检查中…' : '批量检查' }}
      </button>
      <button class="btn ghost" data-testid="reload-batch" type="button" @click="reloadBatch">
        刷新整组结果
      </button>
    </form>

    <p v-if="notice" class="notice-text" data-testid="notice">{{ notice }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>片号</th>
          <th>状态</th>
          <th>补采单</th>
          <th>候选到片</th>
          <th>采纳回执</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in result?.items ?? []" :key="item.seq" :data-testid="`item-${item.seq}`">
          <td>{{ item.seq }}</td>
          <td>{{ item.status }}</td>
          <td>{{ item.order_id ?? '—' }}</td>
          <td>{{ item.candidate_count }}</td>
          <td>{{ item.receipt_id ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-if="item.status === '待领取'"
              class="link"
              type="button"
              :data-testid="`claim-${item.seq}`"
              @click="claim(item.seq)"
            >领取</button>
            <button
              v-if="isUnsettled(item)"
              class="link"
              type="button"
              :data-testid="`arrive-${item.seq}`"
              @click="openArrival(item.seq)"
            >上报到片</button>
            <button
              v-if="isUnsettled(item) && item.candidate_count > 0"
              class="link"
              type="button"
              :data-testid="`confirm-${item.seq}`"
              @click="openConfirm(item.seq)"
            >确认采纳</button>
            <button
              v-if="item.receipt_id"
              class="link"
              type="button"
              :data-testid="`receipt-${item.seq}`"
              @click="showReceipt(item.seq)"
            >看回执</button>
          </td>
        </tr>
        <tr v-if="!result || !result.items.length">
          <td colspan="6" class="empty-state">先做批量检查，缺片条目会按批次逐条列出</td>
        </tr>
      </tbody>
    </table>

    <section class="receipt-panel">
      <h3>采纳分片回执</h3>
      <form class="filter-bar" @submit.prevent="queryByTask">
        <label class="filter-item">
          <span>转写任务编号</span>
          <input v-model="receiptTaskId" data-testid="receipt-task-id" placeholder="如 TRCB-0001" />
        </label>
        <button class="btn" data-testid="query-receipt" type="submit">从转写任务查</button>
      </form>
      <table v-if="receipt" class="data-table" data-testid="receipt-table">
        <tbody>
          <tr><th>回执编号</th><td>{{ receipt.receipt_id }}</td></tr>
          <tr><th>批次 / 片号</th><td>{{ receipt.batch_id }} / {{ receipt.seq }}</td></tr>
          <tr><th>关联转写任务</th><td>{{ receipt.task_id ?? '—' }}</td></tr>
          <tr><th>结算补采单</th><td>{{ receipt.order_id ?? '—' }}</td></tr>
          <tr><th>采纳来源</th><td>{{ receipt.adopted.source }}</td></tr>
          <tr><th>实际所用字节</th><td>{{ receipt.adopted_bytes }}</td></tr>
          <tr><th>采纳摘要</th><td>{{ receipt.adopted.digest || '—' }}</td></tr>
          <tr>
            <th>采纳区间</th>
            <td>{{ receipt.adopted.range_start }}s - {{ receipt.adopted.range_end }}s</td>
          </tr>
          <tr>
            <th>落选到片出处</th>
            <td>
              <span v-if="!receipt.others.length">—</span>
              <span v-for="(other, idx) in receipt.others" :key="idx" class="legend-item">
                {{ other.provenance ?? other.source }}（摘要 {{ other.digest || '—' }}，
                {{ other.byte_size }} 字节）
              </span>
            </td>
          </tr>
          <tr><th>确认时间</th><td>{{ receipt.confirmed_at }}</td></tr>
        </tbody>
      </table>
      <p v-else class="empty-state">暂无回执，确认采纳后可在此或用转写任务编号查询</p>
    </section>

    <div v-if="arrivalFor !== null" class="modal-backdrop" data-testid="arrival-modal">
      <form class="modal" @submit.prevent="submitArrival">
        <h3>上报到片 · 片号 {{ arrivalFor }}</h3>
        <label class="create-field">
          <span>到片来源</span>
          <select v-model="arrivalForm.source" data-testid="arrival-source">
            <option value="原入口补报">原入口补报</option>
            <option value="补采回传">补采回传</option>
            <option value="正常到片">正常到片</option>
          </select>
        </label>
        <label class="create-field">
          <span>字节数</span>
          <input v-model="arrivalForm.byte_size" data-testid="arrival-bytes" type="number" min="0" required />
        </label>
        <label class="create-field">
          <span>摘要</span>
          <input v-model="arrivalForm.digest" data-testid="arrival-digest" placeholder="内容摘要/校验和" required />
        </label>
        <label class="create-field">
          <span>区间起（秒）</span>
          <input v-model="arrivalForm.range_start" data-testid="arrival-range-start" type="number" min="0" step="0.001" required />
        </label>
        <label class="create-field">
          <span>区间止（秒）</span>
          <input v-model="arrivalForm.range_end" data-testid="arrival-range-end" type="number" min="0" step="0.001" required />
        </label>
        <label class="create-field">
          <span>是否合格</span>
          <select v-model="arrivalForm.qualified" data-testid="arrival-qualified">
            <option :value="true">合格</option>
            <option :value="false">不合格</option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="arrivalFor = null">取消</button>
          <button class="btn primary" data-testid="submit-arrival" type="submit">登记到片</button>
        </div>
      </form>
    </div>

    <div v-if="confirmFor !== null" class="modal-backdrop" data-testid="confirm-modal">
      <form class="modal" @submit.prevent="submitConfirm">
        <h3>确认采纳 · 片号 {{ confirmFor }}</h3>
        <label class="create-field">
          <span>采纳哪一份</span>
          <select v-model="confirmForm.adopt_source" data-testid="confirm-source">
            <option value="">自动（优先合格原片）</option>
            <option v-for="source of candidateSources" :key="source" :value="source">
              {{ source }}
            </option>
          </select>
        </label>
        <table class="data-table">
          <thead>
            <tr><th>来源</th><th>摘要</th><th>字节</th><th>区间</th><th>合格</th></tr>
          </thead>
          <tbody>
            <tr v-for="(c, idx) in confirmCandidates" :key="idx">
              <td>{{ c.source }}</td>
              <td>{{ c.digest || '—' }}</td>
              <td>{{ c.byte_size }}</td>
              <td>{{ c.range_start }}s - {{ c.range_end }}s</td>
              <td>{{ c.qualified ? '合格' : '不合格' }}</td>
            </tr>
          </tbody>
        </table>
        <label class="create-field">
          <span>关联转写任务编号</span>
          <input v-model="confirmForm.task_id" data-testid="confirm-task-id" placeholder="如 TRCB-0001" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="confirmFor = null">取消</button>
          <button class="btn primary" data-testid="submit-confirm" type="submit">确认采纳</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span v-if="result">仍缺片号：{{ result.missing.join('、') || '无' }}</span>
      <span v-else>尚未选择批次</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  checkBatch,
  claimItem,
  confirmAdoption,
  loadBatch,
  loadReceipt,
  loadReceiptByTask,
  reportArrival,
} from '@/api/missing-scan'
import type {
  AdoptionReceipt,
  ArrivalCandidate,
  BatchCheckResult,
  MissingItem,
} from '@/api/missing-scan'

const batchId = ref('')
const expectedText = ref('')
const arrivedText = ref('')
const result = ref<BatchCheckResult | null>(null)
const notice = ref('')
const errorMessage = ref('')
const checking = ref(false)

const receipt = ref<AdoptionReceipt | null>(null)
const receiptTaskId = ref('')

const arrivalFor = ref<number | null>(null)
const arrivalForm = ref({
  source: '原入口补报',
  byte_size: 0,
  digest: '',
  range_start: 0,
  range_end: 0,
  qualified: true as boolean,
})

const confirmFor = ref<number | null>(null)
const confirmForm = ref({ adopt_source: '', task_id: '' })

const UNSETTLED = ['待领取', '补采中', '待确认']

const confirmCandidates = computed<ArrivalCandidate[]>(() => {
  if (confirmFor.value === null || !result.value) return []
  const item = result.value.items.find((entry) => entry.seq === confirmFor.value)
  return item?.candidates ?? []
})

const candidateSources = computed(() => [
  ...new Set(confirmCandidates.value.map((c) => c.source)),
])

function isUnsettled(item: MissingItem): boolean {
  return UNSETTLED.includes(item.status)
}

function parseSeqs(text: string): number[] {
  const seqs = new Set<number>()
  for (const part of text.split(/[,，\s]+/).filter(Boolean)) {
    const range = part.match(/^(\d+)\s*[-~]\s*(\d+)$/)
    if (range) {
      const [start, end] = [Number(range[1]), Number(range[2])]
      for (let s = Math.min(start, end); s <= Math.max(start, end); s += 1) seqs.add(s)
    } else if (/^\d+$/.test(part)) {
      seqs.add(Number(part))
    }
  }
  return [...seqs].sort((a, b) => a - b)
}

function showError(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
  notice.value = ''
}

async function runCheck() {
  errorMessage.value = ''
  notice.value = ''
  if (!batchId.value.trim()) {
    errorMessage.value = '请先填写批次号'
    return
  }
  checking.value = true
  try {
    const payload = await checkBatch(
      batchId.value.trim(),
      expectedText.value.trim() ? parseSeqs(expectedText.value) : null,
      parseSeqs(arrivedText.value),
    )
    if (!payload.ok) throw new Error(payload.message)
    result.value = payload.result
    notice.value = payload.message
  } catch (error) {
    showError(error, '批量检查失败')
  } finally {
    checking.value = false
  }
}

async function reloadBatch() {
  errorMessage.value = ''
  if (!batchId.value.trim()) {
    errorMessage.value = '请先填写批次号'
    return
  }
  try {
    const payload = await loadBatch(batchId.value.trim())
    if (!payload.ok) throw new Error(payload.message)
    result.value = payload.result
    notice.value = '整组检查结果已刷新'
  } catch (error) {
    showError(error, '整组结果读取失败')
  }
}

async function claim(seq: number) {
  errorMessage.value = ''
  try {
    const payload = await claimItem(batchId.value.trim(), seq)
    if (!payload.ok) throw new Error(payload.message)
    notice.value = payload.message
    await reloadBatch()
  } catch (error) {
    showError(error, '领取失败')
  }
}

function openArrival(seq: number) {
  arrivalForm.value = {
    source: '原入口补报', byte_size: 0, digest: '',
    range_start: 0, range_end: 0, qualified: true,
  }
  errorMessage.value = ''
  arrivalFor.value = seq
}

async function submitArrival() {
  if (arrivalFor.value === null) return
  errorMessage.value = ''
  try {
    const payload = await reportArrival(batchId.value.trim(), arrivalFor.value, {
      source: arrivalForm.value.source,
      byte_size: Number(arrivalForm.value.byte_size),
      digest: arrivalForm.value.digest.trim(),
      range_start: Number(arrivalForm.value.range_start),
      range_end: Number(arrivalForm.value.range_end),
      qualified: arrivalForm.value.qualified,
    })
    if (!payload.ok) throw new Error(payload.message)
    notice.value = payload.message
    arrivalFor.value = null
    await reloadBatch()
  } catch (error) {
    showError(error, '到片登记失败')
  }
}

function openConfirm(seq: number) {
  confirmForm.value = { adopt_source: '', task_id: '' }
  errorMessage.value = ''
  confirmFor.value = seq
}

async function submitConfirm() {
  if (confirmFor.value === null) return
  errorMessage.value = ''
  try {
    const payload = await confirmAdoption(
      batchId.value.trim(),
      confirmFor.value,
      confirmForm.value.adopt_source || null,
      confirmForm.value.task_id.trim() || null,
    )
    if (!payload.ok) throw new Error(payload.message)
    notice.value = payload.message
    receipt.value = payload.receipt
    confirmFor.value = null
    await reloadBatch()
  } catch (error) {
    showError(error, '确认采纳失败')
  }
}

async function showReceipt(seq: number) {
  errorMessage.value = ''
  try {
    const payload = await loadReceipt(batchId.value.trim(), seq)
    if (!payload.ok) throw new Error(payload.message)
    receipt.value = payload.receipt
  } catch (error) {
    showError(error, '回执读取失败')
  }
}

async function queryByTask() {
  errorMessage.value = ''
  if (!receiptTaskId.value.trim()) {
    errorMessage.value = '请填写转写任务编号'
    return
  }
  try {
    const payload = await loadReceiptByTask(receiptTaskId.value.trim())
    if (!payload.ok) throw new Error(payload.message)
    receipt.value = payload.receipt
    notice.value = `已查到转写任务 ${receiptTaskId.value.trim()} 的采纳回执`
  } catch (error) {
    showError(error, '回执查询失败')
  }
}
</script>

<style scoped>
.receipt-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.receipt-panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
}
.notice-text {
  color: #15803d;
  font-size: 13px;
}
.legend-item {
  display: inline-block;
  margin-right: 10px;
}
.create-field select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  background: #fff;
}
</style>
