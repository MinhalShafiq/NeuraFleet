import { describe, it, expect } from 'vitest'
import {
  FALLBACK_TYPE,
  ROBOT_TYPES,
  SENSOR_HEIGHT,
  headingToYaw,
  robotModelParts,
} from './robotModels'

describe('robot models', () => {
  it('has a model for every backend robot type', () => {
    // keep in sync with RobotType in backend/shared/models.py
    for (const t of ['explorer', 'hauler', 'sentinel', 'mapper', 'relay', 'scout']) {
      expect(ROBOT_TYPES).toContain(t)
    }
  })

  it('puts a sensor puck exactly where the LiDAR service puts the sensor', () => {
    for (const t of ROBOT_TYPES) {
      const puck = robotModelParts(t).find(
        (p) => p.shape === 'cylinder' && p.color === '#22d3ee' && p.position[1] === SENSOR_HEIGHT,
      )
      expect(puck, t).toBeTruthy()
      expect(puck.position[0]).toBe(0) // directly above the scan origin
      expect(puck.position[2]).toBe(0)
    }
  })

  it('keeps every part above the ground and within a sane size', () => {
    for (const t of ROBOT_TYPES) {
      for (const p of robotModelParts(t)) {
        const half = p.shape === 'box' ? p.size[1] / 2 : p.rotation ? p.size[0] : p.size[1] / 2
        expect(p.position[1] - half, `${t} part below ground`).toBeGreaterThanOrEqual(-1e-9)
        expect(Math.max(...p.position.map(Math.abs))).toBeLessThan(2.1)
      }
    }
  })

  it('has a front marker on the +X side so heading can be read', () => {
    for (const t of ROBOT_TYPES) {
      expect(
        robotModelParts(t).some(
          (p) => p.shape === 'box' && p.position[0] > 0.25 && p.size[2] === 0.3,
        ),
        t,
      ).toBe(true)
    }
  })

  it('falls back to a default model for an unknown type', () => {
    expect(robotModelParts('submarine')).toBe(robotModelParts(FALLBACK_TYPE))
    expect(robotModelParts(undefined)).toBe(robotModelParts(FALLBACK_TYPE))
  })

  it('turns world heading into a three.js yaw (counter-clockwise from above = -y rotation)', () => {
    expect(headingToYaw(0)).toBe(0)
    expect(headingToYaw(Math.PI / 2)).toBeCloseTo(-Math.PI / 2)
    // heading +90 deg faces world +Y, which is three.js +Z: rotate +X by yaw about y and check
    const yaw = headingToYaw(Math.PI / 2)
    expect(Math.cos(yaw)).toBeCloseTo(0)
    expect(-Math.sin(yaw)).toBeCloseTo(1) // z component of the rotated forward axis
    expect(headingToYaw(undefined)).toBe(0)
    expect(headingToYaw(null)).toBe(0)
  })
})
