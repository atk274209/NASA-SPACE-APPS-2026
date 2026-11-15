import { useEffect, useRef } from 'react'

// Ambient background: dotted bezier streams flow from both screen edges into the centre, with
// particles riding them; a click sends a shockwave through the particles. Adapted from the
// 21st.dev "gateway-flow" effect (mengto). The original ran a whole demo page in an iframe with
// CDN scripts and fonts; this keeps only the canvas effect, so it runs offline and draws with
// the AM palette. It is decoration only: hidden from assistive technology, never takes a click
// (shockwaves listen on the window), stills itself for reduced motion and pauses in a hidden tab.

export type GatewayFlowProps = {
  /** Animation speed multiplier, 0..3. */
  speed?: number
  /** Number of streams relative to the original 80, 0.25..2.5. */
  density?: number
  /** Line width multiplier, 0.25..4. */
  strokeWidth?: number
  /** Stream and particle colour as "r, g, b". Defaults to the AM signal colour. */
  rgb?: string
  /** Overall strength, 0.05..1. Keep it low behind text. */
  opacity?: number
  /** Click shockwaves. */
  shockwaves?: boolean
  className?: string
}

type Path = { isLeft: boolean; startY: number; particles: { t: number; speed: number }[] }
type Blast = { x: number; y: number; radius: number; life: number }

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function bezier(t: number, p0: number, p1: number, p2: number, p3: number) {
  const u = 1 - t
  return u ** 3 * p0 + 3 * u ** 2 * t * p1 + 3 * u * t ** 2 * p2 + t ** 3 * p3
}

export function GatewayFlow({
  speed = 1,
  density = 1,
  strokeWidth = 1,
  rgb = '134, 174, 240',
  opacity = 0.35,
  shockwaves = true,
  className = '',
}: GatewayFlowProps) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    const k = clamp(speed, 0, 3)
    const lineWidth = 1.2 * clamp(strokeWidth, 0.25, 4)
    const numPaths = Math.max(12, Math.round(80 * clamp(density, 0.25, 2.5)))
    let width = 0
    let height = 0
    let blasts: Blast[] = []
    let raf = 0

    const paths: Path[] = []
    const seed = () => {
      paths.length = 0
      for (let i = 0; i < numPaths; i++) {
        paths.push({
          isLeft: i % 2 === 0,
          startY: (i / numPaths) * height * 1.4 - height * 0.2,
          particles: [{ t: Math.random(), speed: 0.0015 + Math.random() * 0.002 }],
        })
      }
    }

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      seed()
      if (reduce.matches) draw(false)
    }

    const draw = (move: boolean) => {
      ctx.clearRect(0, 0, width, height)
      const cx = width / 2
      const cy = height / 2
      if (move) {
        for (const b of blasts) {
          b.radius += 15 * k
          b.life -= 0.015 * k
        }
        blasts = blasts.filter((b) => b.life > 0)
      }
      for (const path of paths) {
        const x0 = path.isLeft ? 0 : width
        const x1 = path.isLeft ? cx * 0.5 : width - cx * 0.5
        const x2 = path.isLeft ? cx * 0.8 : width - cx * 0.8
        ctx.beginPath()
        ctx.moveTo(x0, path.startY)
        ctx.bezierCurveTo(x1, path.startY, x2, cy, cx, cy)
        ctx.strokeStyle = `rgba(${rgb}, 0.35)`
        ctx.lineWidth = lineWidth
        ctx.setLineDash([1, 4])
        ctx.stroke()
        ctx.setLineDash([])

        for (const p of path.particles) {
          if (move) {
            p.t += p.speed * k
            if (p.t > 1) {
              p.t = 0
              path.startY += (Math.random() - 0.5) * 10
            }
          }
          let x = bezier(p.t, x0, x1, x2, cx)
          let y = bezier(p.t, path.startY, path.startY, cy, cy)
          for (const b of blasts) {
            const dx = x - b.x
            const dy = y - b.y
            const dist = Math.hypot(dx, dy)
            if (dist > 0 && dist < b.radius + 120 && dist > b.radius - 120) {
              const force = (1 - Math.abs(dist - b.radius) / 120) * b.life
              x += (dx / dist) * force * 80
              y += (dy / dist) * force * 80
            }
          }
          ctx.fillStyle = `rgba(${rgb}, 0.7)`
          ctx.fillRect(x - 1.5, y - 1.5, 3, 3)
        }
      }
    }

    const loop = () => {
      draw(true)
      raf = requestAnimationFrame(loop)
    }
    const start = () => {
      cancelAnimationFrame(raf)
      if (reduce.matches || document.hidden || k === 0) draw(false)
      else raf = requestAnimationFrame(loop)
    }
    const onClick = (e: MouseEvent) => {
      if (shockwaves && !reduce.matches) blasts.push({ x: e.clientX, y: e.clientY, radius: 0, life: 1 })
    }

    resize()
    start()
    window.addEventListener('resize', resize)
    window.addEventListener('click', onClick)
    document.addEventListener('visibilitychange', start)
    reduce.addEventListener('change', start)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      window.removeEventListener('click', onClick)
      document.removeEventListener('visibilitychange', start)
      reduce.removeEventListener('change', start)
    }
  }, [speed, density, strokeWidth, rgb, shockwaves])

  return (
    <canvas
      ref={ref}
      aria-hidden
      className={`pointer-events-none fixed inset-0 -z-10 h-full w-full ${className}`}
      style={{ opacity: clamp(opacity, 0.05, 1) }}
    />
  )
}

export default GatewayFlow
