import { describe, it, expect } from "vitest";

import { output_rois } from "../crops";
import { blobToRoi } from "../AutoCrops";


describe("output_rois", () => {
  it("lists unique ROIs from the input metadata and the configurations, without changing the output", () => {
    const roi = { x: 0, y: 0, w: 10, h: 10, label: "a" };
    const output = Object.freeze({
      configurations: Object.freeze(["base", Object.freeze({ roi: Object.freeze([Object.freeze({ ...roi }), Object.freeze({ x: 5, y: 5, w: 1, h: 1, label: "b" })]) })]),
      test_input_metadata: Object.freeze({ roi: Object.freeze([Object.freeze(roi)]), width: 100, height: 50 }),
    });
    const rois = output_rois(output);
    expect(rois.map(r => r.label)).toEqual(["a", "b", "Full Image"]);
    expect(rois[0]).toMatchObject({ ...roi, image_width: 100, image_height: 50 });
    expect(roi).not.toHaveProperty("key");
  });

  it("has nothing to show without ROIs", () => {
    expect(output_rois({ configurations: [] })).toEqual([]);
  });
});


describe("blobToRoi", () => {
  it("clips the regions to the image", () => {
    const roi = blobToRoi({ x: -2, y: 95, r: 10, diff: 0.5 }, 100, 100);
    expect(roi).toMatchObject({ x: 0, y: 95, w: 8, h: 5, diff: 0.5 });
    expect(roi.color.formatHex()).toMatch(/^#[0-9a-f]{6}$/);
  });
});
