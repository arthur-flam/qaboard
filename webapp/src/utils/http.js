import axios from "axios";

// A message for users about a failed request (or any error)
export function errorMessage(error) {
  const response = error?.response;
  const data = response?.data;
  if (data?.error) return data.error;
  if (response) {
    const status = response.status;
    if (status === 504)
      return "The server took too long to answer. It may still be doing what you asked: refresh in a minute before trying again.";
    if (status === 502 || status === 503)
      return `The server is unavailable (${status}), maybe it's restarting. Try again in a minute.`;
    if (status === 401) return "You need to log in to do this.";
    if (status === 403) return "You don't have the permission to do this.";
    // Not an HTML error page (e.g. from nginx)
    if (typeof data === "string" && data.length > 0 && data.length < 500 && !data.trimStart().startsWith("<"))
      return `Error ${status}: ${data}`;
    return `Error ${status}${response.statusText ? ` (${response.statusText})` : ""}`;
  }
  if (error?.request) return "Could not reach the server. Check your connection, or try again in a minute.";
  return error?.message ?? String(error);
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Waits for a background job started by the server (endpoints that answer with a "job_id"). Returns its result.
// `onStillPending` is called once if the job didn't start after `pendingNotice` ms (e.g. the server's worker is down).
export async function waitForJob(job_id, { interval = 2000, timeout = 30 * 60 * 1000, pendingNotice = 60 * 1000, onStillPending } = {}) {
  const start = Date.now();
  let notified = false;
  for (;;) {
    let job;
    try {
      ({ data: job } = await axios.get(`/api/v1/jobs/${job_id}/`));
    } catch (error) {
      // We keep polling when the server is briefly unavailable (e.g. during a deploy)
      const status = error.response?.status;
      const transient = error.isAxiosError && (!error.response || [502, 503, 504].includes(status));
      if (!transient) throw error;
    }
    if (job !== undefined) {
      if (typeof job !== "object" || job === null) throw new Error("Unexpected answer from the server.");
      if (job.state === "SUCCESS") return job.result;
      if (job.state === "FAILURE") throw new Error(job.error ?? "The job failed.");
      if (!["PENDING", "STARTED", "RETRY"].includes(job.state)) throw new Error(`The job ended as ${job.state}.`);
      if (job.state === "PENDING" && !notified && Date.now() - start > pendingNotice) {
        notified = true;
        onStillPending?.();
      }
    }
    if (Date.now() - start > timeout)
      throw new Error("The job is still not done. Refresh later to see its results.");
    await sleep(interval);
  }
}

// Runs outputs again: POST /api/v1/batch/redo/ or /api/v1/output/redo/<id>/
// The server submits the runs in the background, we wait until it's done.
// Returns {started: number of runs submitted (null if unknown), failed: [{id, error}]}
export async function redo(url, params, { onQueued, onStillPending } = {}) {
  const { data } = await axios.post(url, params);
  // Without a job, the runs were already submitted (or older servers that submit them in the request)
  if (!data.job_id) return { started: data.outputs ?? null, failed: [] };
  onQueued?.(data);
  const result = await waitForJob(data.job_id, { onStillPending });
  return { started: result?.started ?? 0, failed: result?.failed ?? [] };
}

export const STILL_PENDING_MESSAGE = "The runs are still waiting to be submitted. If it lasts, tell your QA-Board admins: the server's worker may be down.";

// A toast for the result of `redo`
export function redoToast({ started, failed }) {
  if (started === null) return { message: "Started the runs again.", intent: "success" };
  const total = started + failed.length;
  if (total === 0) return { message: "There was nothing to redo.", intent: "primary" };
  if (failed.length === 0) return { message: `Started ${started} run${started > 1 ? "s" : ""} again.`, intent: "success" };
  return {
    message: `${failed.length} of ${total} run${total > 1 ? "s" : ""} failed to start: ${failed[0].error}`,
    intent: started > 0 ? "warning" : "danger",
    timeout: 15000,
  };
}
