import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StatusLabel } from '@/components/status'

describe('StatusLabel', () => {
  it('always renders a text label (colour is never the only signal)', () => {
    render(<StatusLabel status="COMPLETED_WITH_ERRORS" />)
    expect(screen.getByText('Completed with errors')).toBeInTheDocument()
  })

  it('highlights failures with the signal colour', () => {
    render(<StatusLabel status="FAILED" />)
    expect(screen.getByText('Failed')).toHaveClass('text-signal')
  })

  it('renders neutral statuses without the signal colour', () => {
    render(<StatusLabel status="MEASURED" />)
    expect(screen.getByText('Measured')).not.toHaveClass('text-signal')
  })
})
