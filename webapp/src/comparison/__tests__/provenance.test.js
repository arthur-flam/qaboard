import { describe, expect, it } from 'vitest';
import { distinct_names, provenance } from '../provenance';

const new_commit = { id: 'n000000000', branch: 'feature/denoise', authored_datetime: '2026-09-02T00:00:00Z' };
const head = { id: 'h000000000', branch: 'develop' };
const context = (commit, more) => ({ commit, new_commit, new_project: 'p', new_batch: 'default', new_filter: '', reference_branch: 'develop', head, ...more });

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
  it('waits for the latest commit of the reference branch to tell if it is it', () => {
    expect(provenance({}, context({ id: 'abcdef1234', branch: 'develop' }, { head: undefined, head_pending: true })).kind).toBe('loading');
    expect(provenance({}, context({ id: 'abcdef1234', branch: 'develop' }, { head: undefined })).kind).toBe('on-branch');
  });
  it('knows commits of the same branch as the new one, before or after it', () => {
    expect(provenance({}, context({ id: 'e000000000', branch: 'feature/denoise', authored_datetime: '2026-09-01T00:00:00Z' })).kind).toBe('earlier');
    expect(provenance({}, context({ id: 'e000000000', branch: 'feature/denoise', authored_datetime: '2026-09-03T00:00:00Z' })).kind).toBe('later');
  });
  it('says commits could not load', () => {
    expect(provenance({ error: '404 Not Found', commit_id: 'abcdef1234' }, context(undefined))).toEqual({ kind: 'error', name: 'abcdef1' });
  });
  it('the same commit in another project is not the same runs', () => {
    expect(provenance({ project: 'other', batch: 'default' }, context(new_commit)).kind).not.toBe('same-runs');
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
