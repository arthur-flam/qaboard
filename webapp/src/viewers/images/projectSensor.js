// The sensor of a project's raw images, saved for everyone by its developers and owners (backend/backend/viewer_settings.py),
// for the image viewer's analysis: its `savedSensor` props. The images' metadata comes first, and what users set in
// their browser (prefs' imageSensor) wins over both, for them.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { DateTime } from 'luxon';

import { errorMessage, http } from '../../api/http';
import { projectQuery } from '../../api/queries';
import { projectAccessQuery } from '../../access/api';

const KINDS = ['mosaic', 'mono'];

// The server's (snake_case, with who saved it), as the viewer's SensorSetting. null: nothing saved
export const fromServer = sensor => {
  if (!sensor || typeof sensor !== 'object') return null;
  const setting = {
    ...(KINDS.includes(sensor.kind) ? { kind: sensor.kind } : {}),
    ...(sensor.pattern ? { pattern: sensor.pattern } : {}),
    ...(sensor.block ? { block: sensor.block } : {}),
    ...(sensor.offset ? { offset: { x: sensor.offset.x ?? 0, y: sensor.offset.y ?? 0 } } : {}),
    ...(Number.isFinite(sensor.black_level) ? { blackLevel: sensor.black_level } : {}),
  };
  return Object.keys(setting).length ? setting : null;
};

// The viewer's SensorSetting, as the server saves it (without `grouping`: a way to look). null forgets it
export const toServer = setting => {
  if (!setting) return null;
  return {
    ...(KINDS.includes(setting.kind) ? { kind: setting.kind } : {}),
    ...(setting.pattern ? { pattern: setting.pattern } : {}),
    ...(setting.block ? { block: setting.block } : {}),
    ...(setting.offset ? { offset: { x: setting.offset.x ?? 0, y: setting.offset.y ?? 0 } } : {}),
    ...(Number.isFinite(setting.blackLevel) ? { black_level: setting.blackLevel } : {}),
  };
};

// e.g. "by Bob B, Oct 27, 2026"
export const savedNote = sensor => {
  if (!sensor) return undefined;
  const by = sensor.updated_by?.full_name || sensor.updated_by?.user_name;
  const at = sensor.updated_at ? DateTime.fromISO(sensor.updated_at) : null;
  const when = at?.isValid ? at.toLocaleString(DateTime.DATE_MED) : null;
  return [by && `by ${by}`, when].filter(Boolean).join(', ') || undefined;
};

// Saves it for the project, or forgets it (null). The project's cached data gets the project's new settings.
export const saveProjectSensor = async (queryClient, project, setting) => {
  let data;
  try {
    ({ data } = await http.post('/api/v1/project/settings', { project, sensor: toServer(setting) }));
  } catch (error) {
    // the dialog shows it
    throw new Error(errorMessage(error));
  }
  queryClient.setQueryData(projectQuery(project).queryKey, previous => previous && {
    ...previous,
    data: { ...previous.data, settings: data?.settings ?? {} },
  });
  return data;
};

const SIGN_IN = 'Sign in (in the sidebar) to save it for everyone who opens the project';
const ROLE = "The project's developers and owners can save it for everyone who opens the project";

// The viewer's savedSensor props for the project: what's saved, and saving it if users may (developers and owners)
export const useProjectSensor = (project, signed_in) => {
  const queryClient = useQueryClient();
  const { data: sensor } = useQuery({ ...projectQuery(project), select: data => data?.data?.settings?.sensor ?? null });
  // who may save: users rarely change roles, and the server checks anyway
  const { data: access } = useQuery({ ...projectAccessQuery(project), enabled: !!project && signed_in, staleTime: 5 * 60 * 1000 });
  const can_save = signed_in && !!access?.can_save_viewer_settings;
  return {
    savedSensor: fromServer(sensor),
    savedSensorFor: 'project',
    savedSensorNote: savedNote(sensor),
    onSaveSensor: can_save ? setting => saveProjectSensor(queryClient, project, setting) : undefined,
    saveSensorUnavailable: !signed_in ? SIGN_IN : access && !can_save ? ROLE : undefined,
  };
};
