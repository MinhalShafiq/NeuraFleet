// Pure helpers for turning a LiDAR frame into GPU buffers (kept free of React/three.js so they
// can be unit-tested).

// GPU buffers are allocated once at this size and reused for every frame
// (the REST scan tops out at 15,000 points; the stream sends ~2,000).
export const MAX_POINTS = 20000

// Blue -> green -> red ramp for intensity in [0, 1] (the simulator already clamps it).
// Writes straight into the colour buffer: no per-point allocation.
export function writeIntensityColor(out, offset, intensity) {
  const t = intensity < 0 ? 0 : intensity > 1 ? 1 : intensity
  if (t < 0.5) {
    out[offset] = 0
    out[offset + 1] = t * 2
    out[offset + 2] = 1 - t * 2
  } else {
    out[offset] = (t - 0.5) * 2
    out[offset + 1] = 1 - (t - 0.5) * 2
    out[offset + 2] = 0
  }
}

/**
 * Fill position/colour buffers from a frame's points and return how many were written.
 *
 * LiDAR points are in the WORLD frame (a robot 50 m from the origin produces points 50 m from
 * the origin), but the camera orbits the origin. Subtracting the sensor's world position
 * (`origin`, sent with every frame) centres the cloud on the camera target. Only x and y are
 * shifted: z stays absolute so the ground (z = 0) lines up with the grid.
 *
 * The LiDAR frame is z-up; three.js is y-up, so a point (x, y, z) is written as (x - ox, z, y - oy).
 */
export function fillPointBuffers(points, origin, posArr, colArr, maxPoints = MAX_POINTS) {
  const n = points ? Math.min(points.length, maxPoints) : 0
  const ox = origin?.x ?? 0
  const oy = origin?.y ?? 0
  for (let i = 0; i < n; i++) {
    const p = points[i]
    const o = i * 3
    posArr[o] = p[0] - ox
    posArr[o + 1] = p[2] || 0
    posArr[o + 2] = p[1] - oy
    writeIntensityColor(colArr, o, p[3] || 0)
  }
  return n
}
