// How the Runs page writes dates and values
import { DateTime } from "luxon";

// "3 hr. ago" (the API's dates are UTC)
export const relative = (date, style = 'short') => date ? DateTime.fromISO(date, { zone: 'utc' }).toRelative({ style }) : '';
// "Nov 17, 2026, 6:37 AM", in the reader's time zone
export const absolute = date => date ? DateTime.fromISO(date, { zone: 'utc' }).toLocal().toLocaleString(DateTime.DATETIME_MED) : '';
// a parameter's value as people type it in filters: text as is, the rest as JSON
export const value_text = value => typeof value === 'string' ? value : JSON.stringify(value);
// the label of a time window
export const window_label = days => days === 1 ? 'Last 24 hours' : days === 365 ? 'Last year' : `Last ${days} days`;
