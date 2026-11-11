// In the run's menu: opens the reference picker on the run's card (ReferenceMatchTag), when there are reference runs
import { MenuItem } from "@blueprintjs/core";

import { useComparison } from "../hooks";
import { useReferencePicker } from "./pickerStore";

export const PickReferenceMenuItem = ({ output }) => {
  const comparison = useComparison();
  const open = useReferencePicker(state => state.open);
  if (!comparison?.ref_lookup?.outputs?.length || output.is_pending) return null;
  return <MenuItem
    icon="changes"
    text="Compare with another reference run…"
    htmlTitle="Pick the reference run this run compares with, or a rule for all the runs"
    onClick={() => open(output.id)}
  />;
};
