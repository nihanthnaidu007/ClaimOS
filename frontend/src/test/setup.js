// Vitest setup — loaded once per test file via vite.config.js `test.setupFiles`.
// Extends Testing Library matchers and spins up the MSW request interceptor so
// component-level API calls never touch the network.
import { beforeAll, afterEach, afterAll } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { server } from './handlers';

// 'error' keeps handlers honest: any request without a matching handler fails
// the test instead of silently falling through to the real network.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
