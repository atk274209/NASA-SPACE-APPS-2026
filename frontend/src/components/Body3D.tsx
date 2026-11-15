// The body map in 3D: a procedural human figure standing in a cut-away crew-quarters module.
// The skin is a translucent shell; inside it the organs of each body system are coloured by that
// system's status (the same severity colours as the flat map). The body turns slowly; drag it or
// use the left and right arrow keys to turn it yourself, and click an organ to select its system.
// Keyboard users choose systems in the list next to it, which stays the accessible control.
//
// Everything is built from three.js primitives at runtime: no model files, no network.
// Loaded lazily (see BodyMap), so three.js is only downloaded with the Health page.
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import * as THREE from 'three'
import type { BodyStatus, BodySystem } from '../api/guidance'

const STATUS_TOKEN: Record<BodyStatus, string> = {
  red: '--color-sev-critical',
  yellow: '--color-sev-warning',
  watch: '--color-sev-watch',
  green: '--color-sev-normal',
  no_data: '--color-ink-faint',
}
const FALLBACK: Record<string, string> = {
  '--color-sev-critical': '#ef5560',
  '--color-sev-warning': '#ec8a3d',
  '--color-sev-watch': '#e0b64a',
  '--color-sev-normal': '#5cb98b',
  '--color-ink-faint': '#727c84',
  '--color-ink': '#ebe7df',
  '--color-ink-dim': '#a8afb3',
  '--color-signal': '#86aef0',
  '--color-signal-dim': '#2f4566',
  '--color-hull': '#1a2127',
  '--color-deck': '#222b32',
  '--color-seam': '#2b353d',
  '--color-seam-strong': '#3a4650',
  '--color-void': '#141a1f',
}

function token(name: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || FALLBACK[name] || '#888888'
  } catch {
    return FALLBACK[name] || '#888888'
  }
}

/* ------------------------------------------------------------------ geometry helpers */
const UP = new THREE.Vector3(0, 1, 0)
const v = (x: number, y: number, z = 0) => new THREE.Vector3(x, y, z)

/** A capsule from a to b. */
function capsule(a: THREE.Vector3, b: THREE.Vector3, r: number, mat: THREE.Material): THREE.Mesh {
  const dir = b.clone().sub(a)
  const len = Math.max(dir.length() - 0.0001, 0.0001)
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, len, 8, 20), mat)
  m.position.copy(a).add(b).multiplyScalar(0.5)
  m.quaternion.setFromUnitVectors(UP, dir.normalize())
  return m
}

function ellipsoid(r: [number, number, number], at: THREE.Vector3, mat: THREE.Material, seg = 32): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.SphereGeometry(1, seg, Math.round(seg * 0.75)), mat)
  m.scale.set(...r)
  m.position.copy(at)
  return m
}

/** Gentle folds on the brain hemispheres. */
function wrinkle(g: THREE.BufferGeometry, amount: number, freq: number): THREE.BufferGeometry {
  const p = g.attributes.position as THREE.BufferAttribute
  const n = g.attributes.normal as THREE.BufferAttribute
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i)
    const d = amount * Math.sin(freq * x + 1.3) * Math.sin(freq * y * 1.2) * Math.sin(freq * z + 0.7)
    p.setXYZ(i, x + n.getX(i) * d, y + n.getY(i) * d, z + n.getZ(i) * d)
  }
  g.computeVertexNormals()
  return g
}

function teardrop(height: number, width: number): THREE.LatheGeometry {
  const pts: THREE.Vector2[] = []
  for (let i = 0; i <= 32; i++) {
    const t = (i / 32) * Math.PI
    pts.push(new THREE.Vector2(width * Math.sin(t) * Math.pow(Math.sin(t / 2), 1.6), (height / 2) * Math.cos(t)))
  }
  return new THREE.LatheGeometry(pts.reverse(), 36)
}

/** A tube through points on the cabin wall (radius r) at height y, between two wall angles. */
function wallArc(r: number, y: number, from: number, to: number, thick: number, mat: THREE.Material): THREE.Mesh {
  const pts: THREE.Vector3[] = []
  for (let i = 0; i <= 48; i++) {
    const t = from + ((to - from) * i) / 48
    pts.push(v(r * Math.sin(t), y, r * Math.cos(t)))
  }
  return new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 96, thick, 8, false), mat)
}

/* ------------------------------------------------------------------ materials */
const SKIN_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`
const SKIN_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uBase;
  uniform float uRim;
  uniform float uPower;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    float facing = clamp(dot(normalize(vNormal), normalize(vView)), 0.0, 1.0);
    float rim = pow(1.0 - facing, uPower);
    float alpha = clamp(uBase + rim * uRim, 0.0, 1.0);
    gl_FragColor = vec4(uColor * (0.55 + rim * 0.9), alpha);
    #include <colorspace_fragment>
  }
`

function skinMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: SKIN_VERTEX,
    fragmentShader: SKIN_FRAGMENT,
    uniforms: {
      uColor: { value: new THREE.Color() },
      uBase: { value: 0.05 },
      uRim: { value: 0.75 },
      uPower: { value: 2.2 },
    },
    transparent: true,
    depthWrite: false,
  })
}

function organMaterial(): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ roughness: 0.45, metalness: 0.05, transparent: true })
}

/* ------------------------------------------------------------------ the scene */
interface Built {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  body: THREE.Group
  heart: THREE.Object3D
  drop: THREE.Object3D
  organs: THREE.Mesh[]
  skin: THREE.Mesh[]
  skinMat: THREE.ShaderMaterial
  materials: Map<string, THREE.MeshStandardMaterial>
  outlines: Map<string, THREE.Mesh[]>
}

const CABIN_R = 0.95
const CABIN_H = 2.25
const OPEN = 1.05 // radians each side of the front left open (cut-away)

function build(canvas: HTMLCanvasElement): Built {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 50)

  const signal = new THREE.Color(token('--color-signal'))
  scene.add(new THREE.HemisphereLight(0xc9d8f5, 0x0b0f13, 1.1))
  const key = new THREE.DirectionalLight(0xffffff, 1.6)
  key.position.set(1.6, 3, 3.2)
  scene.add(key)
  const rim = new THREE.DirectionalLight(signal, 1.2)
  rim.position.set(-2.2, 2.2, -2.5)
  scene.add(rim)
  const ceilingLight = new THREE.PointLight(signal, 2.2, 4.5, 1.6)
  ceilingLight.position.set(0, CABIN_H - 0.15, 0.1)
  scene.add(ceilingLight)

  /* ---- the cabin: a cut-away cylinder module (static) */
  const hull = new THREE.MeshStandardMaterial({ color: token('--color-hull'), roughness: 0.85, metalness: 0.25,
    side: THREE.DoubleSide })
  const rib = new THREE.MeshStandardMaterial({ color: token('--color-seam-strong'), roughness: 0.5, metalness: 0.5 })
  const rail = new THREE.MeshStandardMaterial({ color: 0x9aa3a8, roughness: 0.35, metalness: 0.8 })
  const glow = new THREE.MeshBasicMaterial({ color: signal, transparent: true, opacity: 0.85 })
  const cabin = new THREE.Group()
  const from = OPEN, to = Math.PI * 2 - OPEN
  const wall = new THREE.Mesh(new THREE.CylinderGeometry(CABIN_R, CABIN_R, CABIN_H, 72, 1, true, from, to - from), hull)
  wall.position.y = CABIN_H / 2
  cabin.add(wall)
  for (let i = 0; i <= 10; i++) { // vertical ribs
    const t = from + ((to - from) * i) / 10
    const r = new THREE.Mesh(new THREE.BoxGeometry(0.03, CABIN_H, 0.035), rib)
    r.position.set((CABIN_R - 0.02) * Math.sin(t), CABIN_H / 2, (CABIN_R - 0.02) * Math.cos(t))
    r.rotation.y = t
    cabin.add(r)
  }
  for (const y of [0.06, 0.75, 1.5, CABIN_H - 0.06]) cabin.add(wallArc(CABIN_R - 0.03, y, from, to, 0.014, rib))
  for (const [a, y] of [[2.2, 1.05], [4.1, 1.05], [2.2, 1.7], [4.1, 1.7]] as const) { // handrails
    cabin.add(wallArc(CABIN_R - 0.09, y, a - 0.22, a + 0.22, 0.013, rail))
  }
  for (const t of [1.75, 4.55]) { // soft light strips
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(0.07, 0.75),
      new THREE.MeshBasicMaterial({ color: signal, transparent: true, opacity: 0.35, side: THREE.DoubleSide }))
    strip.position.set((CABIN_R - 0.04) * Math.sin(t), 1.65, (CABIN_R - 0.04) * Math.cos(t))
    strip.rotation.y = t
    cabin.add(strip)
  }
  // porthole on the back wall: Earth outside
  const earthCanvas = document.createElement('canvas')
  earthCanvas.width = earthCanvas.height = 128
  const g2 = earthCanvas.getContext('2d')
  if (g2) {
    const grad = g2.createRadialGradient(48, 44, 4, 64, 64, 64)
    grad.addColorStop(0, '#d8ecff')
    grad.addColorStop(0.35, '#4a86c8')
    grad.addColorStop(0.75, '#16365c')
    grad.addColorStop(1, '#05080c')
    g2.fillStyle = grad
    g2.fillRect(0, 0, 128, 128)
    g2.fillStyle = 'rgba(120,170,110,0.55)'
    g2.beginPath(); g2.ellipse(70, 70, 18, 10, 0.6, 0, Math.PI * 2); g2.fill()
    g2.beginPath(); g2.ellipse(44, 84, 9, 6, -0.4, 0, Math.PI * 2); g2.fill()
  }
  const earthTex = new THREE.CanvasTexture(earthCanvas)
  earthTex.colorSpace = THREE.SRGBColorSpace
  const port = new THREE.Mesh(new THREE.CircleGeometry(0.17, 48), new THREE.MeshBasicMaterial({ map: earthTex }))
  const portAngle = Math.PI + 0.85
  port.position.set((CABIN_R - 0.025) * Math.sin(portAngle), 1.48, (CABIN_R - 0.025) * Math.cos(portAngle))
  port.rotation.y = portAngle + Math.PI
  cabin.add(port)
  const frame = new THREE.Mesh(new THREE.TorusGeometry(0.175, 0.025, 12, 48), rib)
  frame.position.copy(port.position)
  frame.rotation.y = port.rotation.y
  cabin.add(frame)
  // floor, polar grid, ceiling ring light
  const floor = new THREE.Mesh(new THREE.CylinderGeometry(CABIN_R, CABIN_R, 0.04, 72),
    new THREE.MeshStandardMaterial({ color: token('--color-deck'), roughness: 0.9, metalness: 0.1 }))
  floor.position.y = -0.02
  cabin.add(floor)
  const grid = new THREE.PolarGridHelper(CABIN_R - 0.02, 12, 5, 72, token('--color-seam-strong'), token('--color-seam'))
  grid.position.y = 0.002
  cabin.add(grid)
  const ceiling = new THREE.Mesh(new THREE.RingGeometry(CABIN_R * 0.62, CABIN_R, 72, 1, OPEN - Math.PI * 1.5, to - from), hull)
  ceiling.rotation.x = Math.PI / 2
  ceiling.position.y = CABIN_H
  cabin.add(ceiling)
  const ring = new THREE.Mesh(new THREE.TorusGeometry(CABIN_R * 0.5, 0.02, 10, 72), glow)
  ring.rotation.x = Math.PI / 2
  ring.position.y = CABIN_H - 0.04
  cabin.add(ring)
  // soft contact shadow under the feet
  const shadowCanvas = document.createElement('canvas')
  shadowCanvas.width = shadowCanvas.height = 64
  const s2 = shadowCanvas.getContext('2d')
  if (s2) {
    const sg = s2.createRadialGradient(32, 32, 0, 32, 32, 32)
    sg.addColorStop(0, 'rgba(0,0,0,0.6)')
    sg.addColorStop(1, 'rgba(0,0,0,0)')
    s2.fillStyle = sg
    s2.fillRect(0, 0, 64, 64)
  }
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(0.75, 0.45),
    new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(shadowCanvas), transparent: true, depthWrite: false }))
  shadow.rotation.x = -Math.PI / 2
  shadow.position.y = 0.004
  cabin.add(shadow)
  scene.add(cabin)

  /* ---- the body (turns) */
  const body = new THREE.Group()
  const skinMat = skinMaterial()
  const skin: THREE.Mesh[] = []
  const organs: THREE.Mesh[] = []
  const materials = new Map<string, THREE.MeshStandardMaterial>()
  const outlines = new Map<string, THREE.Mesh[]>()
  const outlineMat = new THREE.MeshBasicMaterial({ color: signal, side: THREE.BackSide, transparent: true,
    opacity: 0.7, depthWrite: false })

  const addSkin = (m: THREE.Mesh) => {
    m.material = skinMat
    m.renderOrder = 3
    m.userData.code = 'immune_skin'
    skin.push(m)
    body.add(m)
  }
  const matFor = (code: string) => {
    let m = materials.get(code)
    if (!m) {
      m = organMaterial()
      materials.set(code, m)
    }
    return m
  }
  const addOrgan = (code: string, m: THREE.Mesh, outlineScale = 1.07, halo?: THREE.BufferGeometry) => {
    m.material = matFor(code)
    m.renderOrder = 1
    m.userData.code = code
    organs.push(m)
    body.add(m)
    const o = new THREE.Mesh(halo ?? m.geometry, outlineMat)
    o.position.copy(m.position)
    o.quaternion.copy(m.quaternion)
    o.scale.copy(m.scale).multiplyScalar(outlineScale)
    o.visible = false
    o.renderOrder = 0
    o.raycast = () => {}
    body.add(o)
    outlines.set(code, [...(outlines.get(code) ?? []), o])
    return m
  }
  const tmp = new THREE.MeshBasicMaterial()

  // skin shell: head, neck, torso (lathe, flattened front to back), arms, hands, legs, feet
  addSkin(ellipsoid([0.098, 0.122, 0.108], v(0, 1.635, 0), tmp))
  addSkin(capsule(v(0, 1.45), v(0, 1.54), 0.048, tmp))
  const torsoProfile = [
    [0.001, 0.86], [0.1, 0.865], [0.155, 0.9], [0.172, 0.96], [0.162, 1.02], [0.138, 1.08], [0.142, 1.15],
    [0.165, 1.24], [0.182, 1.32], [0.188, 1.39], [0.17, 1.44], [0.11, 1.47], [0.05, 1.49], [0.001, 1.495],
  ].map(([x, y]) => new THREE.Vector2(x, y))
  const torso = new THREE.Mesh(new THREE.LatheGeometry(torsoProfile, 48), tmp)
  torso.scale.z = 0.62
  addSkin(torso)
  const limbs: [THREE.Vector3, THREE.Vector3, number][] = []
  for (const s of [-1, 1]) {
    limbs.push([v(0.205 * s, 1.415), v(0.252 * s, 1.15), 0.047]) // upper arm
    limbs.push([v(0.252 * s, 1.15), v(0.276 * s, 0.9), 0.039]) // forearm
    limbs.push([v(0.088 * s, 0.9), v(0.1 * s, 0.5), 0.072]) // thigh
    limbs.push([v(0.1 * s, 0.5), v(0.098 * s, 0.1), 0.05]) // shin
  }
  for (const [a, b, r] of limbs) addSkin(capsule(a, b, r, tmp))
  for (const s of [-1, 1]) {
    addSkin(ellipsoid([0.034, 0.06, 0.022], v(0.282 * s, 0.82), tmp, 20)) // hands
    addSkin(ellipsoid([0.045, 0.034, 0.11], v(0.098 * s, 0.045, 0.045), tmp, 20)) // feet
  }

  // musculoskeletal: limb muscle mass, spine, pelvis, collarbones
  for (const [a, b, r] of limbs) addOrgan('musculoskeletal', capsule(a, b, r * 0.72, tmp), 1.05)
  for (let i = 0; i < 18; i++) {
    const vert = new THREE.Mesh(new THREE.BoxGeometry(0.036, 0.021, 0.03), tmp)
    vert.position.set(0, 0.96 + i * 0.03, -0.06)
    addOrgan('musculoskeletal', vert, 1.15)
  }
  const pelvis = new THREE.Mesh(new THREE.TorusGeometry(0.095, 0.02, 10, 36), tmp)
  pelvis.rotation.x = Math.PI / 2
  pelvis.scale.set(1, 0.62, 1)
  pelvis.position.set(0, 0.93, -0.005)
  addOrgan('musculoskeletal', pelvis)
  for (const s of [-1, 1]) addOrgan('musculoskeletal', capsule(v(0.02 * s, 1.43, 0.03), v(0.18 * s, 1.44, 0.0), 0.011, tmp), 1.2)

  // nervous system: brain hemispheres with folds
  for (const s of [-1, 1]) {
    const hemi = new THREE.Mesh(wrinkle(new THREE.SphereGeometry(1, 48, 36), 0.06, 9), tmp)
    hemi.scale.set(0.044, 0.064, 0.085)
    hemi.position.set(0.042 * s, 1.672, -0.008)
    addOrgan('neuro_behavioral', hemi, 1.05)
  }
  // eyes
  for (const s of [-1, 1]) addOrgan('eyes', ellipsoid([0.017, 0.017, 0.017], v(0.036 * s, 1.632, 0.084), tmp, 20), 1.2)
  // lungs
  for (const s of [-1, 1]) {
    const lung = new THREE.Mesh(teardrop(0.27, 0.075), tmp)
    lung.scale.z = 0.75
    lung.position.set(0.078 * s, 1.275, -0.005)
    lung.rotation.z = 0.08 * s
    addOrgan('respiratory', lung)
  }
  // heart (the figure's left, the viewer's right), beats
  const heart = new THREE.Mesh(teardrop(0.13, 0.062), tmp)
  heart.scale.set(1, 1, 0.8)
  heart.rotation.set(0.2, 0, Math.PI + 0.45)
  heart.position.set(0.032, 1.235, 0.05)
  addOrgan('cardiovascular', heart, 1.08)
  // digestion: stomach and a winding intestine
  addOrgan('gastrointestinal', ellipsoid([0.07, 0.045, 0.045], v(0.045, 1.105, 0.035), tmp))
  const gutPts: THREE.Vector3[] = []
  for (let i = 0; i <= 120; i++) {
    const t = i / 120
    gutPts.push(v(0.085 * Math.sin(t * Math.PI * 7), 1.04 - t * 0.12 + 0.012 * Math.cos(t * Math.PI * 14),
      0.03 + 0.012 * Math.cos(t * Math.PI * 7)))
  }
  const gutCurve = new THREE.CatmullRomCurve3(gutPts)
  const gutGeo = new THREE.TubeGeometry(gutCurve, 300, 0.016, 10)
  gutGeo.computeBoundingBox()
  const gutCentre = gutGeo.boundingBox!.getCenter(new THREE.Vector3())
  gutGeo.translate(-gutCentre.x, -gutCentre.y, -gutCentre.z) // so the halo is centred on the gut, not the origin
  const gutHalo = new THREE.TubeGeometry(gutCurve, 300, 0.022, 10).translate(-gutCentre.x, -gutCentre.y, -gutCentre.z)
  const gut = new THREE.Mesh(gutGeo, tmp)
  gut.position.copy(gutCentre)
  addOrgan('gastrointestinal', gut, 1, gutHalo)
  scene.add(body)

  // metabolism has no single organ: a droplet floats beside the body (does not turn with it)
  const drop = addOrgan('metabolic', new THREE.Mesh(teardrop(0.15, 0.06), tmp), 1.08)
  scene.remove(drop)
  body.remove(drop)
  const dropGroup = new THREE.Group()
  dropGroup.position.set(0.46, 1.0, 0.32)
  dropGroup.scale.setScalar(1.5)
  dropGroup.add(drop)
  for (const o of outlines.get('metabolic') ?? []) {
    body.remove(o)
    dropGroup.add(o)
  }
  const pad = new THREE.Mesh(new THREE.TorusGeometry(0.085, 0.006, 8, 40), glow)
  pad.rotation.x = Math.PI / 2
  pad.position.y = -0.12
  dropGroup.add(pad)
  scene.add(dropGroup)

  tmp.dispose()
  return { renderer, scene, camera, body, heart, drop: dropGroup, organs, skin, skinMat, materials, outlines }
}

function dispose(b: Built) {
  b.renderer.setAnimationLoop(null)
  b.scene.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (mesh.geometry) mesh.geometry.dispose()
    const mats = (Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []) as THREE.Material[]
    for (const m of mats) {
      const map = (m as THREE.MeshBasicMaterial).map
      if (map) map.dispose()
      m.dispose()
    }
  })
  b.renderer.dispose()
}

/** Frame the body (about 2 m tall, with the droplet beside it); the cabin walls run past the edges. */
function frame(camera: THREE.PerspectiveCamera, w: number, h: number) {
  camera.aspect = w / Math.max(h, 1)
  const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))
  const d = Math.max(1.02 / half, 0.82 / (half * camera.aspect))
  camera.position.set(0, 1.12, d)
  camera.lookAt(0, 0.98, 0)
  camera.updateProjectionMatrix()
}

/* ------------------------------------------------------------------ the component */
export interface Body3DProps {
  systems: BodySystem[]
  selected: string
  onSelect: (code: string) => void
  /** Spoken summary of the selected system, for the canvas label. */
  selectedText: string
  /** Called if WebGL fails after all, so the flat map can be shown instead. */
  onFail?: () => void
}

export default function Body3D({ systems, selected, onSelect, selectedText, onFail }: Body3DProps) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const built = useRef<Built | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const labels = useRef(new Map<string, string>())
  labels.current = new Map(systems.map((s) => [s.code, s.label]))
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const turn = useRef({ angle: 0.35, velocity: 0, dragging: false, hover: false, focus: false, visible: true, dirty: true })

  // build once
  useEffect(() => {
    const canvas = canvasRef.current
    const box = wrap.current
    if (!canvas || !box) return
    let b: Built
    try {
      b = build(canvas)
    } catch {
      onFail?.()
      return
    }
    built.current = b
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    const size = () => {
      const w = box.clientWidth, h = box.clientHeight
      b.renderer.setSize(w, h, false)
      frame(b.camera, w, h)
      turn.current.dirty = true
    }
    size()
    const ro = new ResizeObserver(size)
    ro.observe(box)
    const io = new IntersectionObserver(([e]) => { turn.current.visible = e.isIntersecting; turn.current.dirty = true })
    io.observe(box)
    const lost = (e: Event) => { e.preventDefault(); onFail?.() }
    canvas.addEventListener('webglcontextlost', lost)

    const start = performance.now()
    let last = start
    b.renderer.setAnimationLoop(() => {
      const now = performance.now()
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      const t = (now - start) / 1000
      const st = turn.current
      if (!st.visible || document.hidden) return
      const still = reduce.matches
      const moving = !still || st.dragging || Math.abs(st.velocity) > 0.0005
      if (!moving && !st.dirty) return // nothing changes: keep the last frame
      st.dirty = false
      if (!st.dragging) {
        if (Math.abs(st.velocity) > 0.0005) { // a little inertia after a drag
          st.angle += st.velocity
          st.velocity *= 0.92
        } else if (!still && !st.hover && !st.focus) {
          st.angle += dt * 0.32
        }
      }
      b.body.rotation.y = st.angle
      if (!still) {
        const beat = Math.pow(Math.max(0, Math.sin(t * Math.PI * 2.2)), 6)
        b.heart.scale.set(1 + beat * 0.08, 1 + beat * 0.08, 0.8 * (1 + beat * 0.08))
        for (const o of b.outlines.get('cardiovascular') ?? []) o.scale.copy(b.heart.scale).multiplyScalar(1.08)
        b.drop.position.y = 1.0 + Math.sin(t * 1.4) * 0.025
        b.drop.rotation.y = t * 0.6
      }
      b.renderer.render(b.scene, b.camera)
    })
    return () => {
      ro.disconnect()
      io.disconnect()
      canvas.removeEventListener('webglcontextlost', lost)
      dispose(b)
      built.current = null
    }
    // onFail is a stable callback from the parent; the scene is built once per mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // colour each system by its status; highlight the selected one
  useEffect(() => {
    const b = built.current
    if (!b) return
    const status = new Map(systems.map((s) => [s.code, s.status]))
    for (const [code, mat] of b.materials) {
      const st = status.get(code) ?? 'no_data'
      const empty = st === 'no_data'
      const c = new THREE.Color(token(empty ? '--color-ink-dim' : STATUS_TOKEN[st]))
      const isSel = code === selected
      const isHover = labels.current.get(code) === hovered
      mat.color.copy(c)
      mat.emissive.copy(c)
      const muscle = code === 'musculoskeletal'
      mat.emissiveIntensity = isSel ? 0.75 : isHover ? 0.5 : empty ? (muscle ? 0.05 : 0.2) : 0.3
      mat.opacity = empty ? (isSel ? 0.65 : muscle ? 0.2 : 0.5) : isSel ? 0.95 : muscle ? 0.6 : 0.82
      mat.depthWrite = mat.opacity > 0.5
      for (const o of b.outlines.get(code) ?? []) o.visible = isSel
    }
    const skinSt = status.get('immune_skin') ?? 'no_data'
    const skinSel = selected === 'immune_skin'
    const skinHover = labels.current.get('immune_skin') === hovered
    ;(b.skinMat.uniforms.uColor.value as THREE.Color).set(token(skinSt === 'no_data' ? '--color-signal' : STATUS_TOKEN[skinSt]))
    b.skinMat.uniforms.uBase.value = skinSel ? 0.16 : skinHover ? 0.11 : 0.06
    b.skinMat.uniforms.uRim.value = skinSel ? 1.0 : 0.8
    turn.current.dirty = true
  }, [systems, selected, hovered])

  // pointer: drag to turn, click to select, hover to name
  const ray = useRef(new THREE.Raycaster())
  const down = useRef<{ id: number; x: number; y: number; lastX: number; moved: boolean; slop: number } | null>(null)
  const pick = (clientX: number, clientY: number): string | null => {
    const b = built.current
    const c = canvasRef.current
    if (!b || !c) return null
    const r = c.getBoundingClientRect()
    const p = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1)
    b.scene.updateMatrixWorld()
    ray.current.setFromCamera(p, b.camera)
    const organ = ray.current.intersectObjects(b.organs, false)[0]
    if (organ) return organ.object.userData.code as string
    return ray.current.intersectObjects(b.skin, false)[0] ? 'immune_skin' : null
  }
  const onPointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!e.isPrimary || e.button !== 0 || down.current) return // left button or one finger only
    down.current = { id: e.pointerId, x: e.clientX, y: e.clientY, lastX: e.clientX, moved: false,
      slop: e.pointerType === 'mouse' ? 3 : 10 }
    turn.current.dragging = true
    turn.current.velocity = 0
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const d = down.current
    if (d) {
      if (e.pointerId !== d.id) return
      const step = (e.clientX - d.lastX) * 0.012
      d.lastX = e.clientX
      if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) > d.slop) d.moved = true
      if (d.moved) {
        turn.current.angle += step
        turn.current.velocity = step
        turn.current.dirty = true
      }
      return
    }
    const code = e.pointerType === 'mouse' ? pick(e.clientX, e.clientY) : null
    const label = code ? labels.current.get(code) ?? null : null
    setHovered(label)
    e.currentTarget.style.cursor = code ? 'pointer' : 'grab'
  }
  /** The browser took the gesture over (for example to scroll the page): never a click. */
  const onPointerCancel = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (down.current && e.pointerId !== down.current.id) return
    down.current = null
    turn.current.dragging = false
    turn.current.velocity = 0
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const onPointerUp = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    const d = down.current
    if (d && e.pointerId !== d.id) return
    down.current = null
    turn.current.dragging = false
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (d && !d.moved) {
      turn.current.velocity = 0
      const code = pick(e.clientX, e.clientY)
      if (code && labels.current.has(code)) onSelectRef.current(code)
    }
  }
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault()
      turn.current.velocity = 0
      turn.current.angle += e.key === 'ArrowLeft' ? -0.3 : 0.3
      turn.current.focus = true
      turn.current.dirty = true
    }
  }

  return (
    <div className="flex w-full flex-col items-center gap-1">
      <div
        ref={wrap}
        tabIndex={0}
        role="img"
        aria-label={`Body map in 3D inside the crew quarters. Selected: ${selectedText} Use the left and right arrow keys to turn it; choose a body system in the list.`}
        onKeyDown={onKeyDown}
        onFocus={(e) => { turn.current.focus = e.currentTarget.matches(':focus-visible') }}
        onBlur={() => { turn.current.focus = false }}
        className="relative h-[440px] w-full max-w-[320px] overflow-hidden rounded-md"
      >
        <canvas
          ref={canvasRef}
          className="block h-full w-full touch-pan-y"
          style={{ cursor: 'grab' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          onPointerEnter={() => { turn.current.hover = true }}
          onPointerLeave={() => { turn.current.hover = false; setHovered(null) }}
        />
      </div>
      <p aria-hidden className="h-4 text-center text-xs text-ink-dim">{hovered ?? 'Drag to turn, click an organ'}</p>
    </div>
  )
}
