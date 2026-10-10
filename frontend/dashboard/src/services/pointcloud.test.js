import { describe, expect, it } from 'vitest'
import { MAX_POINTS, fillPointBuffers, writeIntensityColor } from './pointcloud'

const buffers = (n = 8) => [new Float32Array(n * 3), new Float32Array(n * 3)]

describe('fillPointBuffers', () => {
  it('centres the cloud on the sensor: a scan taken 50 m from the origin is drawn near the origin', () => {
    // Regression: points are in the world frame; without subtracting the sensor position the
    // viewer showed an empty grid while the cloud sat tens of metres away from the camera.
    const [pos, col] = buffers()
    const n = fillPointBuffers([[52, 47, 1.5, 0.5]], { x: 50, y: 50, z: 0 }, pos, col)
    expect(n).toBe(1)
    expect(Array.from(pos.slice(0, 3))).toEqual([2, 1.5, -3])
  })

  it('swaps to three.js axes: z (up) becomes y, y becomes z', () => {
    const [pos, col] = buffers()
    fillPointBuffers([[1, 2, 3, 0.5]], { x: 0, y: 0 }, pos, col)
    expect(Array.from(pos.slice(0, 3))).toEqual([1, 3, 2])
  })

  it('keeps z absolute so the ground stays on the grid', () => {
    const [pos, col] = buffers()
    fillPointBuffers([[10, 10, 0, 0.5]], { x: 10, y: 10, z: 5 }, pos, col)
    expect(pos[1]).toBe(0)
  })

  it('treats a missing origin as zero (the gateway mock is already sensor-relative)', () => {
    const [pos, col] = buffers()
    fillPointBuffers([[4, 5, 6, 0.5]], undefined, pos, col)
    expect(Array.from(pos.slice(0, 3))).toEqual([4, 6, 5])
  })

  it('returns the point count, caps at the buffer size, and handles no points', () => {
    const [pos, col] = buffers(4)
    const pts = Array.from({ length: 10 }, () => [1, 1, 1, 0.5])
    expect(fillPointBuffers(pts, null, pos, col, 4)).toBe(4)
    expect(fillPointBuffers([], null, pos, col)).toBe(0)
    expect(fillPointBuffers(undefined, null, pos, col)).toBe(0)
    expect(MAX_POINTS).toBeGreaterThanOrEqual(15000)
  })

  it('does not write beyond the points it was given', () => {
    const [pos, col] = buffers(4)
    pos.fill(-1)
    fillPointBuffers([[1, 1, 1, 0.5]], null, pos, col)
    expect(Array.from(pos.slice(3))).toEqual(new Array(9).fill(-1))
  })
})

describe('writeIntensityColor', () => {
  const c = (i) => {
    const out = new Float32Array(3)
    writeIntensityColor(out, 0, i)
    return Array.from(out)
  }
  it('runs blue -> green -> red', () => {
    expect(c(0)).toEqual([0, 0, 1])
    expect(c(0.5)).toEqual([0, 1, 0])
    expect(c(1)).toEqual([1, 0, 0])
  })
  it('clamps out-of-range intensities', () => {
    expect(c(-3)).toEqual(c(0))
    expect(c(7)).toEqual(c(1))
  })
})
