/**
 * Tests for the visual controls of outputs.
 * Run with: cd webapp && npm test -- controls
 */
import { controls_defaults, updateQueryUrl } from '../controls';


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


describe('updateQueryUrl', () => {
  it("replaces the URL: toggles don't add history entries", () => {
    const history = { push: vi.fn(), replace: vi.fn() };
    updateQueryUrl(history, { show_reference: false });
    expect(history.push).not.toHaveBeenCalled();
    expect(history.replace).toHaveBeenCalledWith(expect.objectContaining({
      search: expect.stringContaining(encodeURIComponent(JSON.stringify({ show_reference: false }))),
    }));
  });
});
