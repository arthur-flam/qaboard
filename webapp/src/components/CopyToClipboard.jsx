// Copies `text` when its child is clicked.
// We don't use navigator.clipboard: it needs a secure context, and QA-Board is often served over plain http.
import { cloneElement } from "react";
import copy from "copy-to-clipboard";

export const CopyToClipboard = ({ text, onCopy, children }) => cloneElement(children, {
  onClick: event => {
    const result = copy(text);
    onCopy?.(text, result);
    children.props.onClick?.(event);
  },
});
