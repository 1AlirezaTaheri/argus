import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
// Imported for its side effect: it publishes window.__argusLangCheck so the
// parity check can run against the shipped bundle. Without this import the
// bundler tree-shakes the module away and the check reports, correctly but
// uselessly, that no check exists.
import './lib/lang-check'
import '@fontsource-variable/inter'

import './index.css'

const container = document.getElementById('root')

if (!container) {
  throw new Error('#root not found in index.html')
}

// StrictMode double-invokes effects in development; that is intentional and
// catches the races this panel is prone to (query cancellation on unmount).
ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)