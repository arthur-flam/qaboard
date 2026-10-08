// The API returns errors as {error: "..."}, sometimes as a plain string or an HTML page.
// Returns something readable for users, never "[object Object]" or a JSON dump of the request.
export const errorMessage = error => {
  const data = error?.response?.data
  if (data?.error) {
    return typeof data.error === 'string' ? data.error : JSON.stringify(data.error)
  }
  if (Array.isArray(data?.errors) && data.errors.length > 0) {
    return data.errors.join(' ')
  }
  if (typeof data === 'string' && data.length > 0 && data.length < 500 && !data.trimStart().startsWith('<')) {
    return data
  }
  if (error?.response?.status) {
    return `${error.message ?? 'Request failed'} (${error.response.status} ${error.response.statusText ?? ''})`.trim()
  }
  return error?.message ?? String(error)
}

// 409: the commit's artifacts are missing. QA-Board may have asked the CI to recreate them.
export const isArtifactsError = error => error?.response?.status === 409 && !!error?.response?.data?.artifacts
