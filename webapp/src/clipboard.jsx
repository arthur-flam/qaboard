// Copying to the clipboard, with a toast.
// We don't use navigator.clipboard: it needs a secure context, and QA-Board is often served over plain http.
import { cloneElement } from "react";
import { Intent } from "@blueprintjs/core";
import copy from "copy-to-clipboard";

import { toaster } from "./toaster";
import { linux_to_windows } from "./utils";

export const copyText = (text, message = 'Copied') => {
  if (copy(text))
    toaster.show({ message, icon: 'tick', intent: Intent.SUCCESS, timeout: 1500 });
  else
    toaster.show({ message: 'Could not copy', intent: Intent.DANGER });
};

// Paths to outputs and artifacts, as users can open them on linux or windows
export const copyPath = (url, os) => os === 'windows'
  ? copyText(linux_to_windows(url), "Windows path copied to clipboard!")
  : copyText(decodeURI(url).slice(2), "Linux path copied to clipboard!");

// Copies `text` when its child is clicked. Other props are given to the child.
export const CopyToClipboard = ({ text, onCopy, children, ...props }) => cloneElement(children, {
  ...props,
  onClick: event => {
    const result = copy(text);
    onCopy?.(text, result);
    children.props.onClick?.(event);
  },
});
