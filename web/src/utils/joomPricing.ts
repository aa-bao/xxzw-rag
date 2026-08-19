/**
 * JOOM 定价计算器 — 纯函数核心模块
 *
 * 业务模型（对齐原 Excel「JOOM定价.2.26-1.xlsx」与旧 Python 项目）：
 *   买家支付价 P  = 成本 / ( 汇率 × (1 − 平台佣金率 − 目标利润率) )
 *   促销折扣价 P0 = P / (1 − 促销折扣率)          ← 需填入平台价格栏
 *   利润      = P × 汇率 × (1 − 平台佣金率) − 成本
 *   利润率    = 利润 / (P × 汇率)
 *   MSRP     = 买家支付价 × MSRP 倍数（默认 2）
 *
 * 原规则（成本分段利润率区间）：
 *   成本 < 30  → 目标利润率 50%–60%
 *   成本 ≥ 30  → 目标利润率 35%–45%
 */

/** 目标利润率区间 */
export interface TargetRange {
  min: number
  max: number
  label: string
}

/** 定价输入参数（全部可由用户动态调整） */
export interface JoomParams {
  /** 商品成本（人民币） */
  cost: number
  /** USD→CNY 汇率 */
  rate: number
  /** 平台佣金率（百分比，Joom 默认 15%） */
  commission: number
  /** 目标利润率（百分比） */
  margin: number
  /** 促销折扣率（百分比，Joom 默认 15%） */
  promo: number
  /** MSRP 划线价倍数（默认 2，即原价 2 倍 → 半价标签） */
  msrpMultiple: number
}

/** 定价计算结果 */
export interface JoomResult {
  /** JOOM 售价（美元，买家实际支付） */
  buyerPrice: number
  /** 促销折扣价格（美元，需填入平台价格栏） */
  platformPrice: number
  /** 建议零售价 / 划线价（美元） */
  msrp: number
  /** 利润（人民币） */
  profit: number
  /** 卖家到手（人民币，扣除平台佣金后） */
  netRevenue: number
  /** 平台佣金金额（人民币） */
  commissionAmount: number
  /** 实际利润率（百分比） */
  margin: number
}

export const DEFAULT_COMMISSION = 15
export const DEFAULT_PROMO = 15
export const DEFAULT_MSRP_MULTIPLE = 2
export const FALLBACK_RATE = 6.77
/** 利润率滑块上限（百分比，避免分母逼近 0） */
export const MARGIN_MAX = 75

const RATE_CACHE_KEY = 'joom.rate.cache.v1'
const FRANKFURTER_URL = 'https://api.frankfurter.dev/v1/latest?from=USD&to=CNY'

function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v))
}

function round2(v: number): number {
  return Math.round(v * 100) / 100
}

/** 按成本返回建议利润率区间（原业务规则） */
export function getTargetRange(cost: number): TargetRange {
  if (cost < 30) return { min: 50, max: 60, label: '50%–60%' }
  return { min: 35, max: 45, label: '35%–45%' }
}

/** 自动模式：取区间中值作为目标利润率 */
export function autoMarginFor(cost: number): number {
  const r = getTargetRange(cost)
  return (r.min + r.max) / 2
}

/**
 * 利润率可用的上限：必须严格小于 (1 − 佣金率)，
 * 否则分母 rate×(1−佣金率−利润率) ≤ 0，价格爆炸。
 */
export function marginCeiling(commission: number): number {
  const ceiling = Math.min(MARGIN_MAX, (1 - clamp(commission, 0, 95) / 100) * 100 - 5)
  // 消除浮点尾差（如 14.999999999999996 → 15）
  return Math.round(ceiling * 10) / 10
}

/** 核心计算 */
export function calcJoom(p: JoomParams): JoomResult {
  const cost = p.cost > 0 ? p.cost : 0
  const rate = p.rate > 0 ? p.rate : FALLBACK_RATE
  const commissionFrac = clamp(p.commission, 0, 95) / 100
  const promoFrac = clamp(p.promo, 0, 95) / 100
  const marginFrac = clamp(p.margin, 0, marginCeiling(p.commission)) / 100
  const msrpMultiple = p.msrpMultiple > 0 ? p.msrpMultiple : DEFAULT_MSRP_MULTIPLE

  const denom = rate * (1 - commissionFrac - marginFrac)
  const buyerPrice = denom > 0 ? cost / denom : 0

  const grossUsd = buyerPrice * rate
  const commissionAmount = grossUsd * commissionFrac
  const netRevenue = grossUsd - commissionAmount
  const profit = netRevenue - cost
  const actualMargin = grossUsd > 0 ? (profit / grossUsd) * 100 : 0

  return {
    buyerPrice: round2(buyerPrice),
    platformPrice: round2(promoFrac < 1 ? buyerPrice / (1 - promoFrac) : 0),
    msrp: round2(buyerPrice * msrpMultiple),
    profit: round2(profit),
    netRevenue: round2(netRevenue),
    commissionAmount: round2(commissionAmount),
    margin: round2(actualMargin),
  }
}

interface RateCache {
  rate: number
  fetchedAt: number
}

/** 读取本地缓存的汇率（上次成功获取） */
export function cachedRate(): RateCache | null {
  try {
    const raw = localStorage.getItem(RATE_CACHE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as RateCache
      if (parsed && parsed.rate > 0) return parsed
    }
  } catch {
    /* ignore */
  }
  return null
}

/**
 * 获取实时 USD→CNY 汇率（Frankfurter 公开 API）。
 * 失败时回退到本地缓存 → 默认汇率 6.77。
 */
export async function fetchUsdCnyRate(): Promise<{ rate: number; live: boolean }> {
  try {
    const resp = await fetch(FRANKFURTER_URL)
    if (!resp.ok) throw new Error('HTTP ' + resp.status)
    const data = (await resp.json()) as { rates?: Record<string, number> }
    const rate = Number(data?.rates?.CNY)
    if (!(rate > 0)) throw new Error('bad rate')
    try {
      localStorage.setItem(RATE_CACHE_KEY, JSON.stringify({ rate, fetchedAt: Date.now() }))
    } catch {
      /* ignore */
    }
    return { rate, live: true }
  } catch {
    const cached = cachedRate()
    if (cached && cached.rate > 0) return { rate: cached.rate, live: false }
    return { rate: FALLBACK_RATE, live: false }
  }
}
