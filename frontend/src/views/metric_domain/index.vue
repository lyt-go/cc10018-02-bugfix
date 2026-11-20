<template>
  <section class="page" data-module="metric_domain">
    <header class="page-head">
      <div>
        <h2>指标域管理管理</h2>
        <p class="page-desc">维护业务指标域的注册、数据归属与刷新策略管理，为指标域管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记指标域</button>
        <button class="btn" data-testid="open-dedup" type="button" @click="openDedup">指标域去重清单</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出指标域管理清单</button>
      </div>
    </header>

    <div v-if="showDedup" class="panel" data-testid="dedup-panel">
      <header class="panel-head">
        <div>
          <h3>指标域去重清单</h3>
          <p class="page-desc">
            每个域编号只保留一条可供新计算使用的有效映射；历史映射保留，供既有聚合结果追溯。
          </p>
        </div>
        <div class="page-actions">
          <button class="btn" data-testid="refresh-dedup" type="button" @click="loadDedup">刷新</button>
          <button class="btn primary" data-testid="adjudicate" type="button" :disabled="adjudicating" @click="adjudicate">
            {{ adjudicating ? '裁决中…' : '批量裁决存量重复域' }}
          </button>
          <button class="btn ghost" type="button" @click="showDedup = false">关闭</button>
        </div>
      </header>

      <div v-if="dedup" class="stat-row">
        <article class="stat-card">
          <span class="stat-label">指标域记录数</span>
          <strong class="stat-value">{{ dedup.summary['指标域记录数'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">去重后域编号数</span>
          <strong class="stat-value">{{ dedup.summary['去重后域编号数'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">重复域编号数</span>
          <strong class="stat-value">{{ dedup.summary['重复域编号数'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">冲突域编号数</span>
          <strong class="stat-value">{{ dedup.summary['冲突域编号数'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">新计划可用域数</span>
          <strong class="stat-value">{{ dedup.summary['新计划可用域数'] }}</strong>
        </article>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th>域编号</th>
            <th>域记录数</th>
            <th>重复</th>
            <th>冲突</th>
            <th>有效映射</th>
            <th>有效数据源表</th>
            <th>连接</th>
            <th>新计划可用</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in dedup?.items ?? []"
            :key="item.域编号"
            :data-testid="`dedup-${item.域编号}`"
            :data-usable="String(item.新计划可用)"
          >
            <td>{{ item.域编号 }}</td>
            <td>{{ item.域记录数 }}</td>
            <td>{{ item.重复 ? '是' : '否' }}</td>
            <td>{{ item.冲突 ? '是' : '否' }}</td>
            <td>{{ item.有效映射编号 || '—' }}</td>
            <td>{{ item.有效数据源表 || '—' }}</td>
            <td>{{ item.连接编号 || '—' }}（{{ item.连接状态 }}）</td>
            <td>{{ item.新计划可用 ? '可用' : '不可用' }}</td>
            <td class="note-cell">{{ item.冲突说明 || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <table v-if="dedup" class="data-table mapping-table">
        <thead>
          <tr>
            <th>映射编号</th>
            <th>域编号</th>
            <th>映射来源</th>
            <th>数据源表</th>
            <th>映射状态</th>
            <th>可供新计算</th>
            <th>替代映射</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="mapping in allMappings" :key="mapping.映射编号">
            <td>{{ mapping.映射编号 }}</td>
            <td>{{ mapping.域编号 }}</td>
            <td>{{ mapping.映射来源 }}</td>
            <td>{{ mapping.数据源表 }}</td>
            <td>{{ mapping.映射状态 }}</td>
            <td>{{ mapping.可供新计算 ? '是' : '否' }}</td>
            <td>{{ mapping.替代映射编号 || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

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
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无指标域管理数据，可先登记指标域</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记指标域</h3>
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
      <span>共 {{ total }} 条指标域管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type DedupMapping = {
  映射编号: string
  域编号: string
  映射来源: string
  数据源表: string
  映射状态: string
  可供新计算: boolean
  替代映射编号: string
}

type DedupItem = {
  域编号: string
  域记录数: number
  重复: boolean
  冲突: boolean
  有效映射编号: string
  有效数据源表: string
  连接编号: string
  连接状态: string
  新计划可用: boolean
  冲突说明: string
  映射: DedupMapping[]
}

type DedupList = {
  applied: boolean
  rule: string
  summary: Record<string, number>
  items: DedupItem[]
}

const ENDPOINT = '/api/metric_domain'
const columns = ["域编号", "域名称", "业务域描述", "数据源表", "更新频率", "负责人", "域状态", "创建时间"]
const actions = ["提交校验", "开启域", "停用域"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const metricNames = ["指标域总数", "待校验域", "已启用域"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const showDedup = ref(false)
const dedup = ref<DedupList | null>(null)
const adjudicating = ref(false)
const allMappings = computed<DedupMapping[]>(() =>
  (dedup.value?.items ?? []).flatMap((item) => item.映射 ?? []),
)
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
    link.download = 'metric_domain.csv'
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

async function openDedup() {
  errorMessage.value = ''
  showDedup.value = true
  await loadDedup()
}

async function loadDedup() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/mapping/dedup-list`)
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '去重清单读取失败')
    dedup.value = payload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '去重清单读取失败'
  }
}

async function adjudicate() {
  adjudicating.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/mapping/adjudicate`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.detail || payload.message || '裁决失败')
    dedup.value = payload
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '裁决失败'
  } finally {
    adjudicating.value = false
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
      throw new Error('指标域管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标域管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('指标域列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标域管理列表读取失败'
  }
}

onMounted(reload)
</script>
