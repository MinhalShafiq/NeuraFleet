// Simple primitive-built robot models for the 3D viewer, as plain data so they can be unit-tested
// without WebGL.  Conventions:
//   - metres, three.js axes (y up), the robot faces +X, its origin is on the ground under the sensor
//   - the LiDAR service mounts the sensor 1.8 m above the ground, so every model carries a sensor
//     puck at exactly that height: the point cloud visibly radiates from it
//   - part: { shape: 'box' | 'cylinder', size: [..], position: [x, y, z], color }
//       box size = [length (x), height (y), width (z)]   cylinder size = [radius, height]
//     a cylinder with `rotation: [rx, 0, rz]` is turned (used for wheels, whose axis is z)

export const SENSOR_HEIGHT = 1.8

const wheel = (x, z, r = 0.22, w = 0.16) => ({
  shape: 'cylinder',
  size: [r, w],
  position: [x, r, z],
  rotation: [Math.PI / 2, 0, 0],
  color: '#1e293b',
})
const wheels = (xs, track, r, w) =>
  xs.flatMap((x) => [wheel(x, track, r, w), wheel(x, -track, r, w)])

const mast = (height) => ({
  shape: 'cylinder',
  size: [0.04, height],
  position: [0, SENSOR_HEIGHT - height / 2, 0],
  color: '#64748b',
})
const puck = () => ({
  shape: 'cylinder',
  size: [0.12, 0.14],
  position: [0, SENSOR_HEIGHT, 0],
  color: '#22d3ee',
})
// a small light on the front edge, so heading is readable from any angle
const nose = (x, y, color = '#f8fafc') => ({
  shape: 'box',
  size: [0.08, 0.1, 0.3],
  position: [x, y, 0],
  color,
})

const MODELS = {
  // all-terrain rover: low body, big wheels, forward camera
  explorer: [
    { shape: 'box', size: [1.1, 0.4, 0.7], position: [0, 0.55, 0], color: '#3b82f6' },
    ...wheels([-0.4, 0.4], 0.45, 0.28, 0.18),
    { shape: 'box', size: [0.25, 0.2, 0.3], position: [0.4, 0.85, 0], color: '#1e3a8a' },
    mast(0.9),
    puck(),
    nose(0.56, 0.6),
  ],
  // small and fast: narrow low chassis
  scout: [
    { shape: 'box', size: [0.7, 0.25, 0.45], position: [0, 0.4, 0], color: '#10b981' },
    ...wheels([-0.25, 0.25], 0.3, 0.17, 0.12),
    mast(1.2),
    puck(),
    nose(0.36, 0.42, '#fef08a'),
  ],
  // big flat-bed carrier with a cargo block
  hauler: [
    { shape: 'box', size: [1.8, 0.35, 1.0], position: [0, 0.5, 0], color: '#f59e0b' },
    { shape: 'box', size: [0.9, 0.5, 0.9], position: [-0.35, 0.93, 0], color: '#92400e' },
    ...wheels([-0.65, 0, 0.65], 0.58, 0.25, 0.2),
    { shape: 'box', size: [0.45, 0.45, 0.8], position: [0.6, 0.93, 0], color: '#fbbf24' },
    mast(0.55),
    puck(),
    nose(0.91, 0.55),
  ],
  // tall static-looking tower on a tracked base
  sentinel: [
    { shape: 'box', size: [0.9, 0.3, 0.7], position: [0, 0.25, 0], color: '#475569' },
    { shape: 'box', size: [0.55, 1.1, 0.5], position: [0, 0.95, 0], color: '#ef4444' },
    mast(0.15),
    puck(),
    nose(0.46, 0.3, '#fca5a5'),
  ],
  // survey platform with a spinning-dish look: wide flat deck plus a scanner dome
  mapper: [
    { shape: 'box', size: [1.0, 0.3, 0.8], position: [0, 0.45, 0], color: '#a855f7' },
    ...wheels([-0.35, 0.35], 0.48, 0.24, 0.16),
    { shape: 'cylinder', size: [0.3, 0.12], position: [-0.2, 0.7, 0], color: '#6b21a8' },
    mast(0.95),
    puck(),
    nose(0.51, 0.5),
  ],
  // comms tower: slim body with an antenna
  relay: [
    { shape: 'box', size: [0.6, 0.3, 0.6], position: [0, 0.35, 0], color: '#06b6d4' },
    ...wheels([-0.2, 0.2], 0.36, 0.18, 0.12),
    { shape: 'cylinder', size: [0.03, 0.8], position: [-0.15, 1.1, 0], color: '#94a3b8' },
    { shape: 'box', size: [0.05, 0.05, 0.3], position: [-0.15, 1.5, 0], color: '#94a3b8' },
    mast(1.2),
    puck(),
    nose(0.31, 0.38),
  ],
}

// An unknown type (a new RobotType added in the backend) gets the explorer rather than nothing.
export const FALLBACK_TYPE = 'explorer'

export function robotModelParts(robotType) {
  return MODELS[robotType] || MODELS[FALLBACK_TYPE]
}

// The scan heading is radians in the world x-y plane (0 = +X, counter-clockwise seen from above).
// three.js has y up and z = world y, so a counter-clockwise turn is a NEGATIVE rotation about y.
export function headingToYaw(heading) {
  return Number.isFinite(heading) && heading !== 0 ? -heading : 0 // never -0
}

export const ROBOT_TYPES = Object.keys(MODELS)
