// Sidebar component test — the Agent Status widget derives from the shared
// AGENT_ORDER constant, so it must render every pipeline stage the backend
// runs (all six, FRAUD_AGENT included). Guards against the widget drifting
// back to a hardcoded five-stage list — the exact defect the surface
// inventory flagged (a second stage list that silently under-reported).
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AGENT_ORDER } from '../features/claims/constants';

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u_test', role: 'adjuster' } }),
}));

import Sidebar from './Sidebar';

describe('Sidebar', () => {
  it('renders the Agent Status widget from the shared six-stage pipeline constant', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );

    expect(screen.getByText('Agent Status')).toBeInTheDocument();
    // Exactly the shared constant's stages render — no more, no fewer.
    const stageLabels = screen.getAllByText(
      /^(INTAKE|POLICY|DOCUMENT|FRAUD|ELIGIBILITY|DECISION)$/
    );
    expect(stageLabels).toHaveLength(AGENT_ORDER.length);
    for (const agent of AGENT_ORDER) {
      expect(screen.getByText(agent.replace(/_AGENT$/, ''))).toBeInTheDocument();
    }
  });
});
