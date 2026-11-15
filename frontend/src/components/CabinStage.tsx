// The crew quarters around the body map: a cylindrical cabin module drawn with CSS 3D (ribbed wall
// panels, a porthole, a floor grid and a ceiling light ring). The body turns slowly inside it, to
// show that everything AMC watches happens in the cabin. Turning stops while the pointer or keyboard
// focus is on the figure, so every body system stays easy to pick, and for reduced motion.
// Decoration only: hidden from assistive technology; the figure inside keeps its own semantics.
import type { CSSProperties, ReactNode } from 'react'

const PANELS = 16
const RADIUS = 150 // px
const HEIGHT = 430 // px
const PANEL_W = 2 * RADIUS * Math.tan(Math.PI / PANELS) + 1 // +1 hides hairline gaps between panels

export function CabinStage({ children, label = 'Crew quarters' }: { children: ReactNode; label?: string }) {
  return (
    <div className="cabin-stage" style={{ '--cabin-h': `${HEIGHT}px`, '--cabin-r': `${RADIUS}px` } as CSSProperties}>
      <div className="cabin-scene">
        <div className="cabin-shell" aria-hidden>
          {Array.from({ length: PANELS }, (_, i) => (
            <div
              key={i}
              className={`cabin-panel${i === 3 ? ' cabin-panel-window' : ''}${i % 4 === 1 ? ' cabin-panel-light' : ''}`}
              style={{ width: PANEL_W, marginLeft: -PANEL_W / 2, transform: `rotateY(${(360 / PANELS) * i}deg) translateZ(${RADIUS}px)` }}
            />
          ))}
          <div className="cabin-floor" />
          <div className="cabin-ceiling" />
        </div>
        <div className="cabin-shadow" aria-hidden />
        <div className="cabin-body">{children}</div>
      </div>
      <p className="cabin-label" aria-hidden>{label}</p>
    </div>
  )
}
