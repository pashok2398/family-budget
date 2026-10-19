import { describe, expect, it } from 'vitest';
import { requestKey } from './ids';

describe('requestKey', () => {
  it('is 32 hex characters and unique', () => {
    const keys = new Set(Array.from({ length: 50 }, requestKey));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(/^[0-9a-f]{32}$/);
  });
});
