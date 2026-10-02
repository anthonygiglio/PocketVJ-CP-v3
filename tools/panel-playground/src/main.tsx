// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './chrome.css'
import App from './App.tsx'

// Chrome faces; the panel's own optional faces load on demand from settings.ts.
const l = document.createElement('link')
l.rel = 'stylesheet'
l.href = 'https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap'
document.head.appendChild(l)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
