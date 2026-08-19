import { describe, expect, it } from 'vitest'
import {
  autoMarginFor,
  calcJoom,
  getTargetRange,
  marginCeiling,
} from './joomPricing'

describe('getTargetRange / autoMarginFor', () => {
  it('成本 < 30 建议利润率 50%–60%（自动取中值 55%）', () => {
    expect(getTargetRange(10).label).toBe('50%–60%')
    expect(autoMarginFor(10)).toBe(55)
  })

  it('成本 ≥ 30 建议利润率 35%–45%（自动取中值 40%）', () => {
    expect(getTargetRange(30).label).toBe('35%–45%')
    expect(getTargetRange(40).label).toBe('35%–45%')
    expect(autoMarginFor(40)).toBe(40)
    expect(autoMarginFor(200)).toBe(40)
  })
})

describe('calcJoom — 与原 Excel / Python 项目数值对齐', () => {
  it('还原 Excel 样例行：成本 40 / 汇率 6.5 / 利润率 50.64%', () => {
    const r = calcJoom({
      cost: 40,
      rate: 6.5,
      commission: 15,
      margin: 50.6401666451918,
      promo: 15,
      msrpMultiple: 2,
    })
    expect(r.buyerPrice).toBe(17.91) // Excel F2 JOOM售价
    expect(r.platformPrice).toBe(21.07) // Excel E2 促销折扣价格 = F2/0.85
    expect(r.profit).toBe(58.95) // Excel I2 利润/CNY
    expect(r.margin).toBe(50.64) // Excel J2 利润率
    expect(r.msrp).toBe(35.82) // MSRP = 价格 × 2
  })

  it('默认参数（佣金/促销 15%）与旧 Python 实现一致', () => {
    const r = calcJoom({ cost: 40, rate: 6.77, commission: 15, margin: 40, promo: 15, msrpMultiple: 2 })
    // 买家价 = 40 / (6.77 × (0.85 − 0.40))
    expect(r.buyerPrice).toBeCloseTo(40 / (6.77 * 0.45), 2)
    expect(r.platformPrice).toBeCloseTo(r.buyerPrice / 0.85, 2)
    expect(r.margin).toBe(40) // 实际利润率 = 目标利润率
  })

  it('利润率受 (1 − 佣金率) 约束，分母不爆炸', () => {
    const r = calcJoom({ cost: 40, rate: 6.5, commission: 15, margin: 200, promo: 15, msrpMultiple: 2 })
    expect(r.buyerPrice).toBeGreaterThan(0)
    expect(r.buyerPrice).toBeLessThan(1000)
    // 被钳制到 marginCeiling(15) = 75
    expect(r.margin).toBeLessThanOrEqual(75)
  })

  it('佣金 / 促销折扣可独立调节', () => {
    const base = calcJoom({ cost: 40, rate: 6.5, commission: 15, margin: 40, promo: 15, msrpMultiple: 2 })
    const lowCommission = calcJoom({ cost: 40, rate: 6.5, commission: 5, margin: 40, promo: 15, msrpMultiple: 2 })
    const lowPromo = calcJoom({ cost: 40, rate: 6.5, commission: 15, margin: 40, promo: 5, msrpMultiple: 2 })

    // 同一目标利润率下：佣金越低 → 买家价越低（分母变大），利润率仍命中目标
    expect(lowCommission.buyerPrice).toBeLessThan(base.buyerPrice)
    expect(lowCommission.margin).toBe(40)
    expect(base.margin).toBe(40)
    // 促销折扣越低 → 填入平台的折扣价越接近买家价
    expect(lowPromo.platformPrice).toBeLessThan(base.platformPrice)
    expect(lowPromo.platformPrice).toBeCloseTo(lowPromo.buyerPrice / 0.95, 1)
  })

  it('MSRP 倍数可调', () => {
    const r = calcJoom({ cost: 40, rate: 6.5, commission: 15, margin: 40, promo: 15, msrpMultiple: 3 })
    expect(r.msrp).toBeCloseTo(r.buyerPrice * 3, 1)
  })

  it('无效输入兜底：成本/汇率为 0 时不产生 NaN', () => {
    const r = calcJoom({ cost: 0, rate: 0, commission: 15, margin: 40, promo: 15, msrpMultiple: 0 })
    expect(Number.isFinite(r.buyerPrice)).toBe(true)
    expect(Number.isFinite(r.profit)).toBe(true)
    expect(r.buyerPrice).toBe(0)
  })
})

describe('marginCeiling', () => {
  it('佣金 15% 时上限 75%，佣金越高上限越低', () => {
    expect(marginCeiling(15)).toBe(75)
    expect(marginCeiling(50)).toBe(45)
    expect(marginCeiling(80)).toBe(15)
  })
})
