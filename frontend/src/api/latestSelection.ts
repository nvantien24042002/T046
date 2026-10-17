import type { AnalysisSelection } from './analytics'
export type SelectionState<T> =
  | { kind: 'loading'; selection: AnalysisSelection }
  | { kind: 'ready'; selection: AnalysisSelection; data: T }
  | { kind: 'error'; selection: AnalysisSelection; error: unknown }

// Contract-independent coordinator for future dataset/config controls. Starting any
// selection or retry invalidates prior success/failure, even if transport ignores abort.
export function createLatestSelection<T>(publish: (state: SelectionState<T>) => void) {
  let generation = 0
  let active: AbortController | undefined
  return {
    async run(selection: AnalysisSelection, load: (selection: AnalysisSelection, signal: AbortSignal) => Promise<T>) {
      const current = ++generation
      active?.abort()
      const controller = new AbortController()
      active = controller
      const expected = { ...selection }
      publish({ kind: 'loading', selection: { ...expected } })
      try {
        const data = await load({ ...expected }, controller.signal)
        if (generation === current) publish({ kind: 'ready', selection: { ...expected }, data })
      } catch (error) {
        if (generation === current) publish({ kind: 'error', selection: { ...expected }, error })
      } finally {
        if (generation === current) active = undefined
      }
    },
    cancel() { generation++; active?.abort(); active = undefined },
  }
}
