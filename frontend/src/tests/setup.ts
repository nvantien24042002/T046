import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
// Legacy fixture harnesses are opt-in inside tests, never a production default.
vi.stubEnv('VITE_DATA_MODE', 'mock')
afterEach(cleanup)
