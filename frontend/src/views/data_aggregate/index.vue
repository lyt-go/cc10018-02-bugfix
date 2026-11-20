<template>
  <section class="page" data-module="data_aggregate">
    <header class="page-head">
      <div>
        <h2>数据聚合管理</h2>
        <p class="page-desc">维护多维数据聚合的维度定义、函数组合与增量更新配置，为数据聚合提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记聚合配置</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据聚合清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`plan-${row.id}`" @click="openPlan(row)">计划/来源</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据聚合数据，可先登记聚合配置</td>
        </tr>
      </tbody>
    </table>

    <div v-if="panelRow" class="panel" data-testid="plan-panel">
      <header class="panel-head">
        <div>
          <h3>聚合计划与来源追溯 · {{ panelRow['配置编号'] }}</h3>
          <p class="page-desc">
            计划从指标域去重清单取有效映射；输出数据的来源必须与计划使用的映射一致，连接无法确认时不允许执行。
          </p>
        </div>
        <div class="page-actions">
          <button class="btn primary" data-testid="execute-plan" type="button" :disabled="executing" @click="executePanel">
            {{ executing ? '执行中…' : '执行聚合计划' }}
          </button>
          <button class="btn" data-testid="refresh-plan" type="button" @click="loadPlan">刷新</button>
          <button class="btn ghost" type="button" @click="panelRow = null">关闭</button>
        </div>
      </header>

      <p v-if="planError" class="error-text" data-testid="plan-error">{{ planError }}</p>

      <table v-if="plan" class="data-table">
        <thead>
          <tr>
            <th>指标域</th>
            <th>有效映射</th>
            <th>映射来源</th>
            <th>数据源表</th>
            <th>连接编号</th>
            <th>连接状态</th>
            <th>可用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="binding in plan.domain_mappings" :key="binding.domain_code">
            <td>{{ binding.domain_code }}</td>
            <td>{{ binding.mapping_code }}</td>
            <td>{{ binding.mapping_source }}</td>
            <td>{{ binding.source_table }}</td>
            <td>{{ binding.connection_code || '—' }}</td>
            <td>{{ binding.connection_status || '—' }}</td>
            <td>{{ binding.usable ? '可执行' : '不可执行' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="plan" class="page-desc mono" data-testid="plan-fingerprint">来源指纹：{{ plan.source_fingerprint }}</p>
      <ul v-if="plan?.blocking_issues?.length" class="issue-list" data-testid="plan-issues">
        <li v-for="issue in plan.blocking_issues" :key="issue">{{ issue }}</li>
      </ul>

      <h4>结果记录</h4>
      <p v-if="!results.length" class="page-desc">暂无结果记录，执行计划后生成。</p>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>结果编号</th>
            <th>生成时间</th>
            <th>来源指纹</th>
            <th>来源表</th>
            <th>输出行数</th>
            <th>输出交易</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in results" :key="record.结果编号" :data-testid="`result-${record.结果编号}`">
            <td>{{ record.结果编号 }}</td>
            <td>{{ record.生成时间 }}</td>
            <td class="mono">{{ record.计划来源指纹 }}</td>
            <td>{{ (record.来源表 || []).join(', ') }}</td>
            <td>{{ record.输出行数 }}</td>
            <td>{{ tradeNumbers(record) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记聚合配置</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据聚合记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type MappingBinding = {
  domain_code: string
  mapping_code: string
  mapping_source: string
  source_table: string
  connection_code: string
  connection_status: string
  usable: boolean
}

type ComputePlan = {
  ok: boolean
  source_fingerprint: string
  source_tables: string[]
  domain_mappings: MappingBinding[]
  blocking_issues: string[]
}

type AggregateResult = {
  结果编号: string
  生成时间: string
  计划来源指纹: string
  来源表: string[]
  输出行数: number
  输出数据: Record<string, unknown>[]
}

const ENDPOINT = '/api/data_aggregate'
const columns = ["配置编号", "数据来源", "聚合维度", "聚合函数", "过滤条件", "输出表名", "增量键", "更新策略"]
const actions = ["提交校验", "标记生效", "停用配置"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["聚合配置总数", "待校验配置", "已生效配置"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const panelRow = ref<Row | null>(null)
const plan = ref<ComputePlan | null>(null)
const results = ref<AggregateResult[]>([])
const planError = ref('')
const executing = ref(false)

function tradeNumbers(record: AggregateResult): string {
  return (record.输出数据 || [])
    .map((row) => String((row as Record<string, unknown>)['交易单号'] ?? ''))
    .filter(Boolean)
    .join(', ') || '—'
}
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'data_aggregate.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function openPlan(row: Row) {
  panelRow.value = row
  plan.value = null
  results.value = []
  planError.value = ''
  await loadPlan()
}

async function loadPlan() {
  if (!panelRow.value) return
  const id = panelRow.value.id
  planError.value = ''
  try {
    const [planResponse, resultResponse] = await Promise.all([
      request(`${ENDPOINT}/${id}/compute-plan`),
      request(`${ENDPOINT}/${id}/results`),
    ])
    const planPayload = await planResponse.json()
    if (planResponse.ok) {
      plan.value = planPayload
    } else {
      // 计划不可执行时，后端仍会返回阻塞原因，尽量展示出来。
      plan.value = planPayload.detail?.plan ?? null
      planError.value = (planPayload.detail?.blocking_issues ?? [planPayload.detail?.error ?? '计算计划不可执行']).join('；')
    }
    if (resultResponse.ok) {
      const resultPayload = await resultResponse.json()
      results.value = resultPayload.items ?? []
    }
  } catch (error) {
    planError.value = error instanceof Error ? error.message : '计划读取失败'
  }
}

async function executePanel() {
  if (!panelRow.value) return
  executing.value = true
  planError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${panelRow.value.id}/execute`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '执行失败')
    await loadPlan()
  } catch (error) {
    planError.value = error instanceof Error ? error.message : '执行失败'
  } finally {
    executing.value = false
  }
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据聚合动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据聚合操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('聚合配置列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据聚合列表读取失败'
  }
}

onMounted(reload)
</script>
