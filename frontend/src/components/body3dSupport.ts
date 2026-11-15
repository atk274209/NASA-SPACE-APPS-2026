/** Whether this browser can draw WebGL at all. Kept apart from Body3D so the check does not pull in three.js. */
export function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}
