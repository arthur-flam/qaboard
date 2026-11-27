import fs from 'fs';
import path from 'path';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { TranscriptReport } from '../TranscriptReport';

const fixture = name => fs.readFileSync(path.join(import.meta.dirname, '../../../../../tests/fixtures/transcripts', name), 'utf8');
const labels = { new: 'my-branch', reference: 'main' };
const conversation = (...messages) => JSON.stringify({ messages });

describe('TranscriptReport', () => {
  it('shows an Inspect sample against the reference: scores, the messages that changed, the others once', () => {
    const { container } = render(<TranscriptReport text={fixture('inspect-new.json')} ref_text={fixture('inspect-reference.json')} labels={labels}/>);
    expect(screen.getByText('mockllm/model')).toBeInTheDocument();
    // the first sample: only the system prompt changed
    expect(screen.getByText('1 changed')).toBeInTheDocument();
    expect(container.querySelectorAll('.qa-transcript-row-pair')).toHaveLength(1);
    expect(container.querySelectorAll('.qa-transcript-row-same')).toHaveLength(4);

    // the second failed, and passed in the reference
    fireEvent.change(screen.getByLabelText('Sample'), { target: { value: 'battery-r9#1' } });
    expect(screen.getByText('2 changed')).toBeInTheDocument();
    const score = container.querySelector('.qa-transcript-score-flipped');
    expect(within(score).getByText('Failed')).toBeInTheDocument();
    expect(within(score).getByText('Passed')).toBeInTheDocument();
    expect(screen.getByText(/unknown robot r9/)).toBeInTheDocument();
    // the words that differ in the answers
    expect([...container.querySelectorAll('.qa-transcript-added')].map(e => e.textContent).join('|')).toContain('at 42%');
    expect([...container.querySelectorAll('.qa-transcript-removed')].map(e => e.textContent).join('|')).toContain('unknown');
  });

  it('folds identical messages, goes through the differences, and shows one side', () => {
    const same = Array.from({ length: 6 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `turn ${i}` }));
    const { container } = render(<TranscriptReport
      text={conversation(...same, { role: 'assistant', content: 'the new answer' })}
      ref_text={conversation(...same, { role: 'assistant', content: 'the old answer' })} labels={labels}
    />);
    fireEvent.click(screen.getByRole('button', { name: 'Changes only' }));
    expect(screen.getByText('5 identical messages')).toBeInTheDocument();
    expect(screen.queryByText('turn 0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('5 identical messages'));
    expect(screen.getByText('turn 0')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'First difference' }));
    expect(screen.getByRole('button', { name: 'Difference 1 of 1' })).toBeInTheDocument();
    expect(container.querySelector('.qa-transcript-row-current')).toHaveTextContent('the new answer');

    // keys work from anywhere in the viewer
    fireEvent.keyDown(screen.getByRole('button', { name: 'Changes only' }), { key: 'c' });
    expect(screen.getByRole('button', { name: 'Changes only' })).not.toHaveClass('bp6-active');

    fireEvent.click(screen.getByRole('button', { name: 'main' }));
    expect(screen.getByText('the old answer')).toBeInTheDocument();
    expect(screen.queryByText(/the new answer/)).not.toBeInTheDocument();
  });

  it('searches, hides roles, and opens what is folded', () => {
    render(<TranscriptReport text={conversation(
      { role: 'system', content: 'You are careful.' },
      { role: 'user', content: 'Reboot r2' },
      { role: 'assistant', content: [{ type: 'reasoning', reasoning: 'Hidden thoughts' }, { type: 'text', text: 'Rebooting' }], tool_calls: [{ id: '1', function: 'reboot', arguments: { robot: 'r2' } }] },
      { role: 'tool', tool_call_id: '1', content: 'ok' },
    )}/>);
    // the system prompt and the reasoning start folded
    expect(screen.queryByText('You are careful.')).not.toBeInTheDocument();
    expect(screen.queryByText('Hidden thoughts')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open all' }));
    expect(screen.getByText('You are careful.')).toBeInTheDocument();
    expect(screen.getByText('Hidden thoughts')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search the messages'), { target: { value: 'robot' } });
    expect(screen.getByText('1 of 4')).toBeInTheDocument();
    expect(screen.queryByText('Reboot r2')).not.toBeInTheDocument();
    expect(screen.getByText('robot', { selector: 'mark' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search the messages'), { target: { value: '' } });

    fireEvent.click(screen.getByTitle('Hide Tool messages'));
    expect(screen.queryByText('ok')).not.toBeInTheDocument();
  });

  it('hands what it cannot read to the fallback, or says why', () => {
    const { rerender } = render(<TranscriptReport text='{"benchmarks": []}' path="transcript.json" fallback={message => <p>as text: {message}</p>}/>);
    expect(screen.getByText(/as text: no messages/)).toBeInTheDocument();
    rerender(<TranscriptReport text="nope" path="transcript.json"/>);
    expect(screen.getByText(/Can't read/)).toHaveTextContent('transcript.json');
  });

  it('shows the conversation alone when the reference has no such sample or file', () => {
    const { container } = render(<TranscriptReport text={fixture('inspect-new.json')} ref_text="broken" labels={labels}/>);
    expect(container.querySelectorAll('.qa-transcript-row-single')).toHaveLength(5);
    expect(screen.queryByRole('button', { name: 'Changes only' })).not.toBeInTheDocument();
  });
});
