/**
 * Tests for the visual controls of outputs.
 * Run with: cd webapp && npm test -- controls
 */
import { controls_defaults, save_controls } from '../controls';
import { history } from '../../router';
import { parse_query } from '../../selection';


describe('controls_defaults', () => {
  const config = {
    outputs: {
      controls: [{ name: 'show_reference', default: true }],
      visualizations: [{ name: 'debug', default_hidden: true }, { name: 'output' }],
    },
  };

  it('uses the defaults from the configuration', () => {
    expect(controls_defaults(config, '')).toMatchObject({ show_reference: true, show: { debug: false }, dynamic_options: {} });
  });

  it('uses the controls from the URL', () => {
    const search = `?controls=${encodeURIComponent(JSON.stringify({ show_reference: false }))}`;
    expect(controls_defaults(config, search).show_reference).toBe(false);
    expect(controls_defaults(config, '?controls=not-json').show_reference).toBe(true);
  });

  it('works without configuration', () => {
    expect(controls_defaults(undefined, '')).toMatchObject({ show: {}, dynamic_options: {} });
  });
});


describe('save_controls', () => {
  it("replaces the URL: toggles don't add history entries", () => {
    const push = vi.spyOn(history, 'push');
    const replace = vi.spyOn(history, 'replace');
    save_controls({ show_reference: false });
    expect(push).not.toHaveBeenCalled();
    expect(parse_query(window.location.search).controls).toBe(JSON.stringify({ show_reference: false }));
    expect(replace).toHaveBeenCalledTimes(1);
  });
});
