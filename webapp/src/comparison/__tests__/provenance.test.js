import { describe, expect, it } from 'vitest';
import { distinct_names, provenance } from '../provenance';

const new_commit = { id: 'n000000000', branch: 'feature/denoise' };
const head = { id: 'h000000000', branch: 'develop' };
const context = commit => ({ commit, new_commit, new_batch: 'default', new_filter: '', reference_branch: 'develop', head });

describe('provenance', () => {
  it('says milestones first', () => {
    expect(provenance({ milestone: {} }, context(head)).kind).toBe('milestone');
  });
  it('waits for the commit', () => {
    expect(provenance({}, context(undefined)).kind).toBe('loading');
  });
  it('knows the latest commit of the reference branch', () => {
    expect(provenance({ batch: 'default' }, context(head))).toEqual({ kind: 'latest', name: 'develop', branch: 'develop' });
  });
  it('knows older commits of the reference branch', () => {
    expect(provenance({}, context({ id: 'abcdef1234', branch: 'develop' }))).toMatchObject({ kind: 'behind', name: 'abcdef1' });
  });
  it('knows commits of the same branch as the new one', () => {
    expect(provenance({}, context({ id: 'e000000000', branch: 'feature/denoise' })).kind).toBe('earlier');
  });
  it('names the new commit with another batch after the batch', () => {
    expect(provenance({ batch: 'nightly' }, context(new_commit))).toEqual({ kind: 'same-commit', name: 'nightly' });
    expect(provenance({ batch: 'default' }, context(new_commit)).kind).toBe('same-runs');
  });
  it('falls back to the commit', () => {
    expect(provenance({}, context({ id: 'c000000000', branch: 'other' })).kind).toBe('commit');
  });
});

describe('distinct_names', () => {
  it('tells references with the same name apart by their batch', () => {
    expect(distinct_names(['develop', 'develop', 'abc'], [{ batch: 'default' }, { batch: 'nightly' }, {}])).toEqual(['develop', 'develop · nightly', 'abc']);
  });
  it('never calls a reference like the new results', () => {
    expect(distinct_names(['new', 'develop'], [{}, {}], ['new'])).toEqual([undefined, 'develop']);
  });
  it('numbers exact copies', () => {
    expect(distinct_names(['develop', 'develop'], [{}, {}])).toEqual(['develop', 'develop (2)']);
  });
});
