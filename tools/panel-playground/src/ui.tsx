// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0

// The few controls the playground chrome needs, on native elements, so the tool has no component library to track.
import { useEffect, useRef, type MouseEvent, type ReactNode } from 'react'

export function Slider({ min, max, step, value, onValueChange, ...rest }: {
  min: number; max: number; step: number; value: number[]; onValueChange: (v: number[]) => void; 'aria-label'?: string
}) {
  const pct = ((value[0] - min) / (max - min)) * 100
  return (
    <input type="range" className="pg-range" min={min} max={max} step={step} value={value[0]} aria-label={rest['aria-label']}
      style={{ ['--fill' as string]: pct + '%' }} onChange={(e) => onValueChange([+e.target.value])} />
  )
}

export function Switch({ checked, onCheckedChange, id }: { checked: boolean; onCheckedChange: (v: boolean) => void; id?: string }) {
  return (
    <button type="button" role="switch" id={id} aria-checked={checked} className={'pg-switch' + (checked ? ' on' : '')}
      onClick={() => onCheckedChange(!checked)}><span /></button>
  )
}

// A modal on <dialog>: Escape and a click on the backdrop close it.
function useModal(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  const props = {
    ref,
    onClose,
    onClick: (e: MouseEvent<HTMLDialogElement>) => { if (e.target === e.currentTarget) onClose() },
  }
  return props
}

export function Modal({ open, onClose, title, description, children }: {
  open: boolean; onClose: () => void; title: string; description?: string; children: ReactNode
}) {
  const props = useModal(open, onClose)
  return (
    <dialog className="pg-modal" {...props}>
      <div className="pg-modal-body">
        <div className="pg-modal-head">
          <h2>{title}</h2>
          <button className="pg-btn" onClick={onClose}>Close</button>
        </div>
        {description && <p className="pg-note">{description}</p>}
        {open && children}
      </div>
    </dialog>
  )
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const props = useModal(open, onClose)
  return (
    <dialog className="pg-drawer" {...props}>
      <div className="pg-modal-head pg-drawer-head">
        <h2>{title}</h2>
        <button className="pg-btn" onClick={onClose}>Done</button>
      </div>
      {open && children}
    </dialog>
  )
}
