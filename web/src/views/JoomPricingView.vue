<!-- JOOM 定价计算器：左侧动态参数（利润率/促销折扣/佣金/汇率/MSRP），右侧实时定价结果 -->
<template>
  <div class="page joom-page">
    <header class="page__header">
      <div class="page__heading">
        <h1 class="page__title">JOOM 定价计算器</h1>
        <span class="page__subtitle">输入成本，动态调节利润率、促销折扣、平台佣金等参数，实时生成 JOOM 定价方案</span>
      </div>
      <div class="page__actions">
        <el-button text class="btn-press" :loading="rateLoading" @click="refreshRate">
          <el-icon class="btn-icon" aria-hidden="true"><Refresh /></el-icon>
          刷新汇率
        </el-button>
        <el-button text class="btn-press" @click="resetDefaults">
          <el-icon class="btn-icon" aria-hidden="true"><RefreshLeft /></el-icon>
          恢复默认
        </el-button>
      </div>
    </header>

    <div class="joom-grid">
      <!-- ═══ 参数区 ═══ -->
      <section class="card glass-surface joom-params" aria-label="定价参数">
        <h2 class="card__title">定价参数</h2>

        <!-- 成本 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label" for="joomCost">商品成本</label>
            <span class="param-value">{{ fmt(cost) }} CNY</span>
          </div>
          <el-input-number
            id="joomCost"
            v-model="cost"
            :min="0.01"
            :max="100000"
            :precision="2"
            :step="5"
            controls-position="right"
            class="param-input"
          />
        </div>

        <!-- 汇率 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label" for="joomRate">汇率（USD→CNY）</label>
            <el-tag :type="rateLive ? 'success' : 'warning'" size="small" effect="light" class="rate-tag">
              {{ rateLive ? '实时' : rateFromCache ? '缓存' : '默认' }}
            </el-tag>
          </div>
          <el-input-number
            id="joomRate"
            v-model="rate"
            :min="1"
            :max="20"
            :precision="2"
            :step="0.01"
            controls-position="right"
            class="param-input"
          />
          <div class="param-hint">1 USD ≈ {{ fmt(rate) }} CNY，可手动微调；点「刷新汇率」拉取实时值</div>
        </div>

        <!-- 目标利润率 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label">目标利润率</label>
            <el-switch v-model="autoMargin" size="small" active-text="自动" inactive-text="手动" inline-prompt />
          </div>
          <el-slider
            v-model="margin"
            :min="0"
            :max="marginMax"
            :step="0.5"
            :disabled="autoMargin"
            :show-tooltip="true"
            :format-tooltip="(v: number) => v + '%'"
          />
          <div class="param-hint">
            <template v-if="autoMargin">
              自动模式：成本 {{ fmt(cost) }} 元 → 建议区间 {{ rangeLabel }}，取中值
              <b class="param-em">{{ fmt(autoMarginValue) }}%</b>
            </template>
            <template v-else>
              手动模式：当前 {{ fmt(margin) }}%（上限 {{ fmt(marginMax) }}%，受佣金率约束）
            </template>
          </div>
        </div>

        <!-- 平台佣金 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label">平台佣金率</label>
            <span class="param-value">{{ fmt(commission) }}%</span>
          </div>
          <el-slider
            v-model="commission"
            :min="0"
            :max="50"
            :step="0.5"
            :show-tooltip="true"
            :format-tooltip="(v: number) => v + '%'"
          />
          <div class="param-hint">Joom 平台抽成比例，默认 15%</div>
        </div>

        <!-- 促销折扣 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label">促销折扣率</label>
            <span class="param-value">{{ fmt(promo) }}%</span>
          </div>
          <el-slider
            v-model="promo"
            :min="0"
            :max="80"
            :step="0.5"
            :show-tooltip="true"
            :format-tooltip="(v: number) => v + '%'"
          />
          <div class="param-hint">填入平台的折扣价按此比例打折后 = 买家支付价，默认 15%</div>
        </div>

        <!-- MSRP 倍数 -->
        <div class="param-block">
          <div class="param-head">
            <label class="param-label">MSRP 划线价倍数</label>
            <span class="param-value">{{ fmt(msrpMultiple) }}×</span>
          </div>
          <el-slider
            v-model="msrpMultiple"
            :min="1.5"
            :max="5"
            :step="0.5"
            :show-tooltip="true"
            :format-tooltip="(v: number) => v + '×'"
          />
          <div class="param-hint">划线价 = 买家支付价 × 倍数，默认 2×（页面显示“5 折”标签）</div>
        </div>
      </section>

      <!-- ═══ 结果区 ═══ -->
      <section class="card glass-surface joom-results" aria-label="定价结果">
        <h2 class="card__title">定价结果</h2>

        <!-- 主卡片：促销折扣价格 -->
        <div class="hero-card">
          <div class="hero-label">促销折扣价格（填入平台）</div>
          <div class="hero-value">${{ fmt(result.platformPrice) }}</div>
          <div class="hero-sub">买家支付价 ${{ fmt(result.buyerPrice) }} ÷ (1 − {{ fmt(promo) }}% 折扣)</div>
          <el-button type="primary" class="btn-press hero-copy" :icon="CopyDocument" @click="copyPlatformPrice">
            {{ copied ? '已复制 ✓' : '复制价格' }}
          </el-button>
        </div>

        <!-- 次级卡片 -->
        <div class="result-grid">
          <div class="result-tile">
            <div class="tile-label">JOOM 售价（买家支付）</div>
            <div class="tile-value">${{ fmt(result.buyerPrice) }}</div>
          </div>
          <div class="result-tile">
            <div class="tile-label">MSRP 划线价</div>
            <div class="tile-value tile-value--muted">${{ fmt(result.msrp) }}</div>
          </div>
          <div class="result-tile">
            <div class="tile-label">利润（人民币）</div>
            <div class="tile-value" :class="{ 'is-negative': result.profit < 0 }">
              {{ fmt(result.profit) }} 元
            </div>
          </div>
          <div class="result-tile">
            <div class="tile-label">实际利润率</div>
            <div class="tile-value" :class="{ 'is-negative': result.margin < 0 }">
              {{ fmt(result.margin) }}%
            </div>
          </div>
        </div>

        <!-- 收益明细 -->
        <div class="breakdown" aria-label="收益明细">
          <div class="breakdown-row">
            <span>买家支付 × 汇率</span>
            <span>${{ fmt(result.buyerPrice) }} × {{ fmt(rate) }} = {{ fmt(result.buyerPrice * rate) }} 元</span>
          </div>
          <div class="breakdown-row">
            <span>平台佣金（{{ fmt(commission) }}%）</span>
            <span>− {{ fmt(result.commissionAmount) }} 元</span>
          </div>
          <div class="breakdown-row">
            <span>卖家到手</span>
            <span class="breakdown-strong">{{ fmt(result.netRevenue) }} 元</span>
          </div>
          <div class="breakdown-row">
            <span>减：商品成本</span>
            <span>− {{ fmt(cost) }} 元</span>
          </div>
          <div class="breakdown-row breakdown-row--total">
            <span>利润</span>
            <span :class="{ 'is-negative': result.profit < 0 }">
              {{ fmt(result.profit) }} 元（{{ fmt(result.margin) }}%）
            </span>
          </div>
        </div>

        <el-alert v-if="marginClamped" type="warning" :closable="false" show-icon class="joom-warning">
          佣金率调低后利润率上限收窄，目标利润率已自动调整为 {{ fmt(marginMax) }}%
        </el-alert>
      </section>
    </div>

    <!-- ═══ 公式说明 ═══ -->
    <section class="card glass-surface joom-note" aria-label="定价公式说明">
      <h2 class="card__title">定价公式说明</h2>
      <div class="note-grid">
        <code class="note-code">买家支付价 = 成本 ÷ (汇率 × (1 − 佣金率 − 利润率))</code>
        <code class="note-code">促销折扣价 = 买家支付价 ÷ (1 − 折扣率)</code>
        <code class="note-code">利润 = 买家支付价 × 汇率 × (1 − 佣金率) − 成本</code>
        <code class="note-code">利润率 = 利润 ÷ (买家支付价 × 汇率)</code>
      </div>
      <p class="note-tip">
        使用流程：填入成本 → 设定利润率与折扣 → 把「促销折扣价格」填入 Joom 平台价格栏，平台自动打折后买家支付「JOOM 售价」。参数会自动保存在本机，下次打开沿用。
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CopyDocument, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import {
  DEFAULT_COMMISSION,
  DEFAULT_MSRP_MULTIPLE,
  DEFAULT_PROMO,
  FALLBACK_RATE,
  autoMarginFor,
  calcJoom,
  fetchUsdCnyRate,
  getTargetRange,
  marginCeiling,
} from '../utils/joomPricing'

const PARAMS_KEY = 'joom.params.v1'

// ── 参数状态 ──
const cost = ref(40)
const rate = ref(FALLBACK_RATE)
const rateLive = ref(false)
const rateFromCache = ref(false)
const rateLoading = ref(false)
const autoMargin = ref(true)
const margin = ref(40)
const commission = ref(DEFAULT_COMMISSION)
const promo = ref(DEFAULT_PROMO)
const msrpMultiple = ref(DEFAULT_MSRP_MULTIPLE)
const copied = ref(false)

// ── 派生 ──
const rangeLabel = computed(() => getTargetRange(cost.value).label)
const autoMarginValue = computed(() => autoMarginFor(cost.value))
const marginMax = computed(() => marginCeiling(commission.value))
const marginClamped = computed(() => !autoMargin.value && margin.value > marginMax.value)

const result = computed(() =>
  calcJoom({
    cost: cost.value,
    rate: rate.value,
    commission: commission.value,
    margin: autoMargin.value ? autoMarginValue.value : Math.min(margin.value, marginMax.value),
    promo: promo.value,
    msrpMultiple: msrpMultiple.value,
  }),
)

// ── 本地持久化 ──
function loadParams(): void {
  try {
    const raw = localStorage.getItem(PARAMS_KEY)
    if (!raw) return
    const p = JSON.parse(raw) as Record<string, number | boolean>
    if (typeof p.cost === 'number' && p.cost > 0) cost.value = p.cost
    if (typeof p.rate === 'number' && p.rate > 0) rate.value = p.rate
    if (typeof p.autoMargin === 'boolean') autoMargin.value = p.autoMargin
    if (typeof p.margin === 'number') margin.value = p.margin
    if (typeof p.commission === 'number') commission.value = p.commission
    if (typeof p.promo === 'number') promo.value = p.promo
    if (typeof p.msrpMultiple === 'number') msrpMultiple.value = p.msrpMultiple
  } catch {
    /* ignore 损坏的缓存 */
  }
}

function saveParams(): void {
  try {
    localStorage.setItem(
      PARAMS_KEY,
      JSON.stringify({
        cost: cost.value,
        rate: rate.value,
        autoMargin: autoMargin.value,
        margin: margin.value,
        commission: commission.value,
        promo: promo.value,
        msrpMultiple: msrpMultiple.value,
      }),
    )
  } catch {
    /* ignore */
  }
}

watch([cost, rate, autoMargin, margin, commission, promo, msrpMultiple], saveParams)

// ── 交互联动 ──
/** 佣金率变化时，手动利润率若超新上限则自动收窄 */
watch(commission, (c) => {
  const ceiling = marginCeiling(c)
  if (!autoMargin.value && margin.value > ceiling) margin.value = ceiling
})

/** 切到手动模式时，以当前自动建议值为起点 */
watch(autoMargin, (auto) => {
  if (!auto) margin.value = Math.min(autoMarginValue.value, marginMax.value)
})

async function refreshRate(): Promise<void> {
  rateLoading.value = true
  try {
    const { rate: r, live } = await fetchUsdCnyRate()
    rate.value = r
    rateLive.value = live
    rateFromCache.value = !live && r !== FALLBACK_RATE
  } finally {
    rateLoading.value = false
  }
}

function resetDefaults(): void {
  cost.value = 40
  autoMargin.value = true
  margin.value = 40
  commission.value = DEFAULT_COMMISSION
  promo.value = DEFAULT_PROMO
  msrpMultiple.value = DEFAULT_MSRP_MULTIPLE
}

function fmt(v: number): string {
  return Number.isFinite(v) ? String(Math.round(v * 100) / 100) : '--'
}

function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

async function copyPlatformPrice(): Promise<void> {
  const text = String(result.value.platformPrice)
  let ok = false
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      /* 降级 */
    }
  }
  if (!ok) ok = legacyCopy(text)
  if (ok) {
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  }
}

onMounted(() => {
  loadParams()
  void refreshRate()
})
</script>

<style scoped>
.page {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}

.page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}

.page__heading {
  min-width: 0;
}

.page__title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text-primary);
}

.page__subtitle {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}

.page__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.btn-icon {
  margin-right: 4px;
}

/* ── 双栏布局 ── */
.joom-grid {
  display: grid;
  grid-template-columns: minmax(320px, 400px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

/* ── 参数区 ── */
.joom-params {
  position: sticky;
  top: 24px;
}

.param-block {
  margin-bottom: 22px;
}

.param-block:last-child {
  margin-bottom: 0;
}

.param-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.param-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.param-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-blue);
  font-variant-numeric: tabular-nums;
}

.param-input {
  width: 100%;
}

.param-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.param-em {
  color: var(--accent-indigo);
}

.rate-tag {
  flex-shrink: 0;
}

/* ── 结果区 ── */
.joom-results {
  min-width: 0;
}

.hero-card {
  position: relative;
  text-align: center;
  padding: 32px 24px 28px;
  border-radius: var(--radius-3xl);
  background: linear-gradient(160deg, color-mix(in srgb, var(--accent-blue) 8%, #fff), rgba(255, 255, 255, 0.6));
  border: 1px solid color-mix(in srgb, var(--accent-blue) 18%, transparent);
  margin-bottom: 16px;
  overflow: hidden;
}

.hero-card::before {
  content: '';
  position: absolute;
  width: 220px;
  height: 220px;
  top: -90px;
  right: -60px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  filter: blur(60px);
  pointer-events: none;
}

.hero-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
}

.hero-value {
  margin-top: 10px;
  font-size: 52px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.05;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.hero-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.hero-copy {
  margin-top: 18px;
  min-width: 132px;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.result-tile {
  padding: 18px 16px;
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
}

.tile-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.tile-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.tile-value--muted {
  color: var(--text-secondary);
}

.is-negative {
  color: var(--accent-red);
}

/* ── 收益明细 ── */
.breakdown {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-2xl);
  padding: 6px 16px;
  background: var(--bg-subtle);
}

.breakdown-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  font-size: 13px;
  color: var(--text-secondary);
  border-bottom: 1px dashed var(--border-subtle);
  font-variant-numeric: tabular-nums;
}

.breakdown-row:last-child {
  border-bottom: none;
}

.breakdown-strong {
  font-weight: 600;
  color: var(--accent-green);
}

.breakdown-row--total {
  font-weight: 700;
  color: var(--text-primary);
  font-size: 14px;
}

.joom-warning {
  margin-top: 16px;
}

/* ── 公式说明 ── */
.joom-note {
  margin-top: 20px;
}

.note-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.note-code {
  display: block;
  padding: 10px 14px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
  white-space: normal;
  word-break: break-all;
}

.note-tip {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-secondary);
}

/* ── 响应式 ── */
@media (max-width: 960px) {
  .joom-grid {
    grid-template-columns: 1fr;
  }

  .joom-params {
    position: static;
  }
}

@media (max-width: 560px) {
  .page {
    padding: 16px;
  }

  .page__header {
    flex-direction: column;
    align-items: flex-start;
  }

  .result-grid {
    grid-template-columns: 1fr;
  }

  .hero-value {
    font-size: 40px;
  }
}
</style>
